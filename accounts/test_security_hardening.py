from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import TenantMembership
from tenants.models import Tenant


User = get_user_model()


TEST_RATES = {
    "login_ip": "10/15m",
    "login_identity": "2/15m",
    "signup": "2/h",
    "invitation": "1/h",
    "invitation_token": "2/15m",
    "verification_resend": "2/h",
}


@override_settings(RATE_LIMITS=TEST_RATES)
class AuthenticationRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="secure-owner", email="secure@example.com", password="a-strong-password-123"
        )

    def tearDown(self):
        cache.clear()

    def test_login_limits_failed_attempts_by_identity(self):
        payload = {"username": self.user.email, "password": "incorrect"}
        self.assertEqual(self.client.post(reverse("login"), payload).status_code, 200)
        self.assertEqual(self.client.post(reverse("login"), payload).status_code, 200)

        response = self.client.post(reverse("login"), payload)

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many attempts", status_code=429)

    def test_signup_counts_invalid_posts(self):
        self.assertEqual(self.client.post(reverse("signup"), {}).status_code, 400)
        self.assertEqual(self.client.post(reverse("signup"), {}).status_code, 400)

        response = self.client.post(reverse("signup"), {})

        self.assertEqual(response.status_code, 429)

    def test_csrf_rejection_uses_generic_error_page(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(reverse("login"), {"username": "x", "password": "y"})

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Request expired", status_code=403)
        self.assertNotContains(response, "CSRF verification failed", status_code=403)

    @override_settings(SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True)
    def test_auth_cookies_use_secure_httponly_policy(self):
        self.client.get(reverse("login"))
        response = self.client.post(
            reverse("login"), {"username": self.user.email, "password": "a-strong-password-123"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.cookies["sessionid"]["secure"])
        self.assertTrue(response.cookies["sessionid"]["httponly"])
        self.assertEqual(response.cookies["sessionid"]["samesite"], "Lax")
        self.assertTrue(self.client.cookies["csrftoken"]["secure"])
        self.assertTrue(self.client.cookies["csrftoken"]["httponly"])


@override_settings(
    RATE_LIMITS=TEST_RATES,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class InvitationRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("owner", "owner@example.com", "a-strong-password-123")
        self.tenant = Tenant.objects.create(name="Secure Store", slug="secure-store")
        TenantMembership.objects.create(
            tenant=self.tenant, user=self.owner, role=TenantMembership.Role.OWNER
        )
        self.client.force_login(self.owner)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.pk
        session.save()

    def tearDown(self):
        cache.clear()

    def test_invitation_creation_is_limited_per_tenant_actor_and_ip(self):
        first = self.client.post(
            reverse("team-members"),
            {"email": "first@example.com", "role": TenantMembership.Role.CASHIER, "notes": ""},
        )
        second = self.client.post(
            reverse("team-members"),
            {"email": "second@example.com", "role": TenantMembership.Role.CASHIER, "notes": ""},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)


class ProductionErrorPageTests(TestCase):
    @override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
    def test_missing_page_uses_generic_404(self):
        response = self.client.get("/definitely-not-present/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertNotContains(response, "Using the URLconf", status_code=404)
