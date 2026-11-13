"""Fast JSON rendering for API responses (orjson is ~10x faster than json for route geometry)."""
import orjson
from rest_framework.renderers import BaseRenderer


def _default(obj):
    return str(obj)  # lazy translation strings, Decimals, etc.


class ORJSONRenderer(BaseRenderer):
    media_type = "application/json"
    format = "json"
    charset = None  # orjson emits UTF-8 bytes

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        return orjson.dumps(data, default=_default, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS)
