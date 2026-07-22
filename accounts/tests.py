from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.models import TenantInvitation, TenantMembership
from accounts.services import change_membership_role, change_membership_status, create_tenant_invitation
from audit.models import AuditEvent
from tenants.models import Tenant
from tenants.services import update_workspace_settings


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

    def test_login_accepts_username_and_case_insensitive_email(self):
        for identifier in ("owner", "OWNER@example.com"):
            self.client.post(reverse("logout")) if self.client.session.get("_auth_user_id") else None
            response = self.client.post(
                reverse("login"),
                data={"username": identifier, "password": "pass12345"},
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_login_fails_closed_for_ambiguous_email_identity(self):
        User.objects.create_user(username="second-owner", email=self.user.email, password="pass12345")

        response = self.client.post(
            reverse("login"),
            data={"username": self.user.email, "password": "pass12345"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The credentials did not match an active account.")

    def test_login_page_shows_security_focused_form(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Username or email")
        self.assertContains(response, "Tenant-scoped")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Pragma"], "no-cache")

    def test_logout_clears_browser_cache_and_session(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Clear-Site-Data"], '"cache"')
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("_auth_user_id", self.client.session)


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
        self.assertContains(response, 'class="responsive-table-wrap')
        self.assertContains(response, 'scope="col"')
        self.assertContains(response, "https://")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("You're invited to join Tenant A", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["manager@example.com"])
        self.assertIn("/accounts/invitations/", mail.outbox[0].body)
        self.assertNotIn(invitation.token_hash, mail.outbox[0].body)

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

    def test_existing_member_can_accept_invitation_for_another_tenant(self):
        other_tenant = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        TenantMembership.objects.create(
            tenant=other_tenant,
            user=self.invitee,
            role=TenantMembership.Role.CASHIER,
            is_active=True,
        )
        invitation = TenantInvitation.objects.create(
            tenant=self.tenant,
            email=self.invitee.email,
            role=TenantInvitation.Role.MANAGER,
            token="multi-tenant-invite-token",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.client.force_login(self.invitee)

        response = self.client.post(reverse("accept_tenant_invitation", args=[invitation.token]))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant, user=self.invitee, is_active=True).exists())
        self.assertTrue(TenantMembership.objects.filter(tenant=other_tenant, user=self.invitee, is_active=True).exists())

    def test_invited_person_without_an_account_can_create_password_and_join(self):
        invitation = TenantInvitation.objects.create(
            tenant=self.tenant,
            email="new.cashier@example.com",
            role=TenantInvitation.Role.CASHIER,
            token="new-account-invite-token",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )

        landing = self.client.get(reverse("accept_tenant_invitation", args=[invitation.token]))
        self.assertEqual(landing.status_code, 200)
        self.assertContains(landing, "Create my account")
        self.assertContains(landing, "I already have an account")

        response = self.client.post(
            reverse("create_invitation_account", args=[invitation.token]),
            data={"password1": "secure-new-password", "password2": "secure-new-password"},
        )

        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="new.cashier@example.com")
        self.assertTrue(user.check_password("secure-new-password"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.id)
        self.assertEqual(self.client.session["current_tenant_id"], self.tenant.id)
        self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant, user=user, role=TenantMembership.Role.CASHIER).exists())
        invitation.refresh_from_db()
        self.assertEqual(invitation.accepted_by, user)
        self.assertFalse(invitation.is_active)

    def test_existing_account_is_directed_to_sign_in_with_invited_email(self):
        invitation = TenantInvitation.objects.create(
            tenant=self.tenant,
            email=self.invitee.email,
            role=TenantInvitation.Role.MANAGER,
            token="existing-account-invite-token",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )

        response = self.client.get(reverse("accept_tenant_invitation", args=[invitation.token]))

        self.assertContains(response, "I already have an account")
        self.assertContains(response, "email=manager%40example.com")
        self.assertNotContains(response, "Create my account")

    def test_invitation_account_creation_rejects_mismatched_passwords(self):
        invitation = TenantInvitation.objects.create(
            tenant=self.tenant,
            email="new.manager@example.com",
            role=TenantInvitation.Role.MANAGER,
            token="password-mismatch-invite-token",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=7),
        )

        response = self.client.post(
            reverse("create_invitation_account", args=[invitation.token]),
            data={"password1": "secure-new-password", "password2": "different-password"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "The passwords do not match.", status_code=400)
        self.assertFalse(User.objects.filter(email="new.manager@example.com").exists())

    def test_duplicate_active_invitation_is_rejected(self):
        TenantInvitation.objects.create(
            tenant=self.tenant, email="new@example.com", role=TenantInvitation.Role.MANAGER,
            token="already-pending", invited_by=self.owner, expires_at=timezone.now() + timedelta(days=7),
        )
        from accounts.services import create_tenant_invitation
        with self.assertRaises(ValueError):
            create_tenant_invitation(tenant=self.tenant, email="NEW@example.com", role=TenantInvitation.Role.MANAGER, invited_by=self.owner)

    def test_suspended_membership_cannot_access_workspace(self):
        TenantMembership.objects.create(tenant=self.tenant, user=self.invitee, role=TenantMembership.Role.MANAGER, status=TenantMembership.Status.SUSPENDED)
        self.client.force_login(self.invitee)
        session = self.client.session
        session["current_tenant_id"] = self.tenant.id
        session.save()
        response = self.client.get(reverse("dashboard"))
        self.assertIsNone(getattr(response.wsgi_request, "tenant", None))
        self.assertNotIn("current_tenant_id", self.client.session)

    def test_workspace_switch_is_limited_to_active_memberships(self):
        other = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        TenantMembership.objects.create(tenant=other, user=self.invitee, role=TenantMembership.Role.CASHIER)
        self.client.force_login(self.invitee)
        response = self.client.post(reverse("switch_workspace", args=[other.id]))
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["current_tenant_id"], other.id)
        self.assertRedirects(self.client.get(reverse("dashboard")), reverse("sales:register"))
        response = self.client.post(reverse("switch_workspace", args=[self.tenant.id]))
        self.assertEqual(response.status_code, 403)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Phase8TenantSecurityTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("phase8-owner", "owner8@example.com", "pass12345")
        self.admin = User.objects.create_user("phase8-admin", "admin8@example.com", "pass12345")
        self.manager = User.objects.create_user("phase8-manager", "manager8@example.com", "pass12345")
        self.cashier = User.objects.create_user("phase8-cashier", "cashier8@example.com", "pass12345")
        self.other_owner = User.objects.create_user("other-owner", "other-owner@example.com", "pass12345")
        self.tenant = Tenant.objects.create(name="Phase 8 A", slug="phase-8-a")
        self.other_tenant = Tenant.objects.create(name="Phase 8 B", slug="phase-8-b")
        self.owner_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.owner, role=TenantMembership.Role.OWNER
        )
        self.admin_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.admin, role=TenantMembership.Role.ADMIN
        )
        self.manager_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.manager, role=TenantMembership.Role.MANAGER
        )
        self.cashier_membership = TenantMembership.objects.create(
            tenant=self.tenant, user=self.cashier, role=TenantMembership.Role.CASHIER
        )
        self.other_membership = TenantMembership.objects.create(
            tenant=self.other_tenant, user=self.other_owner, role=TenantMembership.Role.OWNER
        )

    def login_to(self, user, tenant=None):
        self.client.force_login(user)
        session = self.client.session
        session["current_tenant_id"] = (tenant or self.tenant).pk
        session.save()

    def test_user_can_hold_different_roles_in_multiple_workspaces(self):
        TenantMembership.objects.create(
            tenant=self.other_tenant, user=self.manager, role=TenantMembership.Role.CASHIER
        )
        self.assertEqual(TenantMembership.objects.get(tenant=self.tenant, user=self.manager).role, TenantMembership.Role.MANAGER)
        self.assertEqual(TenantMembership.objects.get(tenant=self.other_tenant, user=self.manager).role, TenantMembership.Role.CASHIER)

    def test_platform_superuser_cannot_be_attached_to_workspace(self):
        operator = User.objects.create_superuser("phase8-operator", "operator8@example.com", "pass12345")
        with self.assertRaises(ValidationError):
            TenantMembership.objects.create(tenant=self.tenant, user=operator, role=TenantMembership.Role.OWNER)

    def test_cashier_cannot_open_team_or_settings(self):
        self.login_to(self.cashier)
        self.assertEqual(self.client.get(reverse("team-members")).status_code, 403)
        self.assertEqual(self.client.get(reverse("workspace-settings")).status_code, 403)

    def test_legacy_owner_and_admin_roles_inherit_owner_admin_guards(self):
        self.login_to(self.owner)
        self.assertEqual(self.client.get(reverse("audit:audit-list")).status_code, 200)
        self.login_to(self.admin)
        self.assertEqual(self.client.get(reverse("catalog:product-list")).status_code, 200)

    def test_cross_tenant_member_detail_and_settings_are_not_exposed(self):
        self.login_to(self.owner)
        self.assertEqual(self.client.get(reverse("member-detail", args=[self.other_membership.pk])).status_code, 404)
        response = self.client.post(reverse("change_member_status", args=[self.other_membership.pk]), {"status": "removed"})
        self.assertEqual(response.status_code, 404)
        self.other_membership.refresh_from_db()
        self.assertTrue(self.other_membership.is_active)

    def test_role_escalation_is_rejected_in_service_and_view(self):
        with self.assertRaisesMessage(ValueError, "equal to or higher"):
            change_membership_role(
                membership=self.admin_membership,
                new_role=TenantMembership.Role.OWNER,
                changed_by=self.owner,
            )
        self.login_to(self.admin)
        response = self.client.post(
            reverse("change_member_role", args=[self.manager_membership.pk]),
            {"role": TenantMembership.Role.ADMIN},
        )
        self.assertEqual(response.status_code, 400)

    def test_user_cannot_remove_own_final_owner_access(self):
        self.admin_membership.status = TenantMembership.Status.REMOVED
        self.admin_membership.save()
        with self.assertRaisesMessage(ValueError, "own workspace access"):
            change_membership_status(
                membership=self.owner_membership,
                new_status=TenantMembership.Status.REMOVED,
                changed_by=self.owner,
            )
        self.owner_membership.refresh_from_db()
        self.assertTrue(self.owner_membership.is_active)

    def test_manager_cannot_invite_or_change_workspace_settings_at_service_layer(self):
        with self.assertRaises(ValueError):
            create_tenant_invitation(
                tenant=self.tenant,
                email="escalation@example.com",
                role=TenantMembership.Role.CASHIER,
                invited_by=self.manager,
            )
        with self.assertRaises(ValueError):
            update_workspace_settings(
                tenant=self.tenant,
                actor=self.manager,
                section="regional",
                values={"currency": "TZS", "timezone": "Africa/Dar_es_Salaam"},
            )

    def test_invitation_is_single_use_revocable_expiring_and_email_scoped(self):
        invitation = TenantInvitation.objects.create(
            tenant=self.tenant, email="new8@example.com", role=TenantMembership.Role.CASHIER,
            token="phase8-single-use", invited_by=self.owner, expires_at=timezone.now() + timedelta(days=1),
        )
        wrong_user = User.objects.create_user("wrong8", "wrong8@example.com", "pass12345")
        from accounts.services import accept_tenant_invitation, revoke_tenant_invitation
        with self.assertRaisesMessage(ValueError, "different email"):
            accept_tenant_invitation(invitation=invitation, accepted_by=wrong_user)
        invited_user = User.objects.create_user("new8", "new8@example.com", "pass12345")
        accept_tenant_invitation(invitation=invitation, accepted_by=invited_user)
        with self.assertRaisesMessage(ValueError, "no longer available"):
            accept_tenant_invitation(invitation=invitation, accepted_by=invited_user)
        revoked = TenantInvitation.objects.create(
            tenant=self.tenant, email="revoked8@example.com", role=TenantMembership.Role.CASHIER,
            token="phase8-revoked", invited_by=self.owner, expires_at=timezone.now() + timedelta(days=1),
        )
        revoke_tenant_invitation(invitation=revoked, revoked_by=self.owner)
        revoked_user = User.objects.create_user("revoked8", "revoked8@example.com", "pass12345")
        with self.assertRaisesMessage(ValueError, "no longer available"):
            accept_tenant_invitation(invitation=revoked, accepted_by=revoked_user)
        expired = TenantInvitation.objects.create(
            tenant=self.tenant, email="expired8@example.com", role=TenantMembership.Role.CASHIER,
            token="phase8-expired", invited_by=self.owner, expires_at=timezone.now() - timedelta(seconds=1),
        )
        expired_user = User.objects.create_user("expired8", "expired8@example.com", "pass12345")
        with self.assertRaisesMessage(ValueError, "expired"):
            accept_tenant_invitation(invitation=expired, accepted_by=expired_user)

    def test_settings_update_is_isolated_and_audited(self):
        self.login_to(self.owner)
        page = self.client.get(reverse("workspace-settings"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Business profile")
        self.assertContains(page, "Regional settings")
        self.assertContains(page, "Receipt and document settings")
        response = self.client.post(reverse("workspace-settings"), {
            "section": "business",
            "name": "Phase 8 A Updated",
            "contact_email": "shop@example.com",
            "contact_phone": "+255700000000",
            "address": "Dar es Salaam",
            "tax_identification_number": "TIN-8",
            "vat_registration_number": "VRN-8",
        })
        self.assertRedirects(response, reverse("workspace-settings"))
        self.tenant.refresh_from_db()
        self.other_tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, "Phase 8 A Updated")
        self.assertEqual(self.other_tenant.name, "Phase 8 B")
        event = AuditEvent.objects.get(tenant=self.tenant, action=AuditEvent.Action.WORKSPACE_SETTINGS_UPDATED)
        self.assertEqual(event.metadata["section"], "business")
        self.assertEqual(event.after_data["tax_identification_number"], "TIN-8")

    def test_reactivation_is_audited_with_distinct_action(self):
        self.manager_membership.status = TenantMembership.Status.SUSPENDED
        self.manager_membership.save()
        change_membership_status(
            membership=self.manager_membership,
            new_status=TenantMembership.Status.ACTIVE,
            changed_by=self.owner,
        )
        self.assertTrue(AuditEvent.objects.filter(
            tenant=self.tenant,
            action=AuditEvent.Action.MEMBER_REACTIVATED,
            target_object_id=str(self.manager_membership.pk),
        ).exists())
