from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from api.models import APIKey
from catalog.models import Category, Product
from inventory.models import Stock
from accounts.models import TenantMembership
from tenants.models import Tenant


User = get_user_model()


class APILayerTests(APITestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        self.user = User.objects.create_user(username="owner", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.user,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )

        self.category_a = Category.objects.create(
            tenant=self.tenant_a,
            name="Networking",
            slug="networking",
            is_active=True,
        )
        self.category_b = Category.objects.create(
            tenant=self.tenant_b,
            name="Consumables",
            slug="consumables",
            is_active=True,
        )
        self.product_a = Product.objects.create(
            tenant=self.tenant_a,
            category=self.category_a,
            name="Router",
            sku="RTR-100",
            barcode="1111111111111",
            cost_price=Decimal("55.00"),
            sale_price=Decimal("80.00"),
            is_active=True,
        )
        self.product_b = Product.objects.create(
            tenant=self.tenant_b,
            category=self.category_b,
            name="Paper Roll",
            sku="PPR-200",
            barcode="2222222222222",
            cost_price=Decimal("5.00"),
            sale_price=Decimal("10.00"),
            is_active=True,
        )
        self.stock_a = Stock.objects.create(
            tenant=self.tenant_a,
            product=self.product_a,
            quantity=10,
            cost_value=Decimal("550.00"),
        )
        self.stock_b = Stock.objects.create(
            tenant=self.tenant_b,
            product=self.product_b,
            quantity=20,
            cost_value=Decimal("100.00"),
        )

        self.api_key_a, self.raw_key_a = APIKey.create_key(
            tenant=self.tenant_a,
            label="Tenant A Integration",
            created_by=self.user,
            can_view_cost=False,
        )
        self.api_key_b, self.raw_key_b = APIKey.create_key(
            tenant=self.tenant_b,
            label="Tenant B Integration",
            created_by=self.user,
            can_view_cost=True,
        )

        session = self.client.session
        session["current_tenant_id"] = self.tenant_a.id
        session.save()

    def auth_headers(self, raw_key):
        return {"HTTP_AUTHORIZATION": f"Api-Key {raw_key}"}

    def test_products_list_is_tenant_scoped(self):
        response = self.client.get(
            reverse("product-list"),
            **self.auth_headers(self.raw_key_a),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["sku"], "RTR-100")

    def test_product_detail_is_tenant_scoped(self):
        response = self.client.get(
            reverse("product-detail", args=[self.product_b.id]),
            **self.auth_headers(self.raw_key_a),
        )

        self.assertEqual(response.status_code, 404)

    def test_product_search_prioritizes_sku_and_is_tenant_scoped(self):
        Product.objects.create(
            tenant=self.tenant_a,
            category=self.category_a,
            name="Router Sleeve",
            sku="RTS-101",
            barcode="3333333333333",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("15.00"),
            is_active=True,
        )

        response = self.client.get(
            reverse("product-search"),
            {"q": "RT"},
            **self.auth_headers(self.raw_key_a),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(item["sku"].startswith("RT") for item in response.data))
        self.assertEqual(response.data[0]["sku"], "RTR-100")
        self.assertEqual({item["sku"] for item in response.data}, {"RTR-100", "RTS-101"})

    def test_stock_list_is_tenant_scoped(self):
        response = self.client.get(
            reverse("stock-list"),
            **self.auth_headers(self.raw_key_a),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["quantity"], 10)

    def test_categories_list_is_tenant_scoped(self):
        response = self.client.get(
            reverse("category-list"),
            **self.auth_headers(self.raw_key_a),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["slug"], "networking")

    def test_cost_price_hidden_without_scope_and_visible_with_scope(self):
        response = self.client.get(
            reverse("product-detail", args=[self.product_a.id]),
            **self.auth_headers(self.raw_key_a),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("cost_price", response.data)

        response_scoped = self.client.get(
            reverse("product-detail", args=[self.product_b.id]),
            **self.auth_headers(self.raw_key_b),
        )
        self.assertEqual(response_scoped.status_code, 200)
        self.assertIn("cost_price", response_scoped.data)
        self.assertEqual(response_scoped.data["cost_price"], "5.00")

    def test_api_key_management_page_lists_and_creates_keys(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant_a.id
        session.save()

        response = self.client.get(reverse("api-key-management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create API key")

        created = self.client.post(
            reverse("api-key-management"),
            data={
                "label": "ERP Bridge",
                "can_view_cost": "on",
                "notes": "Warehouse sync",
            },
        )

        self.assertEqual(created.status_code, 200)
        self.assertContains(created, "API key created")
        self.assertContains(created, "ERP Bridge")
        self.assertTrue(APIKey.objects.filter(tenant=self.tenant_a, label="ERP Bridge").exists())

    def test_api_key_revoke_deactivates_key(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant_a.id
        session.save()
        api_key, _ = APIKey.create_key(
            tenant=self.tenant_a,
            label="Temporary",
            created_by=self.user,
        )

        response = self.client.post(reverse("revoke-api-key", args=[api_key.id]))

        self.assertEqual(response.status_code, 200)
        api_key.refresh_from_db()
        self.assertFalse(api_key.is_active)
