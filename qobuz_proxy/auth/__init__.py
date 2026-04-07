"""
Qobuz authentication module.

Handles credential scraping, API authentication, and token management.
"""

from .api_client import QobuzAPIClient, QobuzAPIError
from .credentials import (
    auto_fetch_credentials,
    clear_user_token,
    load_cached_credentials,
    load_user_token,
    save_credentials_to_cache,
    save_user_token,
)
from .exceptions import AuthenticationError
from .tokens import QobuzToken, WSToken

__all__ = [
    "auto_fetch_credentials",
    "clear_user_token",
    "load_cached_credentials",
    "load_user_token",
    "save_credentials_to_cache",
    "save_user_token",
    "QobuzAPIClient",
    "QobuzAPIError",
    "AuthenticationError",
    "QobuzToken",
    "WSToken",
]
