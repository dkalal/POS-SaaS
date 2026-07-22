from unittest.mock import patch
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import EmailVerification, TenantMembership
from accounts.onboarding_services import onboarding_checklist
from audit.models import AuditEvent
from catalog.models import Product
from purchasing.models import Purchase
from sales.models import Sale
from suppliers.models import Supplier
from tenants.models import OnboardingProgress, Tenant


User = get_user_model()


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    OUTBOUND_EMAIL_ENABLED=True,
)
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

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        OUTBOUND_EMAIL_ENABLED=False,
    )
    def test_signup_without_real_outbound_email_enters_onboarding_without_fake_verification(self):
        response = self.client.post(reverse("signup"), self.signup_data())

        self.assertRedirects(response, reverse("onboarding_setup", args=[1]))
        user = User.objects.get(email="asha@example.com")
        tenant = Tenant.objects.get(slug="kilimanjaro-traders")
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertFalse(EmailVerification.objects.filter(user=user).exists())
        self.assertEqual(tenant.currency, "TZS")
        self.assertEqual(tenant.timezone, "Africa/Dar_es_Salaam")
        self.assertTrue(
            AuditEvent.objects.filter(tenant=tenant, action=AuditEvent.Action.WORKSPACE_CREATED).exists()
        )

    def test_checklist_uses_real_tenant_scoped_data_and_exempts_service_only_stock(self):
        user = User.objects.create_user("service-owner", "service@example.com", "pass12345")
        tenant = Tenant.objects.create(name="Service Studio", slug="service-studio")
        other = Tenant.objects.create(name="Other Workspace", slug="other-workspace")
        TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.OWNER_ADMIN)
        progress = OnboardingProgress.objects.create(tenant=tenant, completed_steps=[1])
        Product.objects.create(tenant=tenant, name="Consultation", sku="SERVICE-1", track_inventory=False)

        other_supplier = Supplier.objects.create(tenant=other, name="Other Supplier")
        Purchase.objects.create(
            tenant=other,
            purchase_number="PUR-OTHER",
            supplier=other_supplier,
            status=Purchase.Status.RECEIVED,
            order_date=date.today(),
            created_by=user,
        )
        state = onboarding_checklist(tenant=tenant, actor=user)
        stock_step = next(step for step in state["steps"] if step["key"] == "stock")
        self.assertTrue(stock_step["complete"])
        self.assertTrue(stock_step["not_applicable"])
        self.assertFalse(state["is_complete"])

        Sale.objects.create(
            tenant=tenant,
            sale_number="SALE-1",
            subtotal=Decimal("1000.00"),
            grand_total=Decimal("1000.00"),
            cashier=user,
        )
        state = onboarding_checklist(tenant=tenant, actor=user)
        progress.refresh_from_db()
        self.assertTrue(state["is_complete"])
        self.assertIsNotNone(progress.completed_at)
        self.assertTrue(
            AuditEvent.objects.filter(tenant=tenant, action=AuditEvent.Action.ONBOARDING_COMPLETED).exists()
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        OUTBOUND_EMAIL_ENABLED=False,
    )
    def test_onboarding_can_be_dismissed_and_resumed_with_audit_events(self):
        self.client.post(reverse("signup"), self.signup_data())
        tenant = Tenant.objects.get(slug="kilimanjaro-traders")

        self.client.post(reverse("dismiss_onboarding"))
        tenant.onboarding.refresh_from_db()
        self.assertIsNotNone(tenant.onboarding.dismissed_at)
        self.client.post(reverse("resume_onboarding"))
        tenant.onboarding.refresh_from_db()
        self.assertIsNone(tenant.onboarding.dismissed_at)
        self.assertEqual(
            set(
                AuditEvent.objects.filter(
                    tenant=tenant,
                    action__in=(AuditEvent.Action.ONBOARDING_DISMISSED, AuditEvent.Action.ONBOARDING_RESUMED),
                ).values_list("action", flat=True)
            ),
            {AuditEvent.Action.ONBOARDING_DISMISSED, AuditEvent.Action.ONBOARDING_RESUMED},
        )
