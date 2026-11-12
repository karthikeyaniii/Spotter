from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.defaults import page_not_found, server_error

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("fuel.urls")),
    path("", include("fuel.ui_urls")),
]


def _is_api(request):
    return request.path.startswith("/api/")


def handler404(request, exception):
    """JSON for API clients, the normal page for browsers."""
    if _is_api(request):
        return JsonResponse({"error": "Not found", "details": f"No API endpoint at {request.path}. See /api/."},
                            status=404)
    return page_not_found(request, exception)


def handler500(request):
    if _is_api(request):
        return JsonResponse({"error": "Internal server error",
                             "details": "Something went wrong on our side. Please try again."}, status=500)
    return server_error(request)
