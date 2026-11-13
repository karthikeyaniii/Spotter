"""
Tests never touch the network. Any real HTTP call to an external service fails
loudly, so a test that forgets to mock geocoding/routing is caught immediately.
"""
from unittest.mock import patch

from fuel.services import http as _http

# Kept for the few tests that exercise polite_get itself with a fake session.
ORIGINAL_POLITE_GET = _http.polite_get


def _blocked(url, **kwargs):
    raise RuntimeError(f"Network access is disabled in tests (tried {url}). Mock it.")


patch("fuel.services.http.polite_get", side_effect=_blocked).start()
patch("fuel.services.geocoding.polite_get", side_effect=_blocked).start()
patch("fuel.services.routing.polite_get", side_effect=_blocked).start()
