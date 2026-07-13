from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import TenantMembership
from catalog.models import Category, Product
from tenants.models import Tenant


User = get_user_model()


class CatalogCrudTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        self.manager = User.objects.create_user(username="manager", password="pass12345")
        self.cashier = User.objects.create_user(username="cashier", password="pass12345")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.manager,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.cashier,
            role=TenantMembership.Role.CASHIER,
            is_active=True,
        )
        self.category = Category.objects.create(
            tenant=self.tenant,
            name="Networking",
            slug="networking",
            sort_order=1,
            is_active=True,
        )
        self.product = Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="Router",
            sku="RTR-001",
            barcode="1111111111111",
            cost_price=Decimal("50.00"),
            sale_price=Decimal("75.00"),
            reorder_level=5,
            track_inventory=True,
            is_active=True,
        )

    def _login_as(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

    def test_manager_can_create_edit_and_toggle_categories(self):
        self._login_as(self.manager)

        response = self.client.get(reverse("catalog:category-list"))
        self.assertEqual(response.status_code, 200)

        created = self.client.post(
            reverse("catalog:category-list"),
            data={
                "name": "Accessories",
                "slug": "accessories",
                "description": "Small add-ons",
                "sort_order": "2",
                "is_active": "on",
            },
        )
        self.assertEqual(created.status_code, 302)
        self.assertTrue(Category.objects.filter(tenant=self.tenant, slug="accessories").exists())

        updated = self.client.post(
            reverse("catalog:category-edit", args=[self.category.id]),
            data={
                "name": "Networking Gear",
                "slug": "networking",
                "description": "Updated",
                "sort_order": "3",
                "is_active": "on",
            },
        )
        self.assertEqual(updated.status_code, 302)
        self.category.refresh_from_db()
        self.assertEqual(self.category.name, "Networking Gear")
        self.assertEqual(self.category.sort_order, 3)

        toggled = self.client.post(reverse("catalog:category-toggle-active", args=[self.category.id]))
        self.assertEqual(toggled.status_code, 302)
        self.category.refresh_from_db()
        self.assertFalse(self.category.is_active)

    def test_category_list_filters_and_paginates(self):
        self._login_as(self.manager)
        for index in range(13):
            Category.objects.create(
                tenant=self.tenant,
                name=f"Category {index:02d}",
                slug=f"category-{index:02d}",
                sort_order=10 + index,
                is_active=index % 2 == 0,
            )

        response = self.client.get(reverse("catalog:category-list"), {"q": "Category 12", "status": "active"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Category 12")
        self.assertNotContains(response, "Category 11")

        paginated = self.client.get(reverse("catalog:category-list"), {"page": 2})
        self.assertEqual(paginated.status_code, 200)
        self.assertContains(paginated, "Category 11")
        self.assertNotContains(paginated, "Category 00")

    def test_manager_can_create_edit_and_toggle_products(self):
        self._login_as(self.manager)

        created = self.client.post(
            reverse("catalog:product-list"),
            data={
                "category": self.category.id,
                "name": "Switch",
                "sku": "sw-100",
                "barcode": "",
                "description": "Managed switch",
                "cost_price": "120.00",
                "sale_price": "180.00",
                "reorder_level": "4",
                "track_inventory": "on",
                "is_active": "on",
            },
        )
        self.assertEqual(created.status_code, 302)
        created_product = Product.objects.get(tenant=self.tenant, sku="SW-100")
        self.assertEqual(created_product.name, "Switch")
        self.assertIsNone(created_product.barcode)

        updated = self.client.post(
            reverse("catalog:product-edit", args=[self.product.id]),
            data={
                "category": self.category.id,
                "name": "Router Pro",
                "sku": "RTR-001",
                "barcode": "1111111111111",
                "description": "Updated router",
                "cost_price": "55.00",
                "sale_price": "90.00",
                "reorder_level": "6",
                "track_inventory": "on",
                "is_active": "on",
            },
        )
        self.assertEqual(updated.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Router Pro")
        self.assertEqual(self.product.sale_price, Decimal("90.00"))

        toggled = self.client.post(reverse("catalog:product-toggle-active", args=[self.product.id]))
        self.assertEqual(toggled.status_code, 302)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)

    def test_product_list_searches_by_name_and_sku(self):
        self._login_as(self.manager)
        Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="Access Point",
            sku="AP-200",
            barcode="2222222222222",
            cost_price=Decimal("30.00"),
            sale_price=Decimal("50.00"),
            reorder_level=2,
            track_inventory=True,
            is_active=True,
        )

        response = self.client.get(reverse("catalog:product-list"), {"q": "AP-200"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AP-200")
        self.assertNotContains(response, "RTR-001")

    def test_cashier_cannot_access_catalog_management(self):
        self._login_as(self.cashier)

        response = self.client.get(reverse("catalog:product-list"))

        self.assertEqual(response.status_code, 403)
