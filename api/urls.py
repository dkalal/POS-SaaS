from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.views import CategoryViewSet, ProductViewSet, StockViewSet, api_key_management, api_key_revoke


router = DefaultRouter()
router.register("products", ProductViewSet, basename="product")
router.register("stock", StockViewSet, basename="stock")
router.register("categories", CategoryViewSet, basename="category")

urlpatterns = [
    path("keys/", api_key_management, name="api-key-management"),
    path("keys/<int:key_id>/revoke/", api_key_revoke, name="revoke-api-key"),
    path("", include(router.urls)),
]
