"""Tests for QobuzProxy configuration system."""

import logging

import pytest

from qobuz_proxy.config import (
    Config,
    ConfigError,
    QobuzConfig,
    dict_to_config,
    validate_config,
)


class TestQobuzConfigFields:
    """Test QobuzConfig dataclass fields and defaults."""

    def test_auth_token_field_exists_with_default(self) -> None:
        qc = QobuzConfig()
        assert qc.auth_token == ""

    def test_user_id_field_exists_with_default(self) -> None:
        qc = QobuzConfig()
        assert qc.user_id == ""

    def test_email_field_exists_with_default(self) -> None:
        qc = QobuzConfig()
        assert qc.email == ""

    def test_max_quality_default(self) -> None:
        qc = QobuzConfig()
        assert qc.max_quality == 27

    def test_all_fields_assignable(self) -> None:
        qc = QobuzConfig(
            email="user@example.com",
            auth_token="my_token_123",
            user_id="12345",
            max_quality=6,
        )
        assert qc.email == "user@example.com"
        assert qc.auth_token == "my_token_123"
        assert qc.user_id == "12345"
        assert qc.max_quality == 6


class TestDictToConfigAuthToken:
    """Test dict_to_config handles auth_token and password alias."""

    def test_auth_token_from_dict(self) -> None:
        d = {"qobuz": {"auth_token": "token_abc"}}
        config = dict_to_config(d)
        assert config.qobuz.auth_token == "token_abc"

    def test_user_id_from_dict(self) -> None:
        d = {"qobuz": {"user_id": "99999"}}
        config = dict_to_config(d)
        assert config.qobuz.user_id == "99999"

    def test_password_alias_maps_to_auth_token(self) -> None:
        d = {"qobuz": {"password": "legacy_pass"}}
        config = dict_to_config(d)
        assert config.qobuz.auth_token == "legacy_pass"

    def test_password_alias_logs_deprecation_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        d = {"qobuz": {"password": "legacy_pass"}}
        with caplog.at_level(logging.WARNING, logger="qobuz_proxy.config"):
            dict_to_config(d)
        assert any("password" in rec.message.lower() and "deprecated" in rec.message.lower()
                    for rec in caplog.records)

    def test_auth_token_takes_precedence_over_password(self) -> None:
        d = {"qobuz": {"auth_token": "new_token", "password": "old_pass"}}
        config = dict_to_config(d)
        assert config.qobuz.auth_token == "new_token"

    def test_defaults_when_no_qobuz_section(self) -> None:
        config = dict_to_config({})
        assert config.qobuz.auth_token == ""
        assert config.qobuz.user_id == ""
        assert config.qobuz.email == ""


class TestValidateConfigWithoutCredentials:
    """Test that validation passes without email/password credentials."""

    def test_validation_passes_without_credentials(self) -> None:
        config = Config()
        config.backend.type = "stub"
        # Should NOT raise — credentials are no longer required
        validate_config(config)

    def test_validation_passes_with_empty_email(self) -> None:
        config = Config()
        config.backend.type = "stub"
        config.qobuz.email = ""
        validate_config(config)

    def test_validation_checks_email_format_when_provided(self) -> None:
        config = Config()
        config.backend.type = "stub"
        config.qobuz.email = "not-an-email"
        with pytest.raises(ConfigError, match="Invalid email format"):
            validate_config(config)

    def test_validation_passes_with_valid_email(self) -> None:
        config = Config()
        config.backend.type = "stub"
        config.qobuz.email = "user@example.com"
        validate_config(config)

    def test_validation_still_checks_quality(self) -> None:
        config = Config()
        config.backend.type = "stub"
        config.qobuz.max_quality = 99
        with pytest.raises(ConfigError, match="Invalid max_quality"):
            validate_config(config)

    def test_validation_still_checks_backend(self) -> None:
        config = Config()
        config.backend.type = "unknown_backend"
        with pytest.raises(ConfigError, match="Unknown backend type"):
            validate_config(config)

    def test_validation_still_checks_ports(self) -> None:
        config = Config()
        config.backend.type = "stub"
        config.server.http_port = 99999
        with pytest.raises(ConfigError, match="Invalid HTTP port"):
            validate_config(config)
