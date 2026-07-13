from rest_framework import serializers

from api.models import APIKey
from catalog.models import Category, Product
from inventory.models import Stock


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug", "description"]
        read_only_fields = fields


class ProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    cost_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "sku",
            "barcode",
            "description",
            "sale_price",
            "cost_price",
            "reorder_level",
            "track_inventory",
            "category",
        ]
        read_only_fields = fields

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        api_key = getattr(request, "auth", None)
        if not getattr(api_key, "can_view_cost", False):
            fields.pop("cost_price", None)
        return fields


class StockSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    cost_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Stock
        fields = ["id", "quantity", "cost_value", "last_movement_at", "product"]
        read_only_fields = fields

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        api_key = getattr(request, "auth", None)
        if not getattr(api_key, "can_view_cost", False):
            fields.pop("cost_value", None)
        return fields


class APIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = APIKey
        fields = [
            "id",
            "tenant",
            "label",
            "key_prefix",
            "can_view_cost",
            "is_active",
            "revoked_at",
            "last_used_at",
            "created_at",
        ]
        read_only_fields = fields

