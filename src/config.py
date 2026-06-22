"""Application configuration using Pydantic Settings.

Domain-specific sub-classes group related settings so each area of the codebase
imports only what it needs. The composed ``Settings`` class and the ``settings``
singleton expose everything at once for convenience.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class _Base(BaseSettings):
    """Shared env-file config inherited by all domain settings classes."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
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
    API_KEY_SECRET: str = "change-me-in-production-use-secrets"


class Settings(
    DatabaseSettings, RabbitMQSettings, OutboxSettings, ClinicalSettings, SecuritySettings
):
    """Composed application settings. All values can be overridden via env vars or .env."""

    DEBUG: bool = False
    APP_NAME: str = "Medical Device Data Aggregation API"


settings = Settings()
