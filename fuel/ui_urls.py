from django.urls import path

from .views import map_page

urlpatterns = [
    path("", map_page, name="map"),
]
