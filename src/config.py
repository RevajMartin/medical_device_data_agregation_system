"""Application configuration using Pydantic Settings.

Domain-specific sub-classes group related settings so each area of the codebase
imports only what it needs. The composed ``Settings`` class and the ``settings``
singleton expose everything at once for convenience.
"""

import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Dev secrets shipped as the field defaults (also what .env.example / .env contain). Fine
# for local dev, but the validator refuses to start with them when DEBUG is off (production),
# so the publicly-known shipped secrets can never be deployed unchanged.
DEV_API_KEY_SECRET = "dev-signing-secret"
DEV_ADMIN_API_TOKEN = "dev-admin-token"


class _Base(BaseSettings):
    """Shared env-file config inherited by all domain settings classes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignore unrelated env vars / .env keys (infra config like POSTGRES_*, RABBITMQ_*,
        # API_PORT is for docker-compose, not the app). Reading the .env file otherwise
        # rejects them as extra inputs -- while env vars in a container are read per-field.
        extra="ignore",
    )


class DatabaseSettings(_Base):
    """PostgreSQL connection and connection-pool tuning."""

    DATABASE_URL: str = "postgresql+asyncpg://user:pass@db:5432/medical_data"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30


class RabbitMQSettings(_Base):
    """RabbitMQ connection. The queue/exchange topology lives in src/messaging.py."""

    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672//"


class OutboxSettings(_Base):
    """Transactional outbox relay tuning."""

    OUTBOX_POLL_INTERVAL: float = 5.0  # seconds between fallback polls
    OUTBOX_BATCH_SIZE: int = 100  # max rows per relay pass


class ClinicalSettings(_Base):
    """Physiological validation bounds, clinical alert thresholds, risk-scoring params.

    Validation bounds (HR_MIN..SPO2_MAX): hard limits enforced by Pydantic → HTTP 422.
    Used as Field(ge/le) constraints in src/schemas/measurement.py.

    Alert thresholds (HR_ALERT_MAX etc.): values are valid but clinically concerning
    → background alert record. Distinct from the validation bounds above.
    """

    # Validation bounds
    HR_MIN: int = 30
    HR_MAX: int = 250
    SYSTOLIC_MIN: int = 60
    SYSTOLIC_MAX: int = 250
    DIASTOLIC_MIN: int = 40
    DIASTOLIC_MAX: int = 150
    SPO2_MIN: float = 50.0
    SPO2_MAX: float = 100.0

    # Reject measurement timestamps more than this far in the future (clock-skew tolerance).
    MAX_FUTURE_SKEW_SECONDS: int = 60

    # Clinical alert thresholds
    HR_ALERT_MAX: int = 150
    SPO2_ALERT_MIN: float = 90.0
    SYSTOLIC_ALERT_MAX: int = 180

    # Risk-scoring feature window
    RISK_WINDOW_HOURS: int = 24
    RISK_MAX_SAMPLES: int = 200


class SecuritySettings(_Base):
    """API key generation and signing."""

    API_KEY_LENGTH: int = 32
    # HMAC signing secret for API key hashes stored in the DB.
    # Without this secret an attacker who leaks the devices table cannot verify
    # any key offline. MUST be overridden in production via env var or .env.
    API_KEY_SECRET: str = DEV_API_KEY_SECRET

    # Operator token for the admin-scoped routes (/admin/*, /devices/register). Distinct
    # from device API keys on purpose: a leaked device key must never grant registration
    # or dead-letter access. MUST be overridden in production via env var or .env.
    ADMIN_API_TOKEN: str = DEV_ADMIN_API_TOKEN


class Settings(
    DatabaseSettings, RabbitMQSettings, OutboxSettings, ClinicalSettings, SecuritySettings
):
    """Composed application settings. All values can be overridden via env vars or .env."""

    DEBUG: bool = False
    APP_NAME: str = "Medical Device Data Aggregation API"

    @model_validator(mode="after")
    def _reject_shipped_dev_secrets(self) -> "Settings":
        """In production, refuse to start with the shipped dev secrets.

        Only enforced when ``DEBUG`` is false (production): the dev defaults shipped in
        ``.env.example`` are convenient locally (DEBUG=true), but are publicly known and must
        never run in a real deployment. A prod deploy (``DEBUG`` unset -> false) that forgets
        to override them fails fast instead of running with a known secret. Set real values
        via env vars / a secret manager.
        """
        if self.DEBUG:
            return self
        insecure = [
            name
            for name, shipped in (
                ("API_KEY_SECRET", DEV_API_KEY_SECRET),
                ("ADMIN_API_TOKEN", DEV_ADMIN_API_TOKEN),
            )
            if getattr(self, name) == shipped
        ]
        if insecure:
            raise ValueError(
                "Refusing to start in production (DEBUG=false) with the shipped dev secret(s): "
                + ", ".join(insecure)
                + ". Set real values via env vars / a secret manager."
            )
        return self


settings = Settings()


def warn_if_debug() -> None:
    """Log a prominent startup warning when the API runs in DEBUG (development) mode.

    Called by the API at startup (it is the service that serves HTTP and can leak error
    detail), so the logs make it obvious that verbose logging and detailed error responses
    are enabled (à la Flask's dev-server warning). DEBUG must never be enabled in production.
    """
    if settings.DEBUG:
        logging.getLogger("medical_data").warning(
            "DEBUG mode is ON -- development mode: verbose logging, detailed error responses, "
            "and the production secret guard is relaxed (shipped dev secrets allowed). Do NOT "
            "set DEBUG=true in production."
        )
