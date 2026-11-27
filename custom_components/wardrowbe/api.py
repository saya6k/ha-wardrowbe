"""HTTP client for the Wardrowbe REST API.

Authentication is two-legged:

* An external ``TokenProvider`` produces the payload accepted by Wardrowbe's
  ``POST /api/v1/auth/sync`` endpoint — either an OIDC ``id_token`` or, in
  dev-mode installs, an ``external_id``.
* ``WardrowbeClient`` exchanges that payload for a Wardrowbe-issued JWT,
  caches it, and re-syncs on expiry or 401.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import aiohttp
from aiohttp import ClientSession

from .const import (
    API_ANALYTICS,
    API_AUTH_CONFIG,
    API_AUTH_SESSION,
    API_AUTH_SYNC,
    API_CAPABILITIES,
    API_HEALTH,
    API_ITEM_TYPES,
    API_ITEMS,
    API_NOTIFICATIONS_HISTORY,
    API_NOTIFICATIONS_SETTINGS,
    API_OUTFIT_SUGGEST,
    API_OUTFIT_SUGGESTIONS,
    API_OUTFITS,
    API_USER_PROFILE,
    DEFAULT_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

_JWT_REFRESH_LEEWAY = timedelta(hours=1)
_DEFAULT_JWT_TTL_SECONDS = 6 * 24 * 3600  # 6 days; Wardrowbe defaults to 7
# Images are proxied through HA, so cap what one request can pull.
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class WardrowbeApiError(Exception):
    """Generic Wardrowbe API error.

    ``status`` carries the HTTP status code when the error originates from a
    non-2xx response, so callers can react to specific codes (e.g. a 503 from
    ``/outfits/suggest`` when the server has internal AI disabled).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class WardrowbeAuthError(WardrowbeApiError):
    """Authentication failed and could not be recovered."""


class TokenProvider:
    """Produces the payload expected by Wardrowbe's /auth/sync endpoint."""

    async def async_get_sync_payload(self) -> dict[str, Any]:
        raise NotImplementedError


class DevTokenProvider(TokenProvider):
    """Sends external_id + synthesised email/display_name for dev-mode installs."""

    def __init__(
        self,
        external_id: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> None:
        self._external_id = external_id
        self._email = email or f"{external_id}@local"
        self._display_name = display_name or external_id

    async def async_get_sync_payload(self) -> dict[str, Any]:
        return {
            "external_id": self._external_id,
            "email": self._email,
            "display_name": self._display_name,
        }


class StaticIdTokenProvider(TokenProvider):
    """Wraps a fixed ``id_token`` — used during the config flow's first sync."""

    def __init__(self, id_token: str) -> None:
        self._id_token = id_token

    async def async_get_sync_payload(self) -> dict[str, Any]:
        return build_oidc_sync_payload(self._id_token)


def build_oidc_sync_payload(id_token: str) -> dict[str, Any]:
    """Decode an OIDC id_token and shape it for Wardrowbe's /auth/sync.

    Wardrowbe's endpoint expects the canonical OIDC claims spelled out as
    body fields (``external_id``, ``email``, ``display_name``); it does not
    decode the raw JWT itself. The id_token is sent alongside so that the
    server can still verify it if it wants.

    The signature is not verified here — we trust HA's OAuth flow to have
    received this token over an authenticated TLS channel from the
    pre-registered token endpoint.
    """
    claims = _decode_jwt_payload(id_token)
    sub = claims.get("sub")
    if not sub:
        raise WardrowbeAuthError("id_token is missing required `sub` claim")
    email = claims.get("email")
    display_name = (
        claims.get("name")
        or claims.get("preferred_username")
        or claims.get("nickname")
        or email
        or str(sub)
    )
    payload: dict[str, Any] = {
        "external_id": str(sub),
        "email": email or "",
        "display_name": display_name,
        "id_token": id_token,
    }
    return payload


def _decode_jwt_payload(id_token: str) -> dict[str, Any]:
    """Return the claims dict from a JWT without verifying the signature."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise WardrowbeAuthError("Malformed id_token (expected 3 dot-separated parts)")
    encoded = parts[1]
    pad = "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded + pad)
        decoded = json.loads(raw)
    except (ValueError, binascii.Error, json.JSONDecodeError) as err:
        raise WardrowbeAuthError(f"Could not decode id_token payload: {err}") from err
    if not isinstance(decoded, dict):
        raise WardrowbeAuthError("id_token payload is not a JSON object")
    return decoded


class WardrowbeClient:
    """Async client for Wardrowbe's REST API."""

    def __init__(  # noqa: PLR0913 — keyword-only options
        self,
        session: ClientSession,
        host: str,
        token_provider: TokenProvider,
        *,
        verify_ssl: bool = True,
        entry_id: str | None = None,
        sign_path: Callable[[str], str] | None = None,
    ) -> None:
        self._session = session
        self._host = host.rstrip("/")
        self._token_provider = token_provider
        self._verify_ssl = verify_ssl
        self._entry_id = entry_id
        self._sign_path = sign_path
        self._jwt: str | None = None
        # Bumped on every successful sync; lets a 401'd request tell whether
        # someone else already re-synced since it fetched its JWT.
        self._jwt_generation = 0
        self._jwt_refresh_at: datetime | None = None
        self._sync_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        return self._host

    # ---- unauthenticated probes ----

    async def async_health(self) -> bool:
        try:
            async with self._session.get(
                self._url(API_HEALTH),
                ssl=self._verify_ssl,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                return resp.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False

    async def async_capabilities(self) -> dict[str, Any] | None:
        """Fetch the server's effective AI capabilities (public, no auth).

        Returns ``None`` on servers predating the endpoint (Wardrowbe < 1.4.0)
        or on any transport/HTTP error, so callers treat an unknown state as
        "assume enabled" rather than blocking actions.
        """
        try:
            async with self._session.get(
                self._url(API_CAPABILITIES),
                ssl=self._verify_ssl,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError):
            return None
        return data if isinstance(data, dict) else None

    async def async_auth_config(self) -> dict[str, Any]:
        async with self._session.get(
            self._url(API_AUTH_CONFIG),
            ssl=self._verify_ssl,
            timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ---- authenticated reads ----

    async def async_session_info(self) -> dict[str, Any]:
        return await self._request("GET", API_AUTH_SESSION)

    async def async_analytics(self) -> dict[str, Any]:
        data = await self._request("GET", API_ANALYTICS)
        if isinstance(data, dict):
            _resolve_image_urls(data, self._host, self._entry_id, self._sign_path)
        return data

    async def async_recent_outfits(self, limit: int = 20) -> list[dict[str, Any]]:
        data = await self._request("GET", API_OUTFITS, params={"limit": limit})
        outfits = _coerce_list(data)
        _resolve_image_urls(outfits, self._host, self._entry_id, self._sign_path)
        return outfits

    async def async_recent_notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", API_NOTIFICATIONS_HISTORY, params={"limit": limit}
        )
        return _coerce_list(data)

    async def async_items_needing_wash(self, limit: int = 100) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            API_ITEMS,
            params={
                "needs_wash": "true",
                "is_archived": "false",
                "page_size": limit,
            },
        )
        items = _coerce_list(data)
        _resolve_image_urls(items, self._host, self._entry_id, self._sign_path)
        return items

    async def async_list_items(  # noqa: PLR0913 — one kwarg per server filter
        self,
        *,
        item_type: str | None = None,
        subtype: str | None = None,
        favorite: bool | None = None,
        search: str | None = None,
        include_needs_wash: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Active (non-archived) items; clean ones only unless asked otherwise."""
        params: dict[str, Any] = {"is_archived": "false", "page_size": limit}
        if not include_needs_wash:
            params["needs_wash"] = "false"
        if item_type:
            params["type"] = item_type
        if subtype:
            params["subtype"] = subtype
        if favorite is not None:
            params["favorite"] = "true" if favorite else "false"
        if search:
            params["search"] = search
        items = _coerce_list(await self._request("GET", API_ITEMS, params=params))
        _resolve_image_urls(items, self._host, self._entry_id, self._sign_path)
        return items

    async def async_item_types(self) -> list[dict[str, Any]]:
        """The user's item types with counts, e.g. [{"type": "shirt", "count": 12}]."""
        return _coerce_list(await self._request("GET", API_ITEM_TYPES))

    async def async_user_profile(self) -> dict[str, Any]:
        return await self._request("GET", API_USER_PROFILE)

    # ---- authenticated writes ----

    async def async_suggest_outfit(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = await self._request("POST", API_OUTFIT_SUGGEST, json=payload)
        if isinstance(result, dict):
            _resolve_image_urls(result, self._host, self._entry_id, self._sign_path)
        return result

    async def async_create_outfit_suggestion(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        result = await self._request("POST", API_OUTFIT_SUGGESTIONS, json=payload)
        if isinstance(result, dict):
            _resolve_image_urls(result, self._host, self._entry_id, self._sign_path)
        return result

    async def async_outfit_action(self, outfit_id: str, action: str) -> dict[str, Any]:
        if action not in {"accept", "reject", "skip"}:
            raise ValueError(f"Unsupported outfit action: {action}")
        return await self._request("POST", f"{API_OUTFITS}/{_seg(outfit_id)}/{action}")

    async def async_post_outfit_feedback(
        self, outfit_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{API_OUTFITS}/{_seg(outfit_id)}/feedback", json=payload
        )

    async def async_log_wear(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"{API_ITEMS}/{_seg(item_id)}/wear", json=payload)

    async def async_log_wash(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"{API_ITEMS}/{_seg(item_id)}/wash", json=payload)

    async def async_archive_item(
        self, item_id: str, reason: str | None
    ) -> dict[str, Any]:
        body = {"reason": reason} if reason else {}
        return await self._request("POST", f"{API_ITEMS}/{_seg(item_id)}/archive", json=body)

    async def async_restore_item(self, item_id: str) -> dict[str, Any]:
        return await self._request("POST", f"{API_ITEMS}/{_seg(item_id)}/restore")

    async def async_test_notification(self, setting_id: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"{API_NOTIFICATIONS_SETTINGS}/{_seg(setting_id)}/test"
        )

    async def async_fetch_image(
        self, path: str, query_string: str
    ) -> tuple[bytes, str]:
        """Fetch a presigned image (no JWT: the URL signature gates access).

        Returns ``(body, content_type)``; raises ``WardrowbeApiError`` with the
        upstream status on a non-200, or 413 past ``MAX_IMAGE_BYTES``.
        """
        url = self._url(path)
        if query_string:
            url = f"{url}?{query_string}"
        try:
            async with self._session.get(
                url,
                ssl=self._verify_ssl,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    raise WardrowbeApiError(
                        f"image fetch → {resp.status}", status=resp.status
                    )
                body = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_IMAGE_BYTES:
                        raise WardrowbeApiError("image too large", status=413)
                content_type = resp.headers.get(
                    "Content-Type", "application/octet-stream"
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WardrowbeApiError(f"image fetch failed: {err}") from err
        return bytes(body), content_type

    # ---- internals ----

    def _url(self, path: str) -> str:
        return f"{self._host}{path}"

    async def _ensure_jwt(self, *, stale: int | None = None) -> tuple[str, int]:
        """Return ``(jwt, generation)``, re-syncing if expired or still ``stale``.

        Passing the generation that just got a 401 as ``stale`` lets
        concurrent requests share one re-sync instead of each forcing their own.
        """
        async with self._sync_lock:
            now = datetime.now(UTC)
            if (
                self._jwt
                and self._jwt_generation != stale
                and self._jwt_refresh_at
                and self._jwt_refresh_at > now
            ):
                return self._jwt, self._jwt_generation
            try:
                payload = await self._token_provider.async_get_sync_payload()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise WardrowbeApiError(f"Failed to refresh auth token: {err}") from err
            try:
                async with self._session.post(
                    self._url(API_AUTH_SYNC),
                    json=payload,
                    ssl=self._verify_ssl,
                    timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
                ) as resp:
                    body_text = await resp.text()
                    # 5xx/429 are outages, not bad credentials — don't force reauth.
                    if resp.status >= 500 or resp.status == 429:
                        raise WardrowbeApiError(
                            f"/auth/sync failed: {resp.status} {body_text}",
                            status=resp.status,
                        )
                    if resp.status >= 400:
                        raise WardrowbeAuthError(
                            f"/auth/sync failed: {resp.status} {body_text}",
                            status=resp.status,
                        )
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise WardrowbeApiError(f"/auth/sync transport error: {err}") from err
            except ValueError as err:
                raise WardrowbeApiError(f"/auth/sync returned invalid JSON: {err}") from err
            if not isinstance(body, dict):
                raise WardrowbeApiError("/auth/sync returned a non-object body")
            jwt = body.get("access_token")
            if not jwt:
                raise WardrowbeAuthError("/auth/sync did not return access_token")
            try:
                ttl = int(body.get("expires_in") or _DEFAULT_JWT_TTL_SECONDS)
            except (TypeError, ValueError):
                ttl = _DEFAULT_JWT_TTL_SECONDS
            ttl = max(ttl, 1)
            # Short-lived tokens would otherwise sit permanently inside the
            # leeway and re-sync on every request.
            leeway = min(_JWT_REFRESH_LEEWAY, timedelta(seconds=ttl / 2))
            self._jwt = jwt
            self._jwt_refresh_at = now + timedelta(seconds=ttl) - leeway
            self._jwt_generation += 1
            return jwt, self._jwt_generation

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        last_status: int | None = None
        last_body: str | None = None
        rejected: int | None = None
        for attempt in (0, 1):
            jwt, generation = await self._ensure_jwt(stale=rejected)
            try:
                async with self._session.request(
                    method,
                    self._url(path),
                    json=json,
                    params=params,
                    headers={"Authorization": f"Bearer {jwt}"},
                    ssl=self._verify_ssl,
                    timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        rejected = generation
                        continue
                    last_status = resp.status
                    if resp.status >= 400:
                        last_body = await resp.text()
                        break
                    if resp.status == 204:
                        return None
                    return await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise WardrowbeApiError(f"{method} {path} failed: {err}") from err
        if last_status == 401:
            raise WardrowbeAuthError(
                f"{method} {path} → 401 after re-sync: {last_body}"
            )
        raise WardrowbeApiError(
            f"{method} {path} → {last_status}: {last_body}", status=last_status
        )


def _seg(value: str) -> str:
    """Encode an id as one path segment so it can't traverse or add a query."""
    value = str(value)
    # Quoting leaves dots alone, and yarl collapses "."/".." segments.
    if value in {"", ".", ".."}:
        raise WardrowbeApiError(f"Invalid id: {value!r}", status=400)
    return quote(value, safe="")


def _coerce_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "results", "data", "outfits", "notifications"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


IMAGE_URL_KEYS: tuple[str, ...] = (
    "image_url",
    "thumbnail_url",
    "image_path",
    "thumbnail_path",
    "preview_url",
    "composite_image_url",
    "composite_url",
    "photo_url",
    "media_url",
)


def _resolve_image_urls(
    payload: Any,
    host: str,
    entry_id: str | None = None,
    sign_path: Callable[[str], str] | None = None,
) -> None:
    """Recursively rewrite relative Wardrowbe image URLs in-place.

    When ``entry_id`` is provided, paths are rewritten to point at the
    HA-side proxy view (``/api/wardrowbe/image/{entry_id}/...``) so
    browsers always load images from HA's own origin — sidestepping
    mixed-content, CORS, and split-network issues with the Wardrowbe
    host. The view requires HA auth, so ``sign_path`` turns each proxy path
    into a short-lived signed one that an ``<img>`` can load without an
    auth header. Without ``entry_id`` we fall back to prepending the
    Wardrowbe host directly.
    """
    if isinstance(payload, list):
        for entry in payload:
            _resolve_image_urls(entry, host, entry_id, sign_path)
        return
    if not isinstance(payload, dict):
        return
    for key in IMAGE_URL_KEYS:
        val = payload.get(key)
        if not isinstance(val, str) or not val.startswith("/"):
            continue
        # Idempotent: skip values that have already been routed through
        # the HA proxy, otherwise we'd double-prefix on repeated passes.
        if val.startswith("/api/wardrowbe/image/"):
            continue
        if entry_id:
            proxied = f"/api/wardrowbe/image/{entry_id}{val}"
            payload[key] = sign_path(proxied) if sign_path else proxied
        elif host:
            payload[key] = f"{host}{val}"
    for val in payload.values():
        if isinstance(val, (list, dict)):
            _resolve_image_urls(val, host, entry_id, sign_path)
