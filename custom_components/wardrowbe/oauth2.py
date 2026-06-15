"""OIDC / OAuth2 helpers for the Wardrowbe integration.

Wardrowbe accepts any OIDC issuer (PocketID, Authentik, Keycloak, Auth0, …),
so authorize/token URLs are not known until the user picks a host. This module
provides:

* ``WardrowbeOAuth2Implementation`` — a per-entry ``LocalOAuth2Implementation``
  parameterised with discovered endpoint URLs, the requested scopes, and an
  optional PKCE branch for public clients that have no client_secret.
* ``discover_oidc_endpoints`` — fetches ``.well-known/openid-configuration``.
* ``OIDCTokenProvider`` — adapts an HA-managed ``OAuth2Session`` to the
  ``TokenProvider`` interface used by ``WardrowbeClient``.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any, cast

import aiohttp
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TokenProvider, WardrowbeAuthError, build_oidc_sync_payload
from .const import DEFAULT_OIDC_SCOPES


class WardrowbeOAuth2Implementation(
    config_entry_oauth2_flow.LocalOAuth2Implementation
):
    """Per-issuer OAuth2 implementation with optional PKCE.

    If ``client_secret`` is empty/None, the implementation behaves as a public
    client: a fresh ``code_verifier`` is generated per authorize step, hashed
    into a ``code_challenge`` (S256), and the verifier is sent at the token
    exchange in place of a client_secret.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        domain: str,
        client_id: str,
        client_secret: str | None,
        authorize_url: str,
        token_url: str,
        issuer_url: str,
        name: str,
        scopes: str = DEFAULT_OIDC_SCOPES,
    ) -> None:
        # LocalOAuth2Implementation expects ``client_secret: str``. For PKCE
        # public clients we pass an empty string and override _token_request
        # to omit it entirely from token exchanges and refreshes.
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
        self._use_pkce = not client_secret
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
        state = external_data.get("state") or {}
        flow_id = state.get("flow_id") if isinstance(state, dict) else None
        verifier = self._pkce_verifiers.pop(flow_id, None) if flow_id else None
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": external_data["code"],
                "redirect_uri": (
                    state.get("redirect_uri") if isinstance(state, dict) else None
                ),
                "code_verifier": verifier or "",
            }
        )

    async def _token_request(self, data: dict[str, Any]) -> dict[str, Any]:
        if not self._use_pkce:
            return cast(
                dict[str, Any], await super()._token_request(data)  # type: ignore[misc]
            )
        # PKCE path: send client_id but never client_secret.
        session = async_get_clientsession(self.hass)
        data["client_id"] = self.client_id
        data.pop("client_secret", None)
        async with session.post(
            self.token_url,
            data=data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
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
        await self._oauth_session.async_ensure_token_valid()
        token = self._oauth_session.token or {}
        id_token = token.get("id_token")
        if not id_token:
            raise WardrowbeAuthError(
                "OIDC provider did not return an id_token; reauthenticate."
            )
        return build_oidc_sync_payload(id_token)
