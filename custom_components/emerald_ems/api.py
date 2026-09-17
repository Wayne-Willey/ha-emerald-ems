"""Client for the Emerald EMS cloud API.

The API is undocumented and was reverse engineered from the Android app. It
answers with HTTP 200 for most outcomes and carries the real result in a
``code`` field in the body, so both have to be checked.
"""

from __future__ import annotations

import asyncio
import socket
from datetime import date
from typing import Any, Final

import aiohttp

from .const import (
    API_BASE_URL,
    APP_VERSION,
    DEFAULT_TIMEOUT,
    DEVICE_NAME,
    DEVICE_OS_VERSION,
    DEVICE_TYPE,
    LOGGER,
)
from .models import EmeraldEnergyData, EmeraldProperty

SIGN_IN_PATH: Final = "/api/v1/customer/sign-in"
TOKEN_REFRESH_PATH: Final = "/api/v1/customer/token-refresh"
PROPERTY_LIST_PATH: Final = "/api/v1/customer/property/list"
FLASHES_DATA_PATH: Final = "/api/v1/customer/device/get-by-date/flashes-data"

SUCCESS_CODE: Final = 200

# Outcomes that mean the token or the credentials are the problem. Anything else
# is reported as a plain API error: re-authenticating on an unrelated failure
# would just hammer sign-in against infrastructure that is not ours.
AUTH_STATUS_CODES: Final = frozenset({401, 403})

BASE_HEADERS: Final = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept-Encoding": "gzip",
    "User-Agent": "ok",
}


class EmeraldApiClientError(Exception):
    """Something went wrong talking to the Emerald API."""


class EmeraldApiClientCommunicationError(EmeraldApiClientError):
    """The API could not be reached, or did not answer in time."""


class EmeraldApiClientAuthenticationError(EmeraldApiClientError):
    """The credentials were rejected, or the session could not be renewed."""


class EmeraldApiClient:
    """Authenticated, token caching client for the Emerald EMS cloud API."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        *,
        base_url: str = API_BASE_URL,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialise the client."""
        self._username = username
        self._password = password
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

        self._token: str | None = None
        self._customer_id: str | None = None
        self._customer_info: dict[str, Any] = {}
        # Serialises token renewal so that concurrent requests cannot each kick
        # off their own sign-in.
        self._auth_lock = asyncio.Lock()

    @property
    def customer_id(self) -> str | None:
        """Customer id from the last successful sign-in."""
        return self._customer_id

    @property
    def customer_info(self) -> dict[str, Any]:
        """Customer object from the last successful sign-in."""
        return dict(self._customer_info)

    @property
    def is_authenticated(self) -> bool:
        """Whether a token is currently held."""
        return self._token is not None

    def _url(self, path: str) -> str:
        """Absolute URL for an API path."""
        return f"{self._base_url}{path}"

    @staticmethod
    def _device_payload() -> dict[str, Any]:
        """Client identification sent with every authentication call."""
        return {
            "app_version": APP_VERSION,
            "device_name": DEVICE_NAME,
            "device_os_version": DEVICE_OS_VERSION,
            "device_type": DEVICE_TYPE,
        }

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Perform one HTTP call and return the decoded body.

        Raises on transport problems and on authentication rejections, but
        leaves interpretation of the body's ``code`` to the caller.
        """
        headers = dict(BASE_HEADERS)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"

        try:
            async with asyncio.timeout(self._timeout):
                response = await self._session.request(
                    method,
                    self._url(path),
                    params=params,
                    json=json,
                    headers=headers,
                )

                if response.status in AUTH_STATUS_CODES:
                    raise EmeraldApiClientAuthenticationError(
                        f"Emerald API rejected the request to {path} "
                        f"with HTTP {response.status}"
                    )

                if response.status >= 400:
                    raise EmeraldApiClientError(
                        f"Emerald API returned HTTP {response.status} for {path}"
                    )

                # Error responses have been seen without a JSON content type.
                try:
                    body = await response.json(content_type=None)
                except ValueError as exception:
                    raise EmeraldApiClientCommunicationError(
                        f"Emerald API returned a non-JSON body for {path}"
                    ) from exception

        except TimeoutError as exception:
            raise EmeraldApiClientCommunicationError(
                f"Timeout while contacting the Emerald API for {path}"
            ) from exception
        except (aiohttp.ClientError, socket.gaierror) as exception:
            raise EmeraldApiClientCommunicationError(
                f"Error contacting the Emerald API for {path}: {exception}"
            ) from exception

        if not isinstance(body, dict):
            raise EmeraldApiClientCommunicationError(
                f"Emerald API returned an unexpected payload for {path}"
            )

        return body

    @staticmethod
    def _check_code(body: dict[str, Any], path: str) -> dict[str, Any]:
        """Validate the body level ``code`` and return the body unchanged."""
        code = body.get("code")
        if code == SUCCESS_CODE:
            return body

        message = body.get("message") or "no message"
        if code in AUTH_STATUS_CODES:
            raise EmeraldApiClientAuthenticationError(
                f"Emerald API rejected the request to {path}: {message}"
            )
        raise EmeraldApiClientError(
            f"Emerald API returned code {code} for {path}: {message}"
        )

    def _store_session(self, body: dict[str, Any]) -> None:
        """Record the token and customer details from an auth response."""
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise EmeraldApiClientAuthenticationError(
                "Emerald API did not return a token"
            )

        self._token = token
        info = body.get("info")
        if isinstance(info, dict):
            self._customer_info = info
            customer_id = info.get("id")
            self._customer_id = str(customer_id) if customer_id else None

    async def async_sign_in(self) -> dict[str, Any]:
        """Authenticate with username and password, replacing any held token."""
        payload = {
            **self._device_payload(),
            "device_token": "",
            "email": self._username,
            "passcode": None,
            "password": self._password,
        }
        body = self._check_code(
            await self._send("POST", SIGN_IN_PATH, json=payload), SIGN_IN_PATH
        )
        self._store_session(body)
        LOGGER.debug("Signed in to the Emerald API as %s", self._username)
        return self.customer_info

    async def async_refresh_token(self) -> None:
        """Renew the held token. Requires an existing token."""
        if self._token is None:
            raise EmeraldApiClientAuthenticationError("No token to refresh")

        payload = {**self._device_payload(), "background_sync_count": 0}
        body = self._check_code(
            await self._send(
                "POST", TOKEN_REFRESH_PATH, json=payload, token=self._token
            ),
            TOKEN_REFRESH_PATH,
        )
        self._store_session(body)
        LOGGER.debug("Refreshed the Emerald API token")

    async def _async_ensure_token(self) -> str:
        """Return a usable token, signing in if none is held."""
        if self._token is not None:
            return self._token

        async with self._auth_lock:
            if self._token is None:
                await self.async_sign_in()

        if self._token is None:  # pragma: no cover - async_sign_in raises first
            raise EmeraldApiClientAuthenticationError(
                "Could not establish an Emerald session"
            )
        return self._token

    async def _async_renew(self, stale_token: str) -> str:
        """Renew an expired session, falling back to a full sign-in.

        ``stale_token`` is the token that was just rejected. If another task has
        already replaced it, that new token is used instead of renewing again.
        """
        async with self._auth_lock:
            if self._token is not None and self._token != stale_token:
                return self._token

            try:
                await self.async_refresh_token()
            except EmeraldApiClientError as exception:
                # The token is roughly 24 hours old at this point and carries no
                # expiry claim, so a refusal here is expected rather than
                # exceptional. Fall back to a full sign-in.
                LOGGER.debug("Token refresh failed (%s), signing in again", exception)
                self._token = None
                await self.async_sign_in()

            if self._token is None:  # pragma: no cover - sign-in raises first
                raise EmeraldApiClientAuthenticationError(
                    "Could not renew the Emerald session"
                )
            return self._token

    async def _async_authenticated_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated call, renewing the session once if needed."""
        token = await self._async_ensure_token()

        try:
            body = await self._send(method, path, params=params, json=json, token=token)
            return self._check_code(body, path)
        except EmeraldApiClientAuthenticationError:
            token = await self._async_renew(token)

        body = await self._send(method, path, params=params, json=json, token=token)
        return self._check_code(body, path)

    async def async_get_properties(self) -> list[EmeraldProperty]:
        """Return every property on the account, owned and shared."""
        body = await self._async_authenticated_request("GET", PROPERTY_LIST_PATH)
        info = body.get("info")
        if not isinstance(info, dict):
            raise EmeraldApiClientError("Emerald API returned no property information")

        properties: list[EmeraldProperty] = []
        for key, is_shared in (("property", False), ("shared_property", True)):
            for raw in info.get(key) or []:
                if not isinstance(raw, dict):
                    continue
                if (
                    parsed := EmeraldProperty.from_api(raw, is_shared=is_shared)
                ) is not None:
                    properties.append(parsed)

        return properties

    async def async_get_energy_data(
        self,
        device_id: str,
        start_date: date,
        end_date: date,
    ) -> EmeraldEnergyData:
        """Return consumption for a device across an inclusive date range."""
        params = {
            "device_id": device_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        body = await self._async_authenticated_request(
            "GET", FLASHES_DATA_PATH, params=params
        )
        info = body.get("info")
        if not isinstance(info, dict):
            raise EmeraldApiClientError("Emerald API returned no energy data")

        return EmeraldEnergyData.from_api(info)

    async def async_validate_credentials(self) -> list[EmeraldProperty]:
        """Sign in and list properties, for use by the config flow."""
        await self.async_sign_in()
        return await self.async_get_properties()
