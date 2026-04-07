"""
QobuzProxy Application.

Thin orchestrator that authenticates once then starts one Speaker per configured device.
"""

import asyncio
import logging
import signal
from typing import Optional

from qobuz_proxy.config import Config
from qobuz_proxy.auth import QobuzAPIClient, AuthenticationError, auto_fetch_credentials
from qobuz_proxy.speaker import Speaker

logger = logging.getLogger(__name__)


class QobuzProxy:
    """
    Main QobuzProxy application.

    Authenticates with Qobuz once (shared credentials), then creates and starts
    one Speaker per entry in config.speakers. Each Speaker manages its own
    discovery, WebSocket, player, and audio backend lifecycle.

    Usage:
        config = load_config(...)
        app = QobuzProxy(config)
        await app.run()
    """

    def __init__(self, config: Config):
        self._config = config
        self._is_running = False
        self._shutdown_event = asyncio.Event()
        self._api_client: Optional[QobuzAPIClient] = None
        self._app_id: str = ""
        self._app_secret: str = ""
        self._speakers: list[Speaker] = []

    async def start(self) -> None:
        """
        Start QobuzProxy.

        1. Fetch shared Qobuz app credentials
        2. Create and authenticate shared QobuzAPIClient
        3. Create Speaker instances from config.speakers
        4. Start all speakers concurrently; keep only the ones that succeed
        5. Raise RuntimeError if no speakers started

        Raises:
            AuthenticationError: If credential fetch or login fails
            RuntimeError: If all speakers fail to start
        """
        logger.info("Starting QobuzProxy...")

        # 1. Fetch app credentials (shared across all speakers)
        logger.info("Fetching Qobuz app credentials...")
        credentials = await auto_fetch_credentials()
        if not credentials:
            raise AuthenticationError("Failed to fetch Qobuz app credentials")

        self._app_id = credentials["app_id"]
        self._app_secret = credentials["app_secret"]
        logger.debug(f"Got app_id: {self._app_id}")

        # 2. Create shared API client and authenticate
        self._api_client = QobuzAPIClient(self._app_id, self._app_secret)
        logger.info(f"Authenticating as user {self._config.qobuz.user_id}...")
        if not await self._api_client.login_with_token(
            user_id=self._config.qobuz.user_id,
            auth_token=self._config.qobuz.auth_token,
        ):
            raise AuthenticationError("Qobuz login failed - check credentials")
        logger.info("Authentication successful")

        # 3. Create Speaker instances
        speakers = [
            Speaker(config=sc, api_client=self._api_client, app_id=self._app_id)
            for sc in self._config.speakers
        ]

        # 4. Start all speakers concurrently
        results = await asyncio.gather(*[s.start() for s in speakers], return_exceptions=True)

        started: list[Speaker] = []
        for speaker, result in zip(speakers, results):
            if isinstance(result, Exception):
                logger.warning(f"Speaker '{speaker.name}' failed to start: {result}")
            elif result is False:
                logger.warning(f"Speaker '{speaker.name}' failed to start")
            else:
                started.append(speaker)

        # 5. Require at least one speaker
        if not started:
            raise RuntimeError("No speakers started successfully — check configuration and logs")

        self._speakers = started
        self._is_running = True

        names = ", ".join(s.name for s in self._speakers)
        logger.info(f"QobuzProxy ready — {len(self._speakers)} speaker(s): {names}")

    async def stop(self) -> None:
        """Stop QobuzProxy and all running speakers."""
        if not self._is_running:
            return

        self._is_running = False
        await asyncio.gather(*[s.stop() for s in self._speakers], return_exceptions=True)
        logger.info("QobuzProxy stopped")

    async def run(self) -> None:
        """
        Run QobuzProxy until interrupted.

        Sets up signal handlers for graceful shutdown on SIGINT/SIGTERM.
        """
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
        """Return True if at least one speaker is running."""
        return self._is_running
