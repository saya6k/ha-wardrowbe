"""OIDC / OAuth2 helpers for the Wardrowbe integration.

Wardrowbe accepts any OIDC issuer (PocketID, Authentik, Keycloak, Auth0, …),
so authorize/token URLs are not known until the user picks a host. This module
provides:

* ``WardrowbeOAuth2Implementation`` — a per-entry ``LocalOAuth2Implementation``
  parameterised with discovered endpoint URLs, the requested scopes, and an
  optional PKCE branch, independent of whether the client has a secret.
* ``discover_oidc_endpoints`` — fetches ``.well-known/openid-configuration``.
* ``OIDCTokenProvider`` — adapts an HA-managed ``OAuth2Session`` to the
  ``TokenProvider`` interface used by ``WardrowbeClient``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from json import JSONDecodeError
from typing import Any, cast

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from .api import (
    TokenProvider,
    WardrowbeApiError,
    WardrowbeAuthError,
    build_oidc_sync_payload,
)
from .const import DEFAULT_OIDC_SCOPES

_LOGGER = logging.getLogger(__name__)


class PKCEVerifierMissingError(aiohttp.ClientError):
    """No stored code_verifier for the flow being resolved.

    A ``ClientError`` so HA's OAuth flow handler aborts with ``oauth_failed``.
    """


class WardrowbeOAuth2Implementation(
    config_entry_oauth2_flow.LocalOAuth2Implementation
):
    """Per-issuer OAuth2 implementation with optional PKCE.

    PKCE and having a ``client_secret`` are independent: a confidential
    client (has a secret) can still use PKCE — OAuth 2.1 recommends PKCE for
    every client type, not just public ones — so ``use_pkce`` is an explicit
    flag, not inferred from whether ``client_secret`` is set. Public clients
    (no secret) must set ``use_pkce=True``; there's no other way for them to
    authenticate the code exchange.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        domain: str,
        client_id: str,
        client_secret: str | None,
        use_pkce: bool,
        authorize_url: str,
        token_url: str,
        issuer_url: str,
        name: str,
        scopes: str = DEFAULT_OIDC_SCOPES,
    ) -> None:
        # LocalOAuth2Implementation expects ``client_secret: str``.
        super().__init__(
            hass,
            domain,
            client_id,
            client_secret or "",
            authorize_url,
            token_url,
        )
        self._issuer_url = issuer_url
        self._display_name = name
        self._scopes = scopes
        self._use_pkce = use_pkce
        self._pkce_verifiers: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self._display_name

    @property
    def issuer_url(self) -> str:
        return self._issuer_url

    @property
    def use_pkce(self) -> bool:
        return self._use_pkce

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        return {"scope": self._scopes}

    async def async_generate_authorize_url(self, flow_id: str) -> str:
        url = await super().async_generate_authorize_url(flow_id)
        if not self._use_pkce:
            return url
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        self._pkce_verifiers[flow_id] = verifier
        return str(
            URL(url).update_query(
                {
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    async def async_resolve_external_data(self, external_data: Any) -> dict[str, Any]:
        if not self._use_pkce:
            return await super().async_resolve_external_data(external_data)
        # HA's callback view always supplies the decoded state with both keys
        # (the parent implementation indexes them the same way).
        state = external_data["state"]
        flow_id = state["flow_id"]
        verifier = self._pkce_verifiers.pop(flow_id, None)
        if not verifier:
            # Only after a reload mid-flow: verifiers live in memory only.
            raise PKCEVerifierMissingError(
                f"No PKCE code_verifier for flow {flow_id}; restart the flow."
            )
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": state["redirect_uri"],
                "code_verifier": verifier,
            }
        )

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        if not self._use_pkce:
            return cast(
                dict[str, Any], await super()._token_request(data)  # type: ignore[misc]
            )
        # PKCE path: send client_id, plus client_secret if this is a
        # confidential client also using PKCE (not just public clients).
        session = async_get_clientsession(self.hass)
        # Start from a clean dict so we don't accidentally inherit keys meant
        # only for the parent implementation.
        request_data: dict[str, Any] = {}
        for key, value in data.items():
            if value is not None and value != "":
                request_data[key] = value
        request_data["client_id"] = self.client_id
        if self.client_secret:
            request_data["client_secret"] = self.client_secret
        _LOGGER.debug("PKCE token request to %s", self.token_url)
        async with session.post(
            self.token_url,
            data=request_data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status >= 400:
                try:
                    error_response = await resp.json(content_type=None)
                except (aiohttp.ClientError, JSONDecodeError):
                    error_response = {}
                error_code = error_response.get("error", "unknown")
                error_description = error_response.get(
                    "error_description", "unknown error"
                )
                _LOGGER.error(
                    "Token request for %s failed (HTTP %s, error=%s): %s",
                    self.domain,
                    resp.status,
                    error_code,
                    error_description,
                )
            resp.raise_for_status()
            return cast(dict[str, Any], await resp.json(content_type=None))


async def discover_oidc_endpoints(
    hass: HomeAssistant, issuer_url: str
) -> dict[str, Any]:
    """Fetch the issuer's openid-configuration document."""
    session = async_get_clientsession(hass)
    well_known = f"{issuer_url.rstrip('/')}/.well-known/openid-configuration"
    async with session.get(
        well_known, timeout=aiohttp.ClientTimeout(total=15)
    ) as resp:
        resp.raise_for_status()
        return cast(dict[str, Any], await resp.json())


class OIDCTokenProvider(TokenProvider):
    """Adapts ``OAuth2Session`` so its current id_token feeds /auth/sync."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        implementation: WardrowbeOAuth2Implementation,
    ) -> None:
        self._oauth_session = config_entry_oauth2_flow.OAuth2Session(
            hass, entry, implementation
        )

    async def async_get_sync_payload(self) -> dict[str, Any]:
        try:
            await self._oauth_session.async_ensure_token_valid()
        except aiohttp.ClientResponseError as err:
            # A 4xx (e.g. invalid_grant) means the refresh token is dead;
            # 429/5xx are provider outages that should just retry later.
            if 400 <= err.status < 500 and err.status != 429:
                raise WardrowbeAuthError(f"OIDC token refresh rejected: {err}") from err
            raise WardrowbeApiError(f"OIDC token refresh failed: {err}") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WardrowbeApiError(f"OIDC token refresh failed: {err}") from err
        token = self._oauth_session.token or {}
        id_token = token.get("id_token")
        if not id_token:
            raise WardrowbeAuthError(
                "OIDC provider did not return an id_token; reauthenticate."
            )
        return build_oidc_sync_payload(id_token)
