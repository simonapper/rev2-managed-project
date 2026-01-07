"""
Django settings for the Reasoning Workbench prototype.

This file is deliberately:
- explicit (no magic defaults)
- conservative (prototype-safe)
- readable (governance > cleverness)

Production hardening is explicitly out of scope.
"""

from pathlib import Path
import os
# import certifi

# --------------------------------------------------
# Core paths
# --------------------------------------------------

# --------------------------------------------------
# Security (prototype only)
# --------------------------------------------------

# WARNING: Do not use this secret key in production
SECRET_KEY = "dev-only-secret-key-change-me"

# Prototype = always debug
DEBUG = True

ALLOWED_HOSTS = []

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "accounts.backends.UsernameOrEmailBackend",
]

# --------------------------------------------------
# Applications
# --------------------------------------------------

INSTALLED_APPS = [
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Project apps
    "accounts",
    "projects",
    "chats",
    "objects",
    "config",
    "navigator",
]


# --------------------------------------------------
# Middleware
# --------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# --------------------------------------------------
# URL configuration
# --------------------------------------------------

ROOT_URLCONF = "workbench.urls"


# --------------------------------------------------
# Templates
# --------------------------------------------------


BASE_DIR = Path(__file__).resolve().parent.parent

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]



# --------------------------------------------------
# WSGI / ASGI
# --------------------------------------------------

WSGI_APPLICATION = "workbench.wsgi.application"
# ASGI can be added later if needed

ENV = os.getenv("DJANGO_ENV", "dev")
# --------------------------------------------------
# Email (Gmail SMTP)
# --------------------------------------------------
# *** NOTE *** AVG Mailshield needs to be turned off 
if ENV == "dev":
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "accounts.email_backends.GmailTLSEmailBackend"
    EMAIL_HOST = "smtp.gmail.com"
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = os.environ["EMAIL_HOST_USER"]
    EMAIL_HOST_PASSWORD = os.environ["EMAIL_HOST_PASSWORD"]
    DEFAULT_FROM_EMAIL = "Workbench <aiscape2026@gmail.com>"
    # --------------------------------------------------
# Database
# --------------------------------------------------

# SQLite is sufficient and intentional for prototype phase
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# --------------------------------------------------
# Password validation
# --------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --------------------------------------------------
# Internationalisation
# --------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --------------------------------------------------
# Static files
# --------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ============================================================
# MEDIA (User-generated files)
# ============================================================

# URL prefix for media files
MEDIA_URL = "/media/"

# Physical storage root (top-level bucket)
MEDIA_ROOT = BASE_DIR / "media"
# --------------------------------------------------
# Default primary key field type
# --------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
