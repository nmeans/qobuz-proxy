"""
Qobuz API Client.

Handles authentication, session management, and signed API requests.
"""

import hashlib
import logging
import time
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)


class QobuzAPIError(Exception):
    """Qobuz API error."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class QobuzAPIClient:
    """Qobuz REST API client with request signing."""

    API_BASE = "https://www.qobuz.com/api.json/0.2"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        session_app_id: Optional[str] = None,
        session_app_secret: Optional[str] = None,
    ):
        """
        Initialize API client.

        Args:
            app_id: Qobuz application ID (used for request signing)
            app_secret: Qobuz application secret (used for request signing)
            session_app_id: App ID to use for session/start (defaults to app_id).
                Set this to scraped web-player credentials when using OAuth app
                credentials as the primary signing identity, because session/start
                only works with web-player app IDs.
            session_app_secret: App secret to use for session/start (defaults to app_secret).
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self._session_app_id = session_app_id or app_id
        self._session_app_secret = session_app_secret or app_secret
        self.user_auth_token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.x_session_id: Optional[str] = None
        self.x_session_expires_at: int = 0
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "QobuzAPIClient":
        """Async context manager entry."""
        headers = {
            "User-Agent": "Mozilla/5.0",
            "X-App-Id": self.app_id,
        }
        self._session = aiohttp.ClientSession(headers=headers)
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        """Async context manager exit."""
        if self._session:
            await self._session.close()
            self._session = None

    async def login_with_credentials(self, email: str, password: str) -> bool:
        """
        Login to Qobuz using email and password.

        Args:
            email: Qobuz account email
            password: Qobuz account password

        Returns:
            True if successful
        """
        try:
            # Per StreamCore32 reference: login is unsigned — email, password, and
            # app_id go in URL query params with NO request_ts/request_sig.
            # POST body is just "extra=partner".
            url = f"{self.API_BASE}/user/login?{urlencode({'email': email, 'password': password, 'app_id': self.app_id})}"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0",
                "X-App-Id": self.app_id,
                "Referer": "https://play.qobuz.com/",
                "Origin": "https://play.qobuz.com",
            }
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data="extra=partner", headers=headers, timeout=timeout) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.debug(f"Login failed: {resp.status} {body[:300]}")
                        return False
                    response = await resp.json()

            if response and "user_auth_token" in response:
                self.user_auth_token = response["user_auth_token"]
                user = response.get("user") or {}
                self.user_id = str(user.get("id", ""))
                logger.info(f"Logged in as user {self.user_id}")
                return True

        except Exception as e:
            logger.error(f"Login failed: {e}")

        return False

    async def exchange_token_for_app(self, user_id: str, source_token: str) -> bool:
        """Exchange a token scoped to another app for one scoped to this client's app_id.

        Calls ``user/login`` unsigned with *source_token* in the
        ``X-User-Auth-Token`` header.  If the server accepts the token it will
        return a ``user_auth_token`` that is valid for requests signed with
        *this* client's ``app_id`` / ``app_secret``.

        Returns True and populates ``user_auth_token`` / ``user_id`` on success.
        """
        try:
            headers = {
                "Content-Type": "text/plain;charset=UTF-8",
                "User-Agent": "Mozilla/5.0",
                "X-App-Id": self.app_id,
                "X-User-Auth-Token": source_token,
                "Referer": "https://play.qobuz.com/",
                "Origin": "https://play.qobuz.com",
            }
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.API_BASE}/user/login",
                    data="extra=partner",
                    headers=headers,
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.debug(f"Token exchange failed ({resp.status}): {body[:200]}")
                        return False
                    response = await resp.json()

            if response and "user_auth_token" in response:
                self.user_auth_token = response["user_auth_token"]
                self.user_id = user_id
                logger.info(f"Token exchanged for app_id={self.app_id}")
                return True

        except Exception as e:
            logger.error(f"Token exchange failed: {e}")

        return False

    async def login_with_token(self, user_id: str, auth_token: str) -> bool:
        """
        Login to Qobuz using a user auth token.

        Args:
            user_id: Qobuz user ID
            auth_token: User auth token (from browser login)

        Returns:
            True if successful
        """
        # First try: token in URL params (normal login flow).
        # Second try: token in X-User-Auth-Token header only (cross-app exchange —
        # the server may reject app-scoped tokens in URL params but accept them in
        # the header for re-authentication with a different app_id).
        for use_header in (False, True):
            try:
                if use_header:
                    # Put token in header; don't include it in signed URL params.
                    params: dict[str, Any] = {"user_id": user_id, "app_id": self.app_id}
                    old_token = self.user_auth_token
                    self.user_auth_token = auth_token
                    try:
                        response = await self._request_signed(
                            "user", "login", params=params, method="POST", body="extra=partner"
                        )
                    finally:
                        self.user_auth_token = old_token
                else:
                    params = {
                        "user_id": user_id,
                        "user_auth_token": auth_token,
                        "app_id": self.app_id,
                    }
                    response = await self._request_signed(
                        "user", "login", params=params, method="POST", body="extra=partner"
                    )

                if response and "user_auth_token" in response:
                    self.user_auth_token = response["user_auth_token"]
                    self.user_id = user_id
                    logger.info(f"Logged in as user {self.user_id}")
                    return True

            except Exception as e:
                logger.error(f"Login failed: {e}")

        return False

    async def start_session(self) -> bool:
        """
        Start a Qobuz session (required for API calls).

        Returns:
            True if successful
        """
        now_ms = int(time.time() * 1000)
        if self.x_session_id and self.x_session_expires_at > now_ms + 60000:
            return True  # Session still valid

        try:
            request_ts = f"{time.time():.6f}"
            params = {"profile": "qbz-1"}

            # Build signature using session-specific credentials (may differ from
            # main signing credentials when using OAuth login with scraped session creds)
            sig_string = "sessionstart"
            for key in sorted(params.keys()):
                sig_string += key + str(params[key])
            sig_string += request_ts + self._session_app_secret
            signature = hashlib.md5(sig_string.encode()).hexdigest()

            body = f"profile=qbz-1&request_ts={request_ts}&request_sig={signature}"
            url = f"{self.API_BASE}/session/start"

            # session/start is app-level — do NOT include X-User-Auth-Token.
            # Including a user token that was issued for a different app_id causes 401.
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://play.qobuz.com/",
                "Origin": "https://play.qobuz.com",
                "X-App-Id": self._session_app_id,
            }

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=body, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        response = await resp.json()
                        if "session_id" in response:
                            self.x_session_id = response["session_id"]
                            self.x_session_expires_at = response.get("expires_at", 0) * 1000
                            logger.debug("Session started")
                            return True
                        logger.debug(f"Session start: 200 but no session_id in response")
                    else:
                        body = await resp.text()
                        logger.debug(f"Session start failed: {resp.status} {body[:200]}")

        except Exception as e:
            logger.error(f"Failed to start session: {e}")

        return False

    async def get_track_url(self, track_id: str, quality: int = 27) -> Optional[dict[str, Any]]:
        """
        Get streaming URL and format info for a track.

        Args:
            track_id: Track ID
            quality: Audio quality (5, 6, 7, or 27)

        Returns:
            Dict with 'url', 'format_id', 'bit_depth', 'sampling_rate',
            'mime_type' keys, or None on failure
        """
        if not await self.start_session():
            logger.debug("Session start failed — proceeding without session ID")

        try:
            request_ts = f"{time.time():.6f}"
            # Use scraped web-player credentials for signing — OAUTH_APP_ID (desktop)
            # is not accepted for streaming endpoints. Falls back to app_id/app_secret
            # if scraped credentials weren't fetched (same values when no fallback set).
            sign_app_id = self._session_app_id
            sign_secret = self._session_app_secret
            logger.debug(
                f"getFileUrl signing with app_id={sign_app_id} "
                f"(oauth={self.app_id}, same={sign_app_id == self.app_id})"
            )

            # app_id goes in the header only, NOT the signed URL params —
            # matching the format used by test_secret() in credentials.py.
            sign_params = {
                "format_id": str(quality),
                "intent": "stream",
                "track_id": track_id,
            }

            sig_string = "trackgetFileUrl"
            for key in sorted(sign_params.keys()):
                sig_string += key + str(sign_params[key])
            sig_string += request_ts + sign_secret
            signature = hashlib.md5(sig_string.encode()).hexdigest()

            params = {
                **sign_params,
                "request_ts": request_ts,
                "request_sig": signature,
            }

            url = f"{self.API_BASE}/track/getFileUrl?{urlencode(params)}"
            headers = {
                "Referer": "https://play.qobuz.com/",
                "Origin": "https://play.qobuz.com",
                "X-App-Id": sign_app_id,
            }
            if self.user_auth_token:
                headers["X-User-Auth-Token"] = self.user_auth_token
            if self.x_session_id:
                headers["X-Session-Id"] = self.x_session_id

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        url_result = data.get("url")
                        if url_result:
                            return {
                                "url": url_result,
                                "format_id": data.get("format_id", quality),
                                "bit_depth": data.get("bit_depth", 0),
                                "sampling_rate": data.get("sampling_rate", 0),
                                "mime_type": data.get("mime_type", ""),
                            }
                        return None
                    else:
                        body = await resp.text()
                        logger.error(f"Failed to get track URL: {resp.status} {body[:300]}")

        except Exception as e:
            logger.error(f"Failed to get track URL: {e}")

        return None

    async def get_track_metadata(self, track_id: str) -> Optional[dict[str, Any]]:
        """
        Get track metadata.

        Args:
            track_id: Track ID

        Returns:
            Track metadata dict or None
        """
        try:
            params = {"track_id": track_id, "app_id": self.app_id}
            response = await self._request_signed("track", "get", params=params)

            if not response:
                return None

            # Transform to flat format
            metadata: dict[str, Any] = {
                "title": response.get("title", ""),
                "artist": "",
                "album": "",
                "album_art_url": "",
                "duration_ms": int(response.get("duration", 0)) * 1000,
            }

            performer = response.get("performer")
            if performer and isinstance(performer, dict):
                metadata["artist"] = performer.get("name", "")

            album = response.get("album")
            if album and isinstance(album, dict):
                metadata["album"] = album.get("title", "")
                image = album.get("image")
                if image and isinstance(image, dict):
                    metadata["album_art_url"] = image.get("large") or image.get("small") or ""

            return metadata

        except Exception as e:
            logger.error(f"Failed to get track metadata: {e}")

        return None

    async def _request_signed(
        self,
        obj: str,
        action: str,
        params: Optional[dict[str, Any]] = None,
        method: str = "GET",
        body: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Make a signed API request."""
        if params is None:
            params = {}

        request_ts = f"{time.time():.6f}"

        # Build signature
        sig_string = obj + action
        for key in sorted(params.keys()):
            sig_string += key + str(params[key])
        sig_string += request_ts + self.app_secret
        signature = hashlib.md5(sig_string.encode()).hexdigest()

        params["request_ts"] = request_ts
        params["request_sig"] = signature

        url = f"{self.API_BASE}/{obj}/{action}?{urlencode(params)}"

        try:
            session = self._session
            close_session = False
            if session is None:
                headers: dict[str, str] = {
                    "X-App-Id": self.app_id,
                    "User-Agent": "Mozilla/5.0",
                }
                if self.user_auth_token:
                    headers["X-User-Auth-Token"] = self.user_auth_token
                session = aiohttp.ClientSession(headers=headers)
                close_session = True

            timeout = aiohttp.ClientTimeout(total=10)
            try:
                if method == "POST":
                    async with session.post(url, data=body, timeout=timeout) as resp:
                        if resp.status == 200:
                            result: dict[str, Any] = await resp.json()
                            return result
                        else:
                            logger.debug(f"API request failed: {resp.status}")
                else:
                    async with session.get(url, timeout=timeout) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            return result
                        else:
                            logger.debug(f"API request failed: {resp.status}")
            finally:
                if close_session:
                    await session.close()

        except Exception as e:
            logger.error(f"API request error: {e}")

        return None
