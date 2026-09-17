"""Tests for the Emerald EMS API client."""

from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.emerald_ems.api import (
    FLASHES_DATA_PATH,
    PROPERTY_LIST_PATH,
    SIGN_IN_PATH,
    TOKEN_REFRESH_PATH,
    EmeraldApiClient,
    EmeraldApiClientAuthenticationError,
    EmeraldApiClientCommunicationError,
    EmeraldApiClientError,
)

from .conftest import (
    TEST_PASSWORD,
    TEST_USERNAME,
    api_url,
    load_json_fixture,
)

START = date(2024, 3, 9)
END = date(2024, 3, 10)


def build_client(hass: HomeAssistant) -> EmeraldApiClient:
    """Build a client bound to the mocked session."""
    return EmeraldApiClient(
        username=TEST_USERNAME,
        password=TEST_PASSWORD,
        session=async_get_clientsession(hass),
    )


class ScriptedClient(EmeraldApiClient):
    """Client whose transport is replaced by a scripted responder.

    Used for the token renewal paths, where the same URL has to answer
    differently on successive calls.
    """

    def __init__(self, *args: Any, script, **kwargs: Any) -> None:
        """Initialise with a callable returning bodies or exceptions."""
        super().__init__(*args, **kwargs)
        self._script = script
        self.calls: list[tuple[str, str | None]] = []

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Return the scripted result for this call."""
        self.calls.append((path, token))
        result = self._script(path, token)
        if isinstance(result, Exception):
            raise result
        return result


async def test_sign_in_stores_session(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """A successful sign-in records the token and customer."""
    client = build_client(hass)
    info = await client.async_sign_in()

    assert client.is_authenticated
    assert client.customer_id == "customer-uuid-1"
    assert info["email"] == TEST_USERNAME


async def test_sign_in_sends_expected_payload(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """The sign-in body carries the credentials and client identification."""
    client = build_client(hass)
    await client.async_sign_in()

    _method, _url, payload, headers = mock_api.mock_calls[0]
    assert payload["email"] == TEST_USERNAME
    assert payload["password"] == TEST_PASSWORD
    assert payload["passcode"] is None
    assert payload["device_type"] == "android"
    assert "Authorization" not in headers


@pytest.mark.parametrize("status", [401, 403])
async def test_http_status_auth_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """An HTTP 401 or 403 is an authentication failure."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), status=status, json={})
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientAuthenticationError):
        await client.async_sign_in()


async def test_body_code_auth_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An auth code in the body is an authentication failure despite HTTP 200."""
    aioclient_mock.post(
        api_url(SIGN_IN_PATH),
        json={"code": 401, "message": "Invalid credentials."},
    )
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientAuthenticationError):
        await client.async_sign_in()


async def test_other_body_code_is_not_auth_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A non-auth error code must not be mistaken for bad credentials.

    Re-signing in on every unexpected code would hammer the upstream service.
    """
    aioclient_mock.post(
        api_url(SIGN_IN_PATH),
        json={"code": 500, "message": "Internal error."},
    )
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientError) as err:
        await client.async_sign_in()

    assert not isinstance(err.value, EmeraldApiClientAuthenticationError)


async def test_missing_token_is_auth_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A success response without a token cannot establish a session."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), json={"code": 200, "info": {}})
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientAuthenticationError):
        await client.async_sign_in()


async def test_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Transport failures surface as communication errors."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), exc=aiohttp.ClientError("boom"))
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientCommunicationError):
        await client.async_sign_in()


async def test_timeout(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Timeouts surface as communication errors, not auth errors."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), exc=TimeoutError())
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientCommunicationError):
        await client.async_sign_in()


async def test_non_json_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An HTML error page is a communication error."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), text="<html>nope</html>")
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientCommunicationError):
        await client.async_sign_in()


async def test_get_properties(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """The property list is parsed into properties and devices."""
    client = build_client(hass)
    properties = await client.async_get_properties()

    assert len(properties) == 1
    site = properties[0]
    assert site.name == "123 EXAMPLE STREET"
    assert site.tariff is not None
    assert site.tariff.unit_charge == 0.2035
    assert site.tariff.is_flat_rate is True
    assert len(site.devices) == 1
    assert site.devices[0].serial_number == "2108123123"
    assert site.devices[0].is_active


async def test_get_properties_authenticates_first(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """An authenticated call signs in on demand and sends the bearer token."""
    client = build_client(hass)
    await client.async_get_properties()

    assert mock_api.mock_calls[0][1].path == SIGN_IN_PATH
    assert mock_api.mock_calls[1][3]["Authorization"] == "Bearer token-one"


async def test_get_energy_data(
    hass: HomeAssistant, mock_api: AiohttpClientMocker
) -> None:
    """Energy data is requested for the given range and parsed."""
    client = build_client(hass)
    data = await client.async_get_energy_data("device-uuid-1", START, END)

    assert data.device_id == "device-uuid-1"
    assert data.average_daily_spend == 1.55
    assert len(data.days) == 2

    _method, url, _payload, _headers = mock_api.mock_calls[-1]
    assert url.query["device_id"] == "device-uuid-1"
    assert url.query["start_date"] == "2024-03-09"
    assert url.query["end_date"] == "2024-03-10"


async def test_missing_info_is_api_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A success code with no payload is reported rather than parsed."""
    aioclient_mock.post(api_url(SIGN_IN_PATH), json=load_json_fixture("sign_in"))
    aioclient_mock.get(api_url(PROPERTY_LIST_PATH), json={"code": 200})
    client = build_client(hass)

    with pytest.raises(EmeraldApiClientError):
        await client.async_get_properties()


async def test_expired_token_is_refreshed(hass: HomeAssistant) -> None:
    """A rejected token is refreshed and the original call retried once."""
    flashes = load_json_fixture("flashes_data")
    refreshed = load_json_fixture("token_refresh")

    def script(path: str, token: str | None):
        if path == FLASHES_DATA_PATH and token == "token-one":
            return EmeraldApiClientAuthenticationError("expired")
        if path == TOKEN_REFRESH_PATH:
            return refreshed
        if path == FLASHES_DATA_PATH:
            return flashes
        raise AssertionError(f"unexpected call to {path}")

    client = ScriptedClient(
        TEST_USERNAME,
        TEST_PASSWORD,
        session=None,
        script=script,
    )
    client._token = "token-one"

    data = await client.async_get_energy_data("device-uuid-1", START, END)

    assert data.device_id == "device-uuid-1"
    assert [path for path, _token in client.calls] == [
        FLASHES_DATA_PATH,
        TOKEN_REFRESH_PATH,
        FLASHES_DATA_PATH,
    ]
    assert client.calls[-1][1] == "token-two"


async def test_failed_refresh_falls_back_to_sign_in(hass: HomeAssistant) -> None:
    """When refreshing fails, the client signs in again from scratch."""
    flashes = load_json_fixture("flashes_data")
    signed_in = load_json_fixture("sign_in")

    def script(path: str, token: str | None):
        if path == FLASHES_DATA_PATH and token == "stale":
            return EmeraldApiClientAuthenticationError("expired")
        if path == TOKEN_REFRESH_PATH:
            return EmeraldApiClientAuthenticationError("refresh rejected")
        if path == SIGN_IN_PATH:
            return signed_in
        if path == FLASHES_DATA_PATH:
            return flashes
        raise AssertionError(f"unexpected call to {path}")

    client = ScriptedClient(
        TEST_USERNAME,
        TEST_PASSWORD,
        session=None,
        script=script,
    )
    client._token = "stale"

    await client.async_get_energy_data("device-uuid-1", START, END)

    assert [path for path, _token in client.calls] == [
        FLASHES_DATA_PATH,
        TOKEN_REFRESH_PATH,
        SIGN_IN_PATH,
        FLASHES_DATA_PATH,
    ]


async def test_retry_does_not_loop(hass: HomeAssistant) -> None:
    """A second rejection after renewal is raised, not retried forever."""

    def script(path: str, token: str | None):
        if path == SIGN_IN_PATH:
            return load_json_fixture("sign_in")
        if path == TOKEN_REFRESH_PATH:
            return load_json_fixture("token_refresh")
        return EmeraldApiClientAuthenticationError("still rejected")

    client = ScriptedClient(
        TEST_USERNAME,
        TEST_PASSWORD,
        session=None,
        script=script,
    )
    client._token = "token-one"

    with pytest.raises(EmeraldApiClientAuthenticationError):
        await client.async_get_energy_data("device-uuid-1", START, END)

    assert [path for path, _token in client.calls].count(FLASHES_DATA_PATH) == 2
