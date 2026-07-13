from django.urls import path

from tenants.views import bootstrap


urlpatterns = [
    path("onboarding/", bootstrap, name="bootstrap"),
]

