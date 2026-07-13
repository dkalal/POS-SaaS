app_name = "sales"

from django.urls import path

from sales.views import register


urlpatterns = [
    path("register/", register, name="register"),
]
