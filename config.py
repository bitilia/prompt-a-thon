import os
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy.pool import NullPool, StaticPool

BASE_DIR = Path(__file__).resolve().parent

def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default

def resolve_dir(base: Path, raw: str | None, default: str) -> str:
    value = default if raw is None or raw.strip() == "" else raw.strip()
    if os.path.isabs(value):
        return value
    return str(base / value)

class Base:

    APP_NAME = os.environ.get("APP_NAME", "prompt-a-thon")
    TAGLINE = os.environ.get("TAGLINE", "AI Literacy Competition")
    COLLEGE_NAME = os.environ.get("COLLEGE_NAME", "Your College")
    COLLEGE_URL = os.environ.get("COLLEGE_URL", "https://example.edu")
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "")
    BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")

    INSTANCE_SLUG = os.environ.get("INSTANCE_SLUG", "").strip()
    MANAGED_MODE = bool(INSTANCE_SLUG)
    TRUST_CLOUDFLARE = _env_bool("TRUST_CLOUDFLARE", False)

    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(64)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'instance' / 'prompt.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    if str(SQLALCHEMY_DATABASE_URI).startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS = {
            "poolclass": NullPool,
            "connect_args": {"check_same_thread": False, "timeout": 20},
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS = {
            "pool_pre_ping": True,
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 30,
            "pool_recycle": 1800,
        }

    @staticmethod
    def init_app(app):
        from sqlalchemy import event as _sa_event
        from sqlalchemy.engine import Engine as _Engine
        import sqlite3 as _sqlite3

        @_sa_event.listens_for(_Engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            if not isinstance(dbapi_conn, _sqlite3.Connection):
                return
            cursor = dbapi_conn.cursor()

            cursor.execute("PRAGMA journal_mode=WAL")

            cursor.execute("PRAGMA synchronous=NORMAL")

            cursor.execute("PRAGMA cache_size=-65536")

            cursor.execute("PRAGMA busy_timeout=60000")

            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()

    MAIL_PROVIDER = os.environ.get("MAIL_PROVIDER", "smtp").strip().lower()

    MAIL_FROM_ADDRESS = os.environ.get("MAIL_FROM_ADDRESS", "").strip()
    MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "").strip()

    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = _env_int("SMTP_PORT", 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "prompt-a-thon")
    SMTP_USE_TLS = _env_bool("SMTP_USE_TLS", True)

    BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")

    SES_ACCESS_KEY_ID = os.environ.get("SES_ACCESS_KEY_ID", "")
    SES_SECRET_ACCESS_KEY = os.environ.get("SES_SECRET_ACCESS_KEY", "")
    SES_REGION = os.environ.get("SES_REGION", "us-east-1")

    OTP_EXPIRY_MINUTES = _env_int("OTP_EXPIRY_MINUTES", 10)
    MAX_OTP_ATTEMPTS = _env_int("MAX_OTP_ATTEMPTS", 5)

    MICROSOFT_CLIENT_ID = os.environ.get("MICROSOFT_CLIENT_ID", "")
    MICROSOFT_CLIENT_SECRET = os.environ.get("MICROSOFT_CLIENT_SECRET", "")
    MICROSOFT_TENANT_ID = os.environ.get("MICROSOFT_TENANT_ID", "common")

    UPLOAD_FOLDER = resolve_dir(BASE_DIR, os.environ.get("UPLOAD_FOLDER"), "uploads")
    MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 25)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    ALLOWED_UPLOAD_EXTENSIONS = {"pdf", "docx"}

    TIMELINE_REGISTRATION_OPEN = os.environ.get("TIMELINE_REGISTRATION_OPEN", "")
    TIMELINE_REGISTRATION_CLOSE = os.environ.get("TIMELINE_REGISTRATION_CLOSE", "")
    TIMELINE_EVENT_DAY = os.environ.get("TIMELINE_EVENT_DAY", "")
    TIMELINE_SUBMISSION_DEADLINE = os.environ.get("TIMELINE_SUBMISSION_DEADLINE", "")
    TIMELINE_RESULTS = os.environ.get("TIMELINE_RESULTS", "")

    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    RATELIMIT_DEFAULT = "200 per day; 50 per hour"

    AUDIT_LOG_RETENTION_DAYS = _env_int("AUDIT_LOG_RETENTION_DAYS", 90)

    PROXY_FIX_X_FOR = _env_int("PROXY_FIX_X_FOR", 1)
    PROXY_FIX_X_PROTO = _env_int("PROXY_FIX_X_PROTO", 1)
    PROXY_FIX_X_HOST = _env_int("PROXY_FIX_X_HOST", 1)

class Development(Base):
    DEBUG = True
    TESTING = False
    SESSION_COOKIE_SECURE = False

class Production(Base):
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"

    @classmethod
    def validate(cls) -> None:

        missing = []
        if not os.environ.get("SECRET_KEY"):
            missing.append("SECRET_KEY")
        if not os.environ.get("ADMIN_EMAIL"):
            missing.append("ADMIN_EMAIL")
        provider = os.environ.get("MAIL_PROVIDER", "smtp").strip().lower()
        if provider == "brevo_api":
            if not os.environ.get("BREVO_API_KEY"):
                missing.append("BREVO_API_KEY")
            if not os.environ.get("MAIL_FROM_ADDRESS"):
                missing.append("MAIL_FROM_ADDRESS")
        elif provider == "ses_api":
            if not os.environ.get("SES_ACCESS_KEY_ID"):
                missing.append("SES_ACCESS_KEY_ID")
            if not os.environ.get("SES_SECRET_ACCESS_KEY"):
                missing.append("SES_SECRET_ACCESS_KEY")
            if not os.environ.get("MAIL_FROM_ADDRESS"):
                missing.append("MAIL_FROM_ADDRESS")
        elif provider == "smtp":
            if not os.environ.get("SMTP_USER"):
                missing.append("SMTP_USER")
            if not os.environ.get("SMTP_PASSWORD"):
                missing.append("SMTP_PASSWORD")
        elif provider == "postfix":
            pass
        else:
            raise RuntimeError(
                f"MAIL_PROVIDER must be 'smtp', 'brevo_api', 'ses_api', or 'postfix'; got {provider!r}"
            )
        if missing:
            raise RuntimeError(
                "Missing required production environment variables: "
                + ", ".join(missing)
            )

class Testing(Base):
    DEBUG = False
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False

CONFIG_MAP = {
    "development": Development,
    "production": Production,
    "testing": Testing,
}

def get_config():
    name = os.environ.get("FLASK_ENV", "development").strip().lower()
    return CONFIG_MAP.get(name, Development)
