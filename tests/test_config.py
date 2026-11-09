import config
from config import Config, build_engine_options, normalize_database_url


def test_postgres_urls_use_psycopg_driver():
    assert normalize_database_url("postgresql://u:p@h:6543/postgres") == "postgresql+psycopg://u:p@h:6543/postgres"
    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_database_url("sqlite:///x.db") == "sqlite:///x.db"
    assert normalize_database_url("") == "" and normalize_database_url(None) == ""


def test_supabase_pooler_engine_options():
    options = build_engine_options("postgresql+psycopg://postgres.abc:pw@aws-0-eu.pooler.supabase.com:6543/postgres")
    assert options["pool_pre_ping"] is True
    assert options["pool_size"] <= 5 and options["pool_recycle"] <= 600
    connect_args = options["connect_args"]
    assert connect_args["prepare_threshold"] is None  # no server-side prepared statements behind PgBouncer
    assert connect_args["sslmode"] == "require"


def test_explicit_sslmode_in_url_is_respected():
    options = build_engine_options("postgresql+psycopg://u:p@h/db?sslmode=verify-full")
    assert "sslmode" not in options["connect_args"]


def test_sqlite_gets_no_pool_options():
    assert build_engine_options("sqlite:///nexus.db") == {}


def test_database_url_env_is_read(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:6543/postgres")
    import importlib

    reloaded = importlib.reload(config)
    try:
        assert reloaded.Config.SQLALCHEMY_DATABASE_URI.startswith("postgresql+psycopg://")
        assert reloaded.Config.SQLALCHEMY_ENGINE_OPTIONS["pool_pre_ping"] is True
    finally:
        monkeypatch.delenv("DATABASE_URL")
        importlib.reload(config)


def test_postgres_engine_can_be_created_without_connecting():
    """The pooler options must be accepted by SQLAlchemy and psycopg (engine creation is lazy)."""
    from sqlalchemy import create_engine

    url = "postgresql+psycopg://postgres.abc:pw@127.0.0.1:6543/postgres"
    engine = create_engine(url, **build_engine_options(url))
    assert engine.dialect.name == "postgresql" and engine.pool.size() == 5


def test_secure_cookie_defaults():
    assert Config.SESSION_COOKIE_HTTPONLY and Config.SESSION_COOKIE_SAMESITE == "Lax"
    assert Config.REMEMBER_COOKIE_HTTPONLY
