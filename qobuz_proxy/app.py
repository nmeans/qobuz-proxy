"""
QobuzProxy Application.

Orchestrates authentication, the web UI, and per-speaker lifecycle.
The HTTP server starts first so users can submit credentials through the
web UI even before a valid Qobuz token is available.
"""

import asyncio
import logging
import signal
from typing import Optional

from aiohttp import web

from qobuz_proxy import __version__
from qobuz_proxy.auth import (
    QobuzAPIClient,
    auto_fetch_credentials,
    clear_user_token,
    load_user_token,
    save_user_token,
)
from qobuz_proxy.config import Config
from qobuz_proxy.speaker import Speaker
from qobuz_proxy.webui.routes import register_routes

logger = logging.getLogger(__name__)


class QobuzProxy:
    """
    Main QobuzProxy application.

    Starts the shared HTTP server (web UI + discovery routes) first, then
    attempts automatic authentication from config or cached tokens. If no
    valid credentials are available the app stays running in a
    "waiting-for-auth" state so the user can provide a token through the
    web UI.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._is_running = False
        self._shutdown_event = asyncio.Event()

        # Auth / API
        self._api_client: Optional[QobuzAPIClient] = None
        self._app_id: str = ""
        self._app_secret: str = ""

        # Auth state — shared with the web UI status endpoint via _web_app["auth_state"].
        # Always the *same* dict object so route handlers see live updates.
        self._auth_state: dict[str, object] = {
            "authenticated": False,
            "user_id": "",
            "email": "",
        }

        # Shared aiohttp application (web UI + per-speaker discovery routes)
        self._web_app: Optional[web.Application] = None
        self._web_runner: Optional[web.AppRunner] = None
        self._web_site: Optional[web.TCPSite] = None

        # Speakers
        self._speakers: list[Speaker] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start web server, fetch app credentials, attempt auto-auth."""
        logger.info("Starting QobuzProxy...")

        # 1. Start the HTTP server so the web UI is reachable immediately
        await self._start_web_server()

        # 2. Fetch Qobuz app credentials (app_id / app_secret)
        logger.info("Fetching Qobuz app credentials...")
        credentials = await auto_fetch_credentials()
        if not credentials:
            logger.warning(
                "Failed to fetch Qobuz app credentials — " "will retry when user submits a token"
            )
        else:
            self._app_id = credentials["app_id"]
            self._app_secret = credentials["app_secret"]
            logger.debug(f"Got app_id: {self._app_id}")

        # 3. Attempt auto-auth from config or cache
        token_info = self._get_token_from_config_or_cache()
        if token_info and self._app_id:
            user_id = token_info["user_id"]
            auth_token = token_info["user_auth_token"]
            email = token_info.get("email", "")

            if await self._authenticate(user_id, auth_token):
                self._auth_state["user_id"] = user_id
                self._auth_state["email"] = email
                self._auth_state["authenticated"] = True
                await self._start_speakers()
            else:
                logger.warning("Cached/config token is invalid — waiting for auth via web UI")

        if not self._auth_state["authenticated"]:
            port = self._config.server.http_port
            logger.info(f"No valid credentials — visit http://localhost:{port} to authenticate")

        self._is_running = True

    async def stop(self) -> None:
        """Stop speakers, then the web server."""
        if not self._is_running:
            return

        self._is_running = False
        await self._stop_speakers()
        await self._stop_web_server()
        logger.info("QobuzProxy stopped")

    async def run(self) -> None:
        """Run until SIGINT / SIGTERM."""
        loop = asyncio.get_running_loop()

        def handle_signal() -> None:
            logger.info("Shutdown signal received")
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, handle_signal)

        try:
            await self.start()
            await self._shutdown_event.wait()
        finally:
            await self.stop()

    @property
    def is_running(self) -> bool:
        """Return True if the application event loop is active."""
        return self._is_running

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def _get_token_from_config_or_cache(self) -> Optional[dict[str, str]]:
        """Return user credentials from config (highest priority) or cache."""
        # Config values take precedence
        if self._config.qobuz.auth_token and self._config.qobuz.user_id:
            return {
                "user_id": self._config.qobuz.user_id,
                "user_auth_token": self._config.qobuz.auth_token,
                "email": self._config.qobuz.email,
            }

        # Fall back to cached token
        cached = load_user_token()
        if cached:
            logger.info("Found cached user token")
            return cached

        return None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self, user_id: str, auth_token: str) -> bool:
        """Validate credentials against the Qobuz API. Returns True on success."""
        if not self._app_id:
            logger.error("Cannot authenticate — app credentials not available")
            return False

        self._api_client = QobuzAPIClient(self._app_id, self._app_secret)
        logger.info(f"Authenticating user {user_id}...")
        if await self._api_client.login_with_token(user_id=user_id, auth_token=auth_token):
            logger.info("Authentication successful")
            return True

        logger.warning("Authentication failed — invalid credentials")
        self._api_client = None
        return False

    # ------------------------------------------------------------------
    # Web UI callbacks
    # ------------------------------------------------------------------

    async def _on_auth_token(self, user_id: str, auth_token: str) -> bool:
        """Called by the web UI when the user submits a token.

        Validates credentials, persists them to cache, and starts speakers
        if they are not already running.
        """
        # Ensure app credentials are available
        if not self._app_id:
            credentials = await auto_fetch_credentials()
            if not credentials:
                logger.error("Cannot validate token — app credentials unavailable")
                return False
            self._app_id = credentials["app_id"]
            self._app_secret = credentials["app_secret"]

        if not await self._authenticate(user_id, auth_token):
            return False

        # Persist to cache
        save_user_token(user_id=user_id, auth_token=auth_token, email="")

        # Update shared auth state
        self._auth_state["authenticated"] = True
        self._auth_state["user_id"] = user_id
        self._auth_state["email"] = ""

        # Start speakers if not already running
        if not self._speakers:
            await self._start_speakers()

        return True

    async def _on_logout(self) -> None:
        """Called by the web UI when the user requests logout."""
        logger.info("Logout requested — stopping speakers and clearing token")
        await self._stop_speakers()

        self._auth_state["authenticated"] = False
        self._auth_state["user_id"] = ""
        self._auth_state["email"] = ""
        self._api_client = None

        clear_user_token()

    # ------------------------------------------------------------------
    # Web server
    # ------------------------------------------------------------------

    async def _start_web_server(self) -> None:
        """Create the shared aiohttp app and start listening."""
        self._web_app = web.Application()

        # Expose state for route handlers
        self._web_app["auth_state"] = self._auth_state
        self._web_app["speakers"] = []
        self._web_app["version"] = __version__
        self._web_app["on_auth_token"] = self._on_auth_token
        self._web_app["on_logout"] = self._on_logout

        register_routes(self._web_app)

        self._web_runner = web.AppRunner(self._web_app)
        await self._web_runner.setup()
        self._web_site = web.TCPSite(
            self._web_runner,
            self._config.server.bind_address,
            self._config.server.http_port,
        )
        await self._web_site.start()
        logger.info(
            f"Web server listening on "
            f"{self._config.server.bind_address}:{self._config.server.http_port}"
        )

    async def _stop_web_server(self) -> None:
        """Shut down the shared aiohttp app."""
        if self._web_site:
            await self._web_site.stop()
        if self._web_runner:
            await self._web_runner.cleanup()

    # ------------------------------------------------------------------
    # Speaker lifecycle
    # ------------------------------------------------------------------

    async def _start_speakers(self) -> None:
        """Create and start Speaker instances from config."""
        assert self._api_client is not None

        speakers = [
            Speaker(
                config=sc,
                api_client=self._api_client,
                app_id=self._app_id,
                web_app=self._web_app,
            )
            for sc in self._config.speakers
        ]

        results = await asyncio.gather(*[s.start() for s in speakers], return_exceptions=True)

        started: list[Speaker] = []
        for speaker, result in zip(speakers, results):
            if isinstance(result, Exception):
                logger.warning(f"Speaker '{speaker.name}' failed to start: {result}")
            elif result is False:
                logger.warning(f"Speaker '{speaker.name}' failed to start")
            else:
                started.append(speaker)

        if not started:
            logger.error("No speakers started successfully — check configuration and logs")
            return

        self._speakers = started

        # Update web UI speaker list
        if self._web_app is not None:
            self._web_app["speakers"] = [
                {"name": s.name, "status": "running"} for s in self._speakers
            ]

        names = ", ".join(s.name for s in self._speakers)
        logger.info(f"QobuzProxy ready — {len(self._speakers)} speaker(s): {names}")

    async def _stop_speakers(self) -> None:
        """Stop all running speakers."""
        if self._speakers:
            await asyncio.gather(*[s.stop() for s in self._speakers], return_exceptions=True)
            self._speakers = []

        if self._web_app is not None:
            self._web_app["speakers"] = []
