from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import TenantMembership
from catalog.models import Category, Product
from inventory.models import Stock
from tenants.models import Tenant


User = get_user_model()


class DashboardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pass12345")
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        TenantMembership.objects.create(
            tenant=self.tenant_a,
            user=self.user,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant_b,
            user=self.user,
            role=TenantMembership.Role.MANAGER,
            is_active=True,
        )
        self.category = Category.objects.create(
            tenant=self.tenant_a,
            name="Networking",
            slug="networking",
            is_active=True,
        )
        self.product = Product.objects.create(
            tenant=self.tenant_a,
            category=self.category,
            name="Router",
            sku="RTR-001",
            barcode="1111111111111",
            cost_price=Decimal("50.00"),
            sale_price=Decimal("75.00"),
            reorder_level=5,
            is_active=True,
        )
        self.stock = Stock.objects.create(
            tenant=self.tenant_a,
            product=self.product,
            quantity=2,
            cost_value=Decimal("100.00"),
        )

    def test_dashboard_redirects_to_login_for_anonymous_users(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_dashboard_redirects_to_bootstrap_when_no_active_tenants_exist(self):
        Tenant.objects.update(is_active=False)
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bootstrap"))

    def test_dashboard_shows_current_tenant_metrics(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant_a.id
        session.save()

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tenant A")
        self.assertContains(response, "Router")
        self.assertContains(response, "Products")
        self.assertContains(response, "1")

    def test_tenant_switch_updates_session(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("select_tenant", args=[self.tenant_b.id]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["current_tenant_id"], self.tenant_b.id)

    def test_inactive_selected_tenant_is_not_reused(self):
        self.tenant_a.is_active = False
        self.tenant_a.save(update_fields=["is_active"])
        self.client.force_login(self.user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant_a.id
        session.save()

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tenant B")
        self.assertEqual(self.client.session["current_tenant_id"], self.tenant_b.id)

    def test_authenticated_user_can_log_out_via_post(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("login"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
