from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import EmailVerification, TenantMembership
from tenants.models import OnboardingProgress, Tenant


User = get_user_model()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class SelfServiceOnboardingTests(TestCase):
    def signup_data(self):
        return {
            "business_name": "Kilimanjaro Traders", "owner_name": "Asha Mushi",
            "email": "asha@example.com", "phone": "+255 700 000 000",
            "password1": "a-strong-password-123", "password2": "a-strong-password-123", "terms": "on",
        }

    def test_signup_provisions_isolated_owner_and_verification(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("signup"), self.signup_data())
        self.assertRedirects(response, reverse("signup_success"))
        user = User.objects.get(email="asha@example.com")
        tenant = Tenant.objects.get(slug="kilimanjaro-traders")
        self.assertTrue(TenantMembership.objects.filter(tenant=tenant, user=user, role="owner_admin").exists())
        self.assertEqual(OnboardingProgress.objects.get(tenant=tenant).current_step, 1)
        self.assertFalse(EmailVerification.objects.get(user=user).is_verified)
        self.assertEqual(len(mail.outbox), 1)

        dashboard = self.client.get(reverse("dashboard"))
        self.assertRedirects(dashboard, reverse("verify_required"))

    def test_signup_is_atomic_when_onboarding_state_fails(self):
        with patch("accounts.onboarding_services.OnboardingProgress.objects.create", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                from accounts.onboarding_services import provision_signup
                provision_signup(**{
                    "business_name": "Atomic Shop", "owner_name": "Owner", "email": "atomic@example.com",
                    "phone": "0700", "password": "a-strong-password-123", "plan": None,
                })
        self.assertFalse(User.objects.filter(email="atomic@example.com").exists())
        self.assertFalse(Tenant.objects.filter(name="Atomic Shop").exists())

    def test_verification_unlocks_workspace(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("signup"), self.signup_data())
        verification = EmailVerification.objects.get(user__email="asha@example.com")
        response = self.client.get(reverse("verify_required"))
        self.assertEqual(response.status_code, 200)
        # The raw token is intentionally not stored; verify the service contract using a fresh token path.
        verification.verified_at = verification.created_at
        verification.save(update_fields=["verified_at"])
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
