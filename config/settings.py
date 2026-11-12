"""
Django settings for the Spotter fuel-route assessment project.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

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

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
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

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOW_ALL_ORIGINS = True

# Django's default ("same-origin") strips the Referer on requests to map tile
# servers, and OpenStreetMap then answers with "Access blocked" tiles.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}

# In-process cache: repeated geocode / route lookups never hit the external APIs again.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route",
        "TIMEOUT": 60 * 60 * 24,
    }
}

# ---------------------------------------------------------------------------
# Fuel route planner configuration
# ---------------------------------------------------------------------------
FUEL_ROUTE = {
    # Vehicle assumptions from the assignment.
    "VEHICLE_RANGE_MILES": float(os.environ.get("VEHICLE_RANGE_MILES", 500)),
    "MPG": float(os.environ.get("VEHICLE_MPG", 10)),
    # Only stations this close to the route are considered (city-level geocoding
    # of the price list is accurate to a few miles, so a 10 mile corridor is used).
    "MAX_DETOUR_MILES": float(os.environ.get("MAX_DETOUR_MILES", 10)),
    # A fuel stop is only worth making if it saves more than this many dollars
    # compared with skipping it (0 = pure cost optimum, which may stop for cents).
    "STOP_PENALTY_USD": float(os.environ.get("STOP_PENALTY_USD", 10)),
    # Data files
    "PRICES_CSV": BASE_DIR / "data" / "fuel-prices-for-be-assessment.csv",
    "CITY_COORDS_JSON": BASE_DIR / "data" / "city_coords.json",
    # Offline US place list (Census Gazetteer): resolves "City, ST" inputs and powers suggestions
    # without calling an external geocoder.
    "US_PLACES_CSV": BASE_DIR / "data" / "us_places.csv",
    # External (free, keyless) services
    "OSRM_BASE_URL": os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org"),
    "NOMINATIM_BASE_URL": os.environ.get("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org"),
    "PHOTON_BASE_URL": os.environ.get("PHOTON_BASE_URL", "https://photon.komoot.io"),
    "USER_AGENT": os.environ.get("GEOCODER_USER_AGENT", "spotter-fuel-route-planner/1.0"),
    "HTTP_TIMEOUT_SECONDS": float(os.environ.get("HTTP_TIMEOUT_SECONDS", 20)),  # routing
    # Geocoders are only a fallback for inputs the offline place list can't resolve,
    # so give up quickly rather than keep the user waiting.
    "GEOCODER_TIMEOUT_SECONDS": float(os.environ.get("GEOCODER_TIMEOUT_SECONDS", 6)),
    # Max points of route geometry returned in the API response (thinned for payload size).
    "MAX_GEOMETRY_POINTS": int(os.environ.get("MAX_GEOMETRY_POINTS", 2500)),
}
