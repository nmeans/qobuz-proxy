"""Qobuz OAuth authentication.

Implements the OAuth flow used by the Qobuz desktop app:
1. Redirect user to Qobuz sign-in page with a callback URL
2. Qobuz redirects back with a ``code_autorisation`` query parameter
3. Exchange that code for a user auth token via the API
"""

import logging
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp

logger = logging.getLogger(__name__)

# Qobuz desktop app credentials (public, used by all OAuth clients)
OAUTH_APP_ID = "304027809"
OAUTH_PRIVATE_KEY = "6lz8C03UDIC7"  # OAuth code exchange key only
OAUTH_APP_SECRET = "2a938b87c2ee98337e60f0b5453a65a7"  # API request signing secret
QOBUZ_API_BASE = "https://www.qobuz.com/api.json/0.2"
QOBUZ_OAUTH_URL = "https://www.qobuz.com/signin/oauth"


def build_oauth_url(redirect_url: str) -> str:
    """Build the Qobuz OAuth URL that the user should be redirected to."""
    params = urlencode({"ext_app_id": OAUTH_APP_ID, "redirect_url": redirect_url})
    return f"{QOBUZ_OAUTH_URL}?{params}"


def extract_code(url: str) -> str:
    """Extract ``code_autorisation`` from a callback URL.

    Raises ValueError if the parameter is missing.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    codes = params.get("code_autorisation", [])
    if not codes:
        raise ValueError(f"No code_autorisation found in URL: {url}")
    return codes[0]


async def exchange_code(code: str, web_app_id: str = "") -> dict[str, str]:
    """Exchange an OAuth authorization code for user credentials.

    Args:
        code: The ``code_autorisation`` received from the OAuth callback.
        web_app_id: If provided, try this app ID first for the ``user/login``
            validation step.  The web-player app ID (scraped at startup) is
            the only one accepted by ``track/getFileUrl``, so passing it here
            means the returned ``user_auth_token`` will already be scoped to
            that app and can be used directly with web-player signing.

    Returns a dict with ``user_id``, ``user_auth_token``, ``display_name``,
    ``email``, and ``token_app_id`` keys.  ``token_app_id`` is the app ID
    that was actually used for the ``user/login`` step.
    """
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Step 1: exchange code for raw OAuth token
        async with session.get(
            f"{QOBUZ_API_BASE}/oauth/callback",
            params={"code": code, "private_key": OAUTH_PRIVATE_KEY},
            headers={"X-App-Id": OAUTH_APP_ID},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Token exchange failed ({resp.status}): {text}")
            data = await resp.json()

        token = data["token"]
        user_id = str(data["user_id"])

        # Step 2: validate token and fetch profile.
        # Try the web-player app ID first (if provided) — a token scoped to
        # the web-player app can be used with MD5 signing for getFileUrl.
        # Fall back to the OAuth app ID if the web-player login fails.
        app_ids_to_try = []
        if web_app_id and web_app_id != OAUTH_APP_ID:
            app_ids_to_try.append(web_app_id)
        app_ids_to_try.append(OAUTH_APP_ID)

        profile_data = None
        token_app_id = OAUTH_APP_ID
        for try_app_id in app_ids_to_try:
            async with session.post(
                f"{QOBUZ_API_BASE}/user/login",
                data="extra=partner",
                headers={
                    "X-App-Id": try_app_id,
                    "X-User-Auth-Token": token,
                    "Content-Type": "text/plain;charset=UTF-8",
                },
            ) as resp:
                if resp.status == 200:
                    profile_data = await resp.json()
                    token_app_id = try_app_id
                    logger.debug(f"user/login succeeded with app_id={try_app_id}")
                    break
                else:
                    logger.debug(
                        f"user/login with app_id={try_app_id} failed ({resp.status})"
                    )

        if profile_data is None:
            raise RuntimeError("Login validation failed for all app IDs")

    user = profile_data.get("user", {})
    avatar = user.get("avatar", "")
    if isinstance(avatar, dict):
        avatar = avatar.get("url", "") or avatar.get("large", "") or ""

    session_token = profile_data.get("user_auth_token") or token

    return {
        "user_id": user_id,
        "user_auth_token": session_token,
        "display_name": user.get("display_name", ""),
        "email": user.get("email", ""),
        "avatar": avatar,
        "token_app_id": token_app_id,
    }
