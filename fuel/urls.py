from django.urls import path

from .views import ApiRootView, HealthView, LivenessView, PlaceSuggestView, RoutePlanView

app_name = "fuel"

urlpatterns = [
    path("", ApiRootView.as_view(), name="root"),
    path("route/", RoutePlanView.as_view(), name="route"),
    path("places/", PlaceSuggestView.as_view(), name="places"),
    path("health/", HealthView.as_view(), name="health"),
    path("health/live/", LivenessView.as_view(), name="live"),
]
