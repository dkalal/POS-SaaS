from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import TenantMembership
from catalog.models import Category, Product
from platform_admin.models import PlatformAuditLog
from tenants.models import SubscriptionPlan, Tenant


User = get_user_model()


class PlatformAdminTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_superuser("operator", "operator@example.com", "pass12345")
        self.tenant_user = User.objects.create_user("owner", "owner@example.com", "pass12345")
        self.plan = SubscriptionPlan.objects.create(name="Starter", code="starter", monthly_price=10000, annual_price=100000)
        self.tenant = Tenant.objects.create(name="Alpha Traders", slug="alpha-traders", subscription_plan=self.plan, status=Tenant.Status.ACTIVE, is_active=True)
        TenantMembership.objects.create(tenant=self.tenant, user=self.tenant_user, role=TenantMembership.Role.OWNER_ADMIN)

    def test_platform_operator_can_access_dashboard_without_tenant_context(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("platform_admin:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Platform overview")
        self.assertContains(response, 'class="admin-sidebar"')
        self.assertContains(response, "data-theme-toggle")
        self.assertContains(response, "Sign out")

    def test_platform_pages_use_shared_theme_shell_without_legacy_dark_panels(self):
        self.client.force_login(self.operator)
        urls = [
            reverse("platform_admin:dashboard"),
            reverse("platform_admin:tenant-list"),
            reverse("platform_admin:tenant-create"),
            reverse("platform_admin:tenant-detail", args=[self.tenant.pk]),
            reverse("platform_admin:plan-list"),
            reverse("platform_admin:plan-create"),
            reverse("platform_admin:plan-edit", args=[self.plan.pk]),
        ]
        legacy_dark_classes = ("bg-slate-900", "border-slate-800", "text-slate-400", "text-white")
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="admin-sidebar"')
                self.assertContains(response, "data-theme-toggle")
                html = response.content.decode()
                main_html = html.split('<main class="main-region" id="main-content">', 1)[1].split("</main>", 1)[0]
                for class_name in legacy_dark_classes:
                    self.assertNotIn(class_name, main_html)

    def test_tenant_user_is_denied_platform_routes(self):
        self.client.force_login(self.tenant_user)
        response = self.client.get(reverse("platform_admin:tenant-list"))
        self.assertEqual(response.status_code, 403)

    def test_suspension_blocks_workspace_access_without_deleting_data(self):
        category = Category.objects.create(tenant=self.tenant, name="Hardware", slug="hardware")
        Product.objects.create(tenant=self.tenant, category=category, name="Cable", sku="CABLE-1", sale_price=1000, cost_price=500)
        self.tenant.status, self.tenant.is_active = Tenant.Status.SUSPENDED, False
        self.tenant.save(update_fields=["status", "is_active", "updated_at"])
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()
        response = self.client.get(reverse("catalog:product-list"))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Product.objects.filter(tenant=self.tenant, sku="CABLE-1").exists())

    def test_status_change_is_audited(self):
        self.client.force_login(self.operator)
        response = self.client.post(reverse("platform_admin:tenant-status", args=[self.tenant.pk]), {"status": Tenant.Status.SUSPENDED})
        self.assertRedirects(response, reverse("platform_admin:tenant-detail", args=[self.tenant.pk]))
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.status, Tenant.Status.SUSPENDED)
        self.assertFalse(self.tenant.is_active)
        event = PlatformAuditLog.objects.get(target_tenant=self.tenant, action=PlatformAuditLog.Action.TENANT_SUSPENDED)
        self.assertEqual(event.actor, self.operator)
        self.assertEqual(event.before_data["status"], Tenant.Status.ACTIVE)

    def test_tenant_scoped_page_never_returns_another_tenants_product(self):
        other = Tenant.objects.create(name="Beta", slug="beta", subscription_plan=self.plan)
        category = Category.objects.create(tenant=self.tenant, name="Hardware", slug="hardware")
        Product.objects.create(tenant=self.tenant, category=category, name="Cable", sku="CABLE-1", sale_price=1000, cost_price=500)
        other_category = Category.objects.create(tenant=other, name="Other", slug="other")
        Product.objects.create(tenant=other, category=other_category, name="Other cable", sku="CABLE-2", sale_price=1000, cost_price=500)
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()
        response = self.client.get(reverse("catalog:product-list"))
        self.assertContains(response, "Cable")
        self.assertNotContains(response, "Other cable")
