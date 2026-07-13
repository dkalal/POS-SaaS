from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.models import TenantInvitation, TenantMembership
from tenants.models import Tenant


User = get_user_model()


class AuthFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="pass12345",
        )
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )

    def test_login_accepts_email_and_establishes_session(self):
        response = self.client.post(
            reverse("login"),
            data={"username": "OWNER@example.com", "password": "pass12345"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_login_page_shows_security_focused_form(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Username or email")
        self.assertContains(response, "Tenant-scoped")


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TenantInvitationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", email="owner@example.com", password="pass12345")
        self.invitee = User.objects.create_user(username="manager", email="manager@example.com", password="pass12345")
        self.tenant = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role=TenantMembership.Role.OWNER_ADMIN,
            is_active=True,
        )

    def test_owner_can_create_tenant_invitation(self):
        self.client.force_login(self.owner)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("team-members"),
                data={
                    "email": "manager@example.com",
                    "role": TenantInvitation.Role.MANAGER,
                    "notes": "Floor supervisor",
                },
            )

        self.assertEqual(response.status_code, 200)
        invitation = TenantInvitation.objects.get(tenant=self.tenant, email="manager@example.com")
        self.assertEqual(invitation.role, TenantInvitation.Role.MANAGER)
        self.assertContains(response, "Invitation created")
        self.assertContains(response, invitation.email)
        self.assertContains(response, "https://")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("You're invited to join Tenant A", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["manager@example.com"])
        self.assertIn(invitation.token, mail.outbox[0].body)

    def test_invited_user_can_accept_invitation(self):
        invitation = TenantInvitation.objects.create(
            tenant=self.tenant,
            email="manager@example.com",
            role=TenantInvitation.Role.MANAGER,
            token="invite-token-123",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.client.force_login(self.invitee)

        response = self.client.post(reverse("accept_tenant_invitation", args=[invitation.token]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["current_tenant_id"], self.tenant.id)
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=self.tenant,
                user=self.invitee,
                role=TenantMembership.Role.MANAGER,
                is_active=True,
            ).exists()
        )
        invitation.refresh_from_db()
        self.assertFalse(invitation.is_active)
        self.assertIsNotNone(invitation.accepted_at)
