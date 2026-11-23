"""Config flow tests — focused on the dev-mode happy path.

OIDC paths require a full OAuth dance which is best covered with the upstream
``current_request_with_proxy`` and ``aioclient_mock`` fixtures; left as a TODO
for follow-up coverage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from yarl import URL

from custom_components.wardrowbe.const import (
    API_AUTH_CONFIG,
    AUTH_MODE_DEV,
    AUTH_MODE_OIDC,
    CONF_AUTH_MODE,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_EXTERNAL_ID,
    CONF_HOST,
    CONF_ISSUER_URL,
    CONF_SCOPES,
    CONF_USE_PKCE,
    CONF_VERIFY_SSL,
    DOMAIN,
)


async def test_dev_mode_happy_path(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "http://wardrowbe.test",
            CONF_AUTH_MODE: AUTH_MODE_DEV,
            CONF_VERIFY_SSL: False,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dev"

    with patch(
        "custom_components.wardrowbe.config_flow.WardrowbeClient", autospec=True
    ) as ClientCls:
        ClientCls.return_value.async_session_info = AsyncMock(
            return_value={"id": "user-123", "name": "Test User"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EXTERNAL_ID: "user-123"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wardrowbe (Test User)"
    data = result["data"]
    assert data[CONF_HOST] == "http://wardrowbe.test"
    assert data[CONF_AUTH_MODE] == AUTH_MODE_DEV
    assert data[CONF_EXTERNAL_ID] == "user-123"


async def test_dev_mode_reconfigure(
    hass: HomeAssistant, dev_mode_entry: MockConfigEntry
) -> None:
    dev_mode_entry.add_to_hass(hass)
    assert dev_mode_entry.data[CONF_VERIFY_SSL] is False

    result = await dev_mode_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "http://wardrowbe.test",
            CONF_AUTH_MODE: AUTH_MODE_DEV,
            CONF_VERIFY_SSL: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dev"

    with patch(
        "custom_components.wardrowbe.config_flow.WardrowbeClient", autospec=True
    ) as ClientCls:
        ClientCls.return_value.async_session_info = AsyncMock(
            return_value={"id": "user-123", "name": "Test User"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EXTERNAL_ID: "user-123"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert dev_mode_entry.data[CONF_VERIFY_SSL] is True


async def test_oidc_pkce_required_without_secret(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A public client (no secret) can't opt out of PKCE."""
    aioclient_mock.get(f"http://wardrowbe.test{API_AUTH_CONFIG}", status=404)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "http://wardrowbe.test",
            CONF_AUTH_MODE: AUTH_MODE_OIDC,
            CONF_VERIFY_SSL: True,
        },
    )
    assert result["step_id"] == "oidc"

    with patch(
        "custom_components.wardrowbe.config_flow.discover_oidc_endpoints",
        new=AsyncMock(
            return_value={
                "authorization_endpoint": "http://issuer.test/authorize",
                "token_endpoint": "http://issuer.test/token",
            }
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ISSUER_URL: "http://issuer.test",
                CONF_CLIENT_ID: "my-client",
                CONF_CLIENT_SECRET: "",
                CONF_USE_PKCE: False,
                CONF_SCOPES: "openid",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "oidc"
    assert result["errors"] == {"base": "pkce_required_without_secret"}


async def test_dev_mode_invalid_host(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "not-a-url",
            CONF_AUTH_MODE: AUTH_MODE_DEV,
            CONF_VERIFY_SSL: True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_host"}


_DISCOVERY = {
    "authorization_endpoint": "http://issuer.test/authorize",
    "token_endpoint": "http://issuer.test/token",
}


async def _oidc_to_authorize_url(
    hass: HomeAssistant, oidc_input: dict[str, object] | None
) -> tuple[str, dict[str, object]]:
    """Run user → oidc → external step; return the authorize URL and oidc defaults."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "http://wardrowbe.test",
            CONF_AUTH_MODE: AUTH_MODE_OIDC,
            CONF_VERIFY_SSL: True,
        },
    )
    assert result["step_id"] == "oidc"
    defaults = {
        str(key): key.default()
        for key in result["data_schema"].schema
        if callable(getattr(key, "default", None))
    }
    with patch(
        "custom_components.wardrowbe.config_flow.discover_oidc_endpoints",
        new=AsyncMock(return_value=_DISCOVERY),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], oidc_input or defaults
        )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    return result["url"], defaults


@pytest.mark.usefixtures("current_request_with_host")
async def test_oidc_second_account_on_same_host(hass: HomeAssistant) -> None:
    """Adding another account on a known host reuses its OIDC client and forces login."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="http://wardrowbe.test::user-1",
        data={
            CONF_HOST: "http://wardrowbe.test",
            CONF_VERIFY_SSL: True,
            CONF_AUTH_MODE: AUTH_MODE_OIDC,
            CONF_ISSUER_URL: "http://issuer.test",
            CONF_CLIENT_ID: "my-client",
            CONF_CLIENT_SECRET: "s3cret",
            CONF_USE_PKCE: False,
            CONF_SCOPES: "openid email",
        },
    ).add_to_hass(hass)

    url, defaults = await _oidc_to_authorize_url(hass, None)

    assert defaults[CONF_ISSUER_URL] == "http://issuer.test"
    assert defaults[CONF_CLIENT_ID] == "my-client"
    assert defaults[CONF_USE_PKCE] is False
    assert defaults[CONF_SCOPES] == "openid email"
    assert defaults[CONF_CLIENT_SECRET] == ""  # never echoed back into the form
    query = URL(url).query
    assert query["prompt"] == "login"
    assert query["client_id"] == "my-client"
    # Blank secret field kept the stored secret, so PKCE-off was accepted.
    assert "code_challenge" not in query


@pytest.mark.usefixtures("current_request_with_host")
async def test_oidc_first_account_does_not_force_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"http://wardrowbe.test{API_AUTH_CONFIG}", status=404)
    url, _ = await _oidc_to_authorize_url(
        hass,
        {
            CONF_ISSUER_URL: "http://issuer.test",
            CONF_CLIENT_ID: "my-client",
            CONF_CLIENT_SECRET: "",
            CONF_USE_PKCE: True,
            CONF_SCOPES: "openid",
        },
    )
    assert "prompt" not in URL(url).query


@pytest.mark.usefixtures("current_request_with_host")
async def test_oidc_prefills_client_from_auth_config(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Issuer and client ID come from /auth/config; a public client just submits."""
    aioclient_mock.get(
        f"http://wardrowbe.test{API_AUTH_CONFIG}",
        json={
            "oidc": {
                "enabled": True,
                "issuer_url": "http://issuer.test",
                "client_id": "wardrowbe-mobile",
            }
        },
    )

    url, defaults = await _oidc_to_authorize_url(hass, None)

    assert defaults[CONF_ISSUER_URL] == "http://issuer.test"
    assert defaults[CONF_CLIENT_ID] == "wardrowbe-mobile"
    assert defaults[CONF_USE_PKCE] is True
    query = URL(url).query
    assert query["client_id"] == "wardrowbe-mobile"
    assert "code_challenge" in query
    assert "prompt" not in query
