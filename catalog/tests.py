from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import TenantMembership
from catalog.models import Category, Product
from catalog.forms import ProductForm
from inventory.models import Stock
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

        response = self.client.get(reverse("catalog:product-list"))
        self.assertContains(response, "Networking")
        self.assertNotContains(response, "Category object")

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

    def test_manual_sku_is_normalized_and_unique_within_business(self):
        self._login_as(self.manager)
        payload = {
            "category": self.category.id,
            "name": "Managed Switch",
            "sku": " sw 100 ",
            "barcode": "",
            "description": "",
            "cost_price": "120.00",
            "sale_price": "180.00",
            "reorder_level": "4",
            "track_inventory": "on",
            "is_active": "on",
        }

        created = self.client.post(reverse("catalog:product-list"), data=payload)

        self.assertEqual(created.status_code, 302)
        self.assertTrue(Product.objects.filter(tenant=self.tenant, sku="SW-100").exists())

        payload["name"] = "Another Switch"
        duplicate = self.client.post(reverse("catalog:product-list"), data=payload)

        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(duplicate, "A product with this SKU already exists in this business.")

    def test_same_sku_is_allowed_in_another_business(self):
        other_tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        Product.objects.create(
            tenant=other_tenant,
            name="Tenant B Router",
            sku=self.product.sku,
            cost_price=Decimal("50.00"),
            sale_price=Decimal("75.00"),
        )

        self.assertEqual(Product.objects.filter(sku="RTR-001").count(), 2)

    def test_blank_sku_is_generated_and_incremented_for_its_business_and_prefix(self):
        self._login_as(self.manager)
        payload = {
            "category": self.category.id,
            "name": "Dell XPS 14",
            "sku": "",
            "barcode": "",
            "description": "",
            "cost_price": "120.00",
            "sale_price": "180.00",
            "reorder_level": "4",
            "track_inventory": "on",
            "is_active": "on",
        }

        first = self.client.post(reverse("catalog:product-list"), data=payload)
        second = self.client.post(reverse("catalog:product-list"), data=payload)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            list(
                Product.objects.filter(tenant=self.tenant, name="Dell XPS 14")
                .order_by("sku")
                .values_list("sku", flat=True)
            ),
            ["NET-DELL-XPS14-001", "NET-DELL-XPS14-002"],
        )

    def test_product_sku_search_is_tenant_scoped(self):
        self._login_as(self.manager)
        other_tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        Product.objects.create(
            tenant=other_tenant,
            name="Other Business Access Point",
            sku="AP-200",
            cost_price=Decimal("30.00"),
            sale_price=Decimal("50.00"),
        )
        Product.objects.create(
            tenant=self.tenant,
            category=self.category,
            name="Local Access Point",
            sku="AP-200",
            cost_price=Decimal("30.00"),
            sale_price=Decimal("50.00"),
        )

        response = self.client.get(reverse("catalog:product-list"), {"q": "AP-200"})

        self.assertContains(response, "Local Access Point")
        self.assertNotContains(response, "Other Business Access Point")

    def test_cashier_cannot_access_catalog_management(self):
        self._login_as(self.cashier)

        response = self.client.get(reverse("catalog:product-list"))

        self.assertEqual(response.status_code, 403)

    def test_cross_tenant_product_and_category_routes_are_not_visible_or_mutable(self):
        self._login_as(self.manager)
        other_tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        other_category = Category.objects.create(tenant=other_tenant, name="Other", slug="other")
        other_product = Product.objects.create(tenant=other_tenant, category=other_category, name="Other item", sku="OTHER-001")

        for url in (
            reverse("catalog:product-detail", args=[other_product.id]),
            reverse("catalog:product-edit", args=[other_product.id]),
            reverse("catalog:product-toggle-active", args=[other_product.id]),
            reverse("catalog:category-edit", args=[other_category.id]),
            reverse("catalog:category-toggle-active", args=[other_category.id]),
        ):
            response = self.client.post(url) if "toggle-active" in url else self.client.get(url)
            self.assertEqual(response.status_code, 404)

    def test_archived_categories_are_not_selectable_for_new_products(self):
        archived = Category.objects.create(tenant=self.tenant, name="Archived", slug="archived", is_active=False)
        form = ProductForm(tenant=self.tenant)
        self.assertNotIn(archived, form.fields["category"].queryset)

    def test_product_stock_filters_use_real_stock_and_reorder_level(self):
        self._login_as(self.manager)
        Stock.objects.create(tenant=self.tenant, product=self.product, quantity=2)
        low = Product.objects.create(
            tenant=self.tenant, category=self.category, name="Low item", sku="LOW-001", reorder_level=3,
        )
        Stock.objects.create(tenant=self.tenant, product=low, quantity=2)
        out = Product.objects.create(
            tenant=self.tenant, category=self.category, name="Out item", sku="OUT-001", reorder_level=2,
        )
        Stock.objects.create(tenant=self.tenant, product=out, quantity=0)

        response = self.client.get(reverse("catalog:product-list"), {"stock_state": "low_stock"})
        self.assertContains(response, "Router")
        self.assertContains(response, "Low item")
        self.assertNotContains(response, "Out item")

        response = self.client.get(reverse("catalog:product-list"), {"stock_state": "out_of_stock"})
        self.assertContains(response, "Out item")
        self.assertNotContains(response, "Low item")

    def test_product_edit_does_not_change_existing_stock(self):
        self._login_as(self.manager)
        Stock.objects.create(tenant=self.tenant, product=self.product, quantity=7)
        response = self.client.post(
            reverse("catalog:product-edit", args=[self.product.id]),
            data={
                "category": self.category.id,
                "name": "Router Updated",
                "sku": "RTR-001",
                "barcode": self.product.barcode,
                "description": "Updated",
                "cost_price": "55.00",
                "sale_price": "90.00",
                "reorder_level": "6",
                "track_inventory": "on",
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Stock.objects.get(tenant=self.tenant, product=self.product).quantity, 7)
