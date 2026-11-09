"""Central application configuration. Every setting comes from environment variables."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _bool(name, default=False):
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name, default):
    value = os.getenv(name, "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        return default


def normalize_database_url(url):
    """Return a SQLAlchemy URL that uses the psycopg (v3) driver for PostgreSQL."""
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def build_engine_options(database_url):
    """Engine settings that are safe for Supabase's transaction pooler (PgBouncer)."""
    if not database_url.startswith("postgresql"):
        return {}
    options = {
        "pool_size": _int("DB_POOL_SIZE", 5),
        "max_overflow": _int("DB_MAX_OVERFLOW", 5),
        "pool_timeout": 30,
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "connect_args": {
            # Transaction pooling cannot keep server-side prepared statements.
            "prepare_threshold": None,
            "connect_timeout": 10,
        },
    }
    if "sslmode=" not in database_url:
        options["connect_args"]["sslmode"] = os.getenv("DB_SSLMODE", "require")
    return options


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    DEBUG = _bool("FLASK_DEBUG", False)
    TESTING = False

    SECRET_KEY = os.getenv("SECRET_KEY", "").strip()

    SQLALCHEMY_DATABASE_URI = normalize_database_url(os.getenv("DATABASE_URL")) or "sqlite:///nexus.db"
    SQLALCHEMY_ENGINE_OPTIONS = build_engine_options(SQLALCHEMY_DATABASE_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Uploads
    UPLOAD_FOLDER = str(BASE_DIR / "uploads")
    MAX_AUDIO_MB = _int("MAX_AUDIO_MB", 100)
    MAX_CONTENT_LENGTH = (MAX_AUDIO_MB + 2) * 1024 * 1024
    MAX_FORM_MEMORY_SIZE = 2 * 1024 * 1024  # Werkzeug default (500 kB) is too small for long transcripts
    MAX_TRANSCRIPT_CHARS = _int("MAX_TRANSCRIPT_CHARS", 200_000)
    MIN_TRANSCRIPT_CHARS = 20
    MAX_CHAT_MESSAGE_CHARS = 2000

    # Sessions and cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV == "production"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = APP_ENV == "production"
    WTF_CSRF_TIME_LIMIT = None

    # Gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash-lite").strip()
    GEMINI_AUDIO_MODEL = os.getenv("GEMINI_AUDIO_MODEL", "gemini-3.5-flash-lite").strip()
    GEMINI_TIMEOUT_SECONDS = _int("GEMINI_TIMEOUT_SECONDS", 120)
    GEMINI_AUDIO_TIMEOUT_SECONDS = _int("GEMINI_AUDIO_TIMEOUT_SECONDS", 600)
    GEMINI_INLINE_AUDIO_MAX_MB = 15

    # Mail and reminders
    MAIL_SERVER = os.getenv("MAIL_SERVER", "").strip()
    MAIL_PORT = _int("MAIL_PORT", 587)
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", True)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "").strip()
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "").strip()
    REMINDER_INTERVAL_MINUTES = max(1, _int("REMINDER_INTERVAL_MINUTES", 30))
    SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)

    # Pagination
    TASKS_PER_PAGE = 25


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-production"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    SCHEDULER_ENABLED = False
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    GEMINI_API_KEY = "test-key"
    GEMINI_TEXT_MODEL = "test-text-model"
    GEMINI_AUDIO_MODEL = "test-audio-model"
    MAIL_SERVER = ""
