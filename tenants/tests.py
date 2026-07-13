from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import TenantMembership
from api.models import APIKey
from tenants.models import Tenant


User = get_user_model()


class TenantBootstrapTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="pass12345")

    def test_bootstrap_creates_first_tenant_membership_and_api_key(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("bootstrap"),
            data={
                "tenant_name": "Alpha Traders",
                "tenant_slug": "",
                "api_key_label": "Initial POS Sync",
                "api_key_can_view_cost": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        tenant = Tenant.objects.get(name="Alpha Traders")
        membership = TenantMembership.objects.get(tenant=tenant, user=self.owner)
        api_key = APIKey.objects.get(tenant=tenant)

        self.assertEqual(membership.role, TenantMembership.Role.OWNER_ADMIN)
        self.assertTrue(api_key.can_view_cost)
        self.assertEqual(self.client.session["current_tenant_id"], tenant.id)
        self.assertContains(response, "Copy the API key now")
        self.assertContains(response, api_key.label)
        self.assertContains(response, "Go to dashboard")

    def test_bootstrap_redirects_to_dashboard_once_tenants_exist(self):
        tenant = Tenant.objects.create(name="Existing Tenant", slug="existing-tenant")
        TenantMembership.objects.create(
            tenant=tenant,
            user=self.owner,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("bootstrap"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))

