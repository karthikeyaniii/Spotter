from django.urls import path

from .views import ApiRootView, HealthView, PlaceSuggestView, RoutePlanView

app_name = "fuel"

urlpatterns = [
    path("", ApiRootView.as_view(), name="root"),
    path("route/", RoutePlanView.as_view(), name="route"),
    path("places/", PlaceSuggestView.as_view(), name="places"),
    path("health/", HealthView.as_view(), name="health"),
]
