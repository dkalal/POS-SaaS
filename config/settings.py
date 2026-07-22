from pathlib import Path
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

from config.compat import patch_django_context_copy_for_python_314

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
patch_django_context_copy_for_python_314()


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"{name} must be a boolean value.")


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_secret(name, default=""):
    """Load a secret from NAME or a Docker/Kubernetes-style NAME_FILE."""
    file_path = os.getenv(f"{name}_FILE", "").strip()
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ImproperlyConfigured(f"Unable to read {name}_FILE.") from exc
    return os.getenv(name, default).strip()


ENVIRONMENT = os.getenv("DJANGO_ENVIRONMENT", "development").strip().lower()
if ENVIRONMENT not in {"development", "test", "staging", "production"}:
    raise ImproperlyConfigured(
        "DJANGO_ENVIRONMENT must be development, test, staging, or production."
    )
IS_PRODUCTION = ENVIRONMENT in {"staging", "production"}
SECRET_KEY = env_secret("DJANGO_SECRET_KEY", "dev-secret-key")
SECRET_KEY_FALLBACKS = env_list("DJANGO_SECRET_KEY_FALLBACKS")
AUDIT_LOG_HMAC_KEY = env_secret("DJANGO_AUDIT_LOG_HMAC_KEY", SECRET_KEY) or SECRET_KEY
AUDIT_LOG_HMAC_KEY_FALLBACKS = env_list("DJANGO_AUDIT_LOG_HMAC_KEY_FALLBACKS")
DEBUG = env_bool("DJANGO_DEBUG", not IS_PRODUCTION)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
PUBLIC_BASE_URL = os.getenv("DJANGO_PUBLIC_BASE_URL", "").strip().rstrip("/")
EMAIL_BACKEND = os.getenv("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_secret("DJANGO_EMAIL_HOST_PASSWORD")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = int(os.getenv("DJANGO_EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv("DJANGO_DEFAULT_FROM_EMAIL", "POS SaaS <no-reply@pos-saas.local>")

SENDGRID_API_KEY = env_secret("SENDGRID_API_KEY")
if SENDGRID_API_KEY:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.getenv("SENDGRID_EMAIL_HOST", "smtp.sendgrid.net")
    EMAIL_PORT = int(os.getenv("SENDGRID_EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.getenv("SENDGRID_EMAIL_HOST_USER", "apikey")
    EMAIL_HOST_PASSWORD = SENDGRID_API_KEY
    EMAIL_USE_TLS = env_bool("SENDGRID_EMAIL_USE_TLS", True)

# Verification must never be enforced merely because a backend prints or stores mail locally.
# Custom production backends can opt in explicitly after delivery is verified end to end.
OUTBOUND_EMAIL_ENABLED = env_bool(
    "DJANGO_OUTBOUND_EMAIL_ENABLED",
    EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend",
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "tenants",
    "accounts",
    "catalog",
    "suppliers",
    "purchasing",
    "inventory",
    "sales",
    "payments",
    "api",
    "dashboard",
    "reports",
    "audit",
    "platform_admin",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "core.middleware.RequestLoggingMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.AuthenticatedResponseCacheControlMiddleware",
    "core.middleware.CurrentTenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.session_identity",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

database_url = env_secret("DATABASE_URL")
if database_url:
    parsed = urlparse(database_url)
    database_options = {
        key: values[-1]
        for key, values in parse_qs(parsed.query).items()
        if key in {"sslmode", "sslrootcert", "sslcert", "sslkey"}
    }
    if IS_PRODUCTION and env_bool("DJANGO_DATABASE_SSL_REQUIRE", True):
        database_options.setdefault("sslmode", "require")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "127.0.0.1",
            "PORT": str(parsed.port or 5432),
            "CONN_MAX_AGE": int(os.getenv("DJANGO_DATABASE_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": database_options,
        }
    }
    connect_timeout = int(os.getenv("DJANGO_DATABASE_CONNECT_TIMEOUT", "10"))
    statement_timeout = int(os.getenv("DJANGO_DATABASE_STATEMENT_TIMEOUT_MS", "0"))
    lock_timeout = int(os.getenv("DJANGO_DATABASE_LOCK_TIMEOUT_MS", "0"))
    DATABASES["default"]["OPTIONS"].setdefault("connect_timeout", connect_timeout)
    timeout_options = []
    if statement_timeout > 0:
        timeout_options.append(f"-c statement_timeout={statement_timeout}")
    if lock_timeout > 0:
        timeout_options.append(f"-c lock_timeout={lock_timeout}")
    if timeout_options:
        DATABASES["default"]["OPTIONS"].setdefault("options", " ".join(timeout_options))
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = env_bool(
        "DJANGO_DATABASE_DISABLE_SERVER_SIDE_CURSORS", False
    )
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

test_database_name = os.getenv("DJANGO_TEST_DATABASE_NAME", "").strip()
if test_database_name:
    DATABASES["default"]["TEST"] = {"NAME": test_database_name}

cache_url = env_secret("DJANGO_CACHE_URL")
if cache_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": cache_url,
            "KEY_PREFIX": os.getenv("DJANGO_CACHE_KEY_PREFIX", "pos_saas"),
            "TIMEOUT": 300,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pos-saas-development",
        }
    }

AUTH_PASSWORD_VALIDATORS = [] if env_bool("DJANGO_DISABLE_PASSWORD_VALIDATORS", False) else [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTHENTICATION_BACKENDS = [
    "accounts.auth_backends.EmailOrUsernameModelBackend",
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Dar_es_Salaam"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", IS_PRODUCTION)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("DJANGO_SESSION_EXPIRE_AT_BROWSER_CLOSE", True)
SESSION_COOKIE_AGE = int(os.getenv("DJANGO_SESSION_COOKIE_AGE", "28800"))
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", IS_PRODUCTION)
CSRF_FAILURE_VIEW = "config.views.csrf_failure"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", IS_PRODUCTION)
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "31536000" if IS_PRODUCTION else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
X_FRAME_OPTIONS = "DENY"

# Only enable forwarded HTTPS handling when the named reverse proxy is known to
# strip and replace X-Forwarded-Proto. Never trust this header from the public web.
if env_bool("DJANGO_TRUST_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", False)
TRUSTED_PROXY_IPS = env_list("DJANGO_TRUSTED_PROXY_IPS")
TRUSTED_PROXY_COUNT = int(os.getenv("DJANGO_TRUSTED_PROXY_COUNT", "1"))

RATE_LIMITS = {
    "login_ip": os.getenv("DJANGO_RATE_LOGIN_IP", "20/15m"),
    "login_identity": os.getenv("DJANGO_RATE_LOGIN_IDENTITY", "5/15m"),
    "signup": os.getenv("DJANGO_RATE_SIGNUP", "10/h"),
    "invitation": os.getenv("DJANGO_RATE_INVITATION", "20/h"),
    "invitation_token": os.getenv("DJANGO_RATE_INVITATION_TOKEN", "10/15m"),
    "verification_resend": os.getenv("DJANGO_RATE_VERIFICATION_RESEND", "3/h"),
}

LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "INFO").strip().upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "core.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "loggers": {
        "django.request": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.security": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "pos_saas": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}

if IS_PRODUCTION:
    configuration_errors = []
    public_url = urlparse(PUBLIC_BASE_URL)
    if DEBUG:
        configuration_errors.append("DJANGO_DEBUG must be false")
    if len(SECRET_KEY) < 50 or SECRET_KEY in {"dev-secret-key", "replace-me"}:
        configuration_errors.append("DJANGO_SECRET_KEY must be a unique secret of at least 50 characters")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS or all(
        host in {"localhost", "127.0.0.1", "0.0.0.0", "[::1]"} for host in ALLOWED_HOSTS
    ):
        configuration_errors.append("DJANGO_ALLOWED_HOSTS must list explicit public hostnames")
    if public_url.scheme != "https" or not public_url.hostname or public_url.username or public_url.password:
        configuration_errors.append("DJANGO_PUBLIC_BASE_URL must be an https URL")
    elif not any(
        public_url.hostname == host or (host.startswith(".") and public_url.hostname.endswith(host))
        for host in ALLOWED_HOSTS
    ):
        configuration_errors.append("DJANGO_PUBLIC_BASE_URL hostname must be in DJANGO_ALLOWED_HOSTS")
    if database_url and urlparse(database_url).scheme not in {"postgres", "postgresql"}:
        configuration_errors.append("DATABASE_URL must use PostgreSQL")
    if database_url and database_options.get("sslmode", "").lower() != "verify-full":
        configuration_errors.append(
            "DATABASE_URL must use sslmode=verify-full in production"
        )
    if not database_url:
        configuration_errors.append("DATABASE_URL (or DATABASE_URL_FILE) is required")
    if not cache_url:
        configuration_errors.append("DJANGO_CACHE_URL is required for shared rate limits")
    if EMAIL_BACKEND in {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.filebased.EmailBackend",
        "django.core.mail.backends.locmem.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
    } or not OUTBOUND_EMAIL_ENABLED:
        configuration_errors.append("a deliverable outbound email backend is required")
    if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and not EMAIL_HOST:
        configuration_errors.append("DJANGO_EMAIL_HOST is required for SMTP delivery")
    if ".local" in DEFAULT_FROM_EMAIL.lower():
        configuration_errors.append("DJANGO_DEFAULT_FROM_EMAIL must use a deliverable domain")
    if not SESSION_COOKIE_SECURE or not CSRF_COOKIE_SECURE or not SECURE_SSL_REDIRECT:
        configuration_errors.append("HTTPS redirect and secure cookies must remain enabled")
    for rate_name, rate_value in RATE_LIMITS.items():
        if not re.fullmatch(r"[1-9]\d*/(?:[1-9]\d*)?[smhd]", rate_value.strip().lower()):
            configuration_errors.append(f"invalid rate limit for {rate_name}")
    if TRUSTED_PROXY_COUNT < 1:
        configuration_errors.append("DJANGO_TRUSTED_PROXY_COUNT must be at least 1")
    if env_bool("DJANGO_TRUST_X_FORWARDED_PROTO", False) and not TRUSTED_PROXY_IPS:
        configuration_errors.append(
            "DJANGO_TRUSTED_PROXY_IPS is required when trusting X-Forwarded-Proto"
        )
    if configuration_errors:
        raise ImproperlyConfigured("Invalid production configuration: " + "; ".join(configuration_errors))

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["api.authentication.APIKeyAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["api.throttles.ApiKeyRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "api_key": "240/min",
    },
}
