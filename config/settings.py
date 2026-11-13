"""
Django settings for the Fuel Route Planner.

Local development needs no configuration (DEBUG on, SQLite). For production set
DJANGO_DEBUG=0, DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS; the app refuses to
start with DEBUG off and the development secret key.
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

_DEV_SECRET_KEY = "dev-only-insecure-key-change-me"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY") or _DEV_SECRET_KEY
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()]
if not DEBUG and (SECRET_KEY in (_DEV_SECRET_KEY, "change-me") or len(SECRET_KEY) < 32):
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a random value of 32+ characters when DJANGO_DEBUG=0.")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "fuel",
]

# Compression: behind nginx (DJANGO_NUM_PROXIES > 0) nginx gzips in C, outside the app
# workers; serving directly (local development) Django does it.
_GZIP_IN_DJANGO = not int(os.environ.get("DJANGO_NUM_PROXIES") or 0)

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # static files without a separate web server
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if _GZIP_IN_DJANGO:
    MIDDLEWARE.insert(2, "django.middleware.gzip.GZipMiddleware")  # after WhiteNoise

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite for local development; set DATABASE_URL (e.g. postgres://user:pass@host:5432/db)
# for production. PostgreSQL uses a per-process connection pool (gunicorn threads share it).
import dj_database_url  # noqa: E402

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_health_checks=True,
    )
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    DATABASES["default"]["CONN_MAX_AGE"] = 0  # pooling replaces persistent connections
    DATABASES["default"].setdefault("OPTIONS", {})["pool"] = {
        "min_size": int(os.environ.get("DB_POOL_MIN") or 2),
        "max_size": int(os.environ.get("DB_POOL_MAX") or 10),
        "timeout": 10,
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
                    if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Public, read-only API without cookies: any origin may call it.
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = False

if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS") or 60 * 60 * 24 * 30)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # Joining the browser HSTS preload list is hard to undo, so it is opt-in.
    SECURE_HSTS_PRELOAD = os.environ.get("DJANGO_HSTS_PRELOAD", "0") == "1"
    if not SECURE_HSTS_PRELOAD:
        SILENCED_SYSTEM_CHECKS = ["security.W021"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# Django's default ("same-origin") strips the Referer on requests to map tile
# servers, and OpenStreetMap then answers with "Access blocked" tiles.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "fuel.renderers.ORJSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    # Public API, no sessions: also avoids CSRF failures on POST for logged-in admin users.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    # Each new route can trigger calls to free public services; cap per-client usage.
    "DEFAULT_THROTTLE_CLASSES": ["fuel.throttling.FixedWindowScopedThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "route": (os.environ.get("THROTTLE_ROUTE") or "30/minute"),
        "places": (os.environ.get("THROTTLE_PLACES") or "240/minute"),
    },
    "EXCEPTION_HANDLER": "fuel.exceptions.api_exception_handler",
    # Behind nginx: take the client IP from X-Forwarded-For (one trusted proxy) so
    # throttling is per real client, not per load balancer.
    "NUM_PROXIES": int(os.environ.get("DJANGO_NUM_PROXIES") or 0) or None,
}

# Shared cache for routes, API throttle counters, single-flight locks and the
# cross-instance limiter for the public map APIs.
# * REDIS_URL set (production): Redis, shared by every process on every host.
# * otherwise: a file cache, shared by the processes of one machine.
REDIS_URL = os.environ.get("REDIS_URL") or ""
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": 60 * 60 * 24 * 7,
            "KEY_PREFIX": "fuel",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": os.environ.get("DJANGO_CACHE_DIR") or str(BASE_DIR / ".cache"),
            "TIMEOUT": 60 * 60 * 24 * 7,
            "OPTIONS": {"MAX_ENTRIES": 5000},
        }
    }

# ---------------------------------------------------------------------------
# Fuel route planner configuration
# ---------------------------------------------------------------------------
FUEL_ROUTE = {
    # Vehicle assumptions from the assignment.
    "VEHICLE_RANGE_MILES": float(os.environ.get("VEHICLE_RANGE_MILES") or 500),
    "MPG": float(os.environ.get("VEHICLE_MPG") or 10),
    # Only stations this close to the route are considered (city-level geocoding
    # of the price list is accurate to a few miles, so a 10 mile corridor is used).
    "MAX_DETOUR_MILES": float(os.environ.get("MAX_DETOUR_MILES") or 10),
    # A fuel stop is only worth making if it saves more than this many dollars
    # compared with skipping it (0 = pure cost optimum, which may stop for cents).
    "STOP_PENALTY_USD": float(os.environ.get("STOP_PENALTY_USD") or 10),
    # Data files
    "PRICES_CSV": BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
    "CITY_COORDS_JSON": BASE_DIR / "data" / "city_coords.json",
    # Offline US place list (Census Gazetteer): resolves "City, ST" inputs and powers suggestions
    # without calling an external geocoder.
    "US_PLACES_CSV": BASE_DIR / "data" / "us_places.csv",
    # External (free, keyless) services
    "OSRM_BASE_URL": (os.environ.get("OSRM_BASE_URL") or "https://router.project-osrm.org"),
    "NOMINATIM_BASE_URL": (os.environ.get("NOMINATIM_BASE_URL") or "https://nominatim.openstreetmap.org"),
    "PHOTON_BASE_URL": (os.environ.get("PHOTON_BASE_URL") or "https://photon.komoot.io"),
    "USER_AGENT": (os.environ.get("GEOCODER_USER_AGENT") or "spotter-fuel-route-planner/1.0"),
    "HTTP_TIMEOUT_SECONDS": float(os.environ.get("HTTP_TIMEOUT_SECONDS") or 20),  # routing
    # Geocoders are only a fallback for inputs the offline place list can't resolve,
    # so give up quickly rather than keep the user waiting.
    "GEOCODER_TIMEOUT_SECONDS": float(os.environ.get("GEOCODER_TIMEOUT_SECONDS") or 6),
    # Public services allow at most 1 request/second; calls are spaced per host.
    "MIN_SECONDS_BETWEEN_CALLS": {
        "nominatim.openstreetmap.org": 1.0,
        "router.project-osrm.org": 1.0,
    },
    # Identical trip requests are answered from cache for this long (prices are static per load;
    # reloading prices changes the cache key, so stale plans are never served).
    "PLAN_CACHE_SECONDS": int(os.environ.get("PLAN_CACHE_SECONDS") or 600),
    # Max points of route geometry returned in the API response (thinned for payload size).
    "MAX_GEOMETRY_POINTS": int(os.environ.get("MAX_GEOMETRY_POINTS") or 2500),
}


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": (os.environ.get("LOG_LEVEL") or "INFO")},
    "loggers": {"django": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}

# Tests use a private in-memory cache (no shared throttle counters or cached routes).
if len(sys.argv) > 1 and sys.argv[1] == "test":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
