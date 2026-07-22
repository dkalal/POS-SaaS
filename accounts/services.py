import secrets
import re
import hashlib
import logging
from datetime import timedelta
from urllib.parse import urljoin

from django.db import transaction
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse

from accounts.models import TenantInvitation, TenantMembership
from accounts.rbac import OWNER_ROLES, active_membership_for, grantable_roles_for, role_level
from audit.models import AuditEvent
from audit.services import log_audit_event


logger = logging.getLogger(__name__)


def _normalize_email(email):
    return email.strip().lower()


def _username_for_email(email):
    """Create a stable, valid Django username without exposing it as an onboarding task."""
    User = get_user_model()
    base = re.sub(r"[^\w.@+-]", "-", email)[:140] or "team-member"
    username = base
    suffix = 1
    while User._default_manager.filter(username=username).exists():
        suffix += 1
        username = f"{base[:150 - len(str(suffix)) - 1]}-{suffix}"
    return username


def _require_team_admin(*, actor, tenant):
    membership = active_membership_for(actor, tenant)
    if membership is None or membership.role not in OWNER_ROLES:
        raise ValueError("You do not have permission to manage this workspace team.")
    return membership


def _assert_grantable(*, actor_membership, role):
    if role not in grantable_roles_for(actor_membership):
        raise ValueError("You cannot grant a role equal to or higher than your workspace authority.")


def _assert_target_manageable(*, actor_membership, target_membership):
    if actor_membership.pk == target_membership.pk:
        raise ValueError("You cannot change your own workspace access.")
    if role_level(target_membership.role) >= role_level(actor_membership.role):
        raise ValueError("You cannot manage a member with equal or higher workspace authority.")


def _assert_not_final_owner_admin(membership):
    if membership.role not in OWNER_ROLES or membership.status != TenantMembership.Status.ACTIVE:
        return
    another_exists = TenantMembership.objects.filter(
        tenant=membership.tenant,
        role__in=OWNER_ROLES,
        status=TenantMembership.Status.ACTIVE,
        is_active=True,
    ).exclude(pk=membership.pk).exists()
    if not another_exists:
        raise ValueError("The last active Owner/Admin cannot be demoted, deactivated, or removed.")


@transaction.atomic
def create_tenant_invitation(*, tenant, email, role, invited_by, notes="", base_url=""):
    actor_membership = _require_team_admin(actor=invited_by, tenant=tenant)
    _assert_grantable(actor_membership=actor_membership, role=role)
    if role not in (TenantMembership.Role.MANAGER, TenantMembership.Role.CASHIER):
        raise ValueError("New invitations may grant only Manager or Cashier access.")
    email = _normalize_email(email)
    if TenantMembership.objects.filter(
        tenant=tenant, user__email__iexact=email, is_active=True,
    ).exists():
        raise ValueError("This person already has an active membership in this workspace.")
    now = timezone.now()
    TenantInvitation.objects.filter(
        tenant=tenant,
        status=TenantInvitation.Status.PENDING,
        expires_at__lte=now,
        is_active=True,
    ).update(status=TenantInvitation.Status.EXPIRED, is_active=False, updated_at=now)
    if TenantInvitation.objects.filter(
        tenant=tenant,
        email__iexact=email,
        status=TenantInvitation.Status.PENDING,
        expires_at__gt=now,
        is_active=True,
    ).exists():
        raise ValueError("There is already an active invitation for this email address.")

    raw_token = secrets.token_urlsafe(32)
    invitation = TenantInvitation.objects.create(
        tenant=tenant,
        email=email,
        role=role,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        invited_by=invited_by,
        notes=notes.strip(),
        expires_at=timezone.now() + timedelta(days=7),
    )
    invitation._raw_token = raw_token
    log_audit_event(
        tenant=tenant, actor=invited_by, action=AuditEvent.Action.INVITATION_CREATED,
        target=invitation, after_data={"email": email, "role": role},
    )
    invite_path = reverse("accept_tenant_invitation", args=[raw_token])
    invite_url = urljoin(base_url, invite_path) if base_url else invite_path
    def deliver_invitation():
        try:
            send_mail(
            subject=f"You're invited to join {tenant.name}",
            message=(
                f"You've been invited to join {tenant.name} as {invitation.get_role_display()}.\n\n"
                f"Accept your invitation here: {invite_url}\n\n"
                f"This invitation expires on {invitation.expires_at:%Y-%m-%d %H:%M:%S %Z}."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
        except Exception:
            # The raw token remains transient on this returned object so the authorized
            # admin can copy the fallback link; no reusable secret is persisted.
            invitation._delivery_failed = True
            logger.exception("invitation_delivery_failed invitation_id=%s", invitation.pk)
        else:
            invitation._delivery_failed = False

    transaction.on_commit(deliver_invitation)
    return invitation


@transaction.atomic
def revoke_tenant_invitation(*, invitation, revoked_by):
    _require_team_admin(actor=revoked_by, tenant=invitation.tenant)
    invitation = TenantInvitation.objects.select_for_update().get(pk=invitation.pk, tenant=invitation.tenant)
    if invitation.status != TenantInvitation.Status.PENDING or not invitation.is_active:
        raise ValueError("This invitation is no longer pending.")
    invitation.is_active = False
    invitation.status = TenantInvitation.Status.REVOKED
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["is_active", "revoked_at", "status", "updated_at"])
    log_audit_event(tenant=invitation.tenant, actor=revoked_by, action=AuditEvent.Action.INVITATION_REVOKED, target=invitation, metadata={"email": invitation.email})
    return invitation


@transaction.atomic
def accept_tenant_invitation(*, invitation, accepted_by):
    invitation = TenantInvitation._base_manager.select_for_update().select_related("tenant", "invited_by").get(pk=invitation.pk)
    normalized_email = _normalize_email(accepted_by.email or "")
    if normalized_email != invitation.email.lower():
        raise ValueError("This invitation is for a different email address.")
    if not invitation.is_active or invitation.revoked_at is not None or invitation.accepted_at is not None:
        raise ValueError("This invitation is no longer available.")
    if invitation.expires_at <= timezone.now():
        invitation.status = TenantInvitation.Status.EXPIRED
        invitation.is_active = False
        invitation.save(update_fields=["status", "is_active", "updated_at"])
        raise ValueError("This invitation has expired.")
    if accepted_by.is_superuser:
        raise ValueError("Platform administrators cannot accept tenant invitations.")
    membership, created = TenantMembership.objects.update_or_create(
        tenant=invitation.tenant,
        user=accepted_by,
        defaults={
            "role": invitation.role,
            "status": TenantMembership.Status.ACTIVE,
            "invited_by": invitation.invited_by,
            "joined_at": timezone.now(),
            "is_active": True,
        },
    )
    invitation.accepted_by = accepted_by
    invitation.accepted_at = timezone.now()
    invitation.is_active = False
    invitation.status = TenantInvitation.Status.ACCEPTED
    invitation.save(update_fields=["accepted_by", "accepted_at", "is_active", "status", "updated_at"])
    log_audit_event(
        tenant=invitation.tenant,
        actor=accepted_by,
        action=AuditEvent.Action.INVITATION_ACCEPTED,
        target=membership,
        after_data={
            "tenant_id": membership.tenant_id,
            "user_id": membership.user_id,
            "role": membership.role,
            "is_active": membership.is_active,
        },
        metadata={"invitation_id": invitation.pk, "email": invitation.email, "membership_created": created},
    )
    return membership, invitation


@transaction.atomic
def change_membership_role(*, membership, new_role, changed_by):
    target = TenantMembership.objects.select_for_update().select_related("tenant", "user").get(
        pk=membership.pk, tenant=membership.tenant
    )
    actor_membership = _require_team_admin(actor=changed_by, tenant=target.tenant)
    _assert_target_manageable(actor_membership=actor_membership, target_membership=target)
    _assert_grantable(actor_membership=actor_membership, role=new_role)
    if target.role in OWNER_ROLES and new_role not in OWNER_ROLES:
        _assert_not_final_owner_admin(target)
    before = {"role": target.role, "status": target.status}
    target.role = new_role
    target.save(update_fields=["role", "updated_at"])
    log_audit_event(
        tenant=target.tenant,
        actor=changed_by,
        action=AuditEvent.Action.ROLE_UPDATED,
        target=target,
        before_data=before,
        after_data={"role": target.role, "status": target.status},
    )
    return target


@transaction.atomic
def change_membership_status(*, membership, new_status, changed_by):
    if new_status not in (
        TenantMembership.Status.ACTIVE,
        TenantMembership.Status.SUSPENDED,
        TenantMembership.Status.REMOVED,
    ):
        raise ValueError("Unsupported membership status.")
    target = TenantMembership.objects.select_for_update().select_related("tenant", "user").get(
        pk=membership.pk, tenant=membership.tenant
    )
    actor_membership = _require_team_admin(actor=changed_by, tenant=target.tenant)
    _assert_target_manageable(actor_membership=actor_membership, target_membership=target)
    if new_status == TenantMembership.Status.ACTIVE:
        _assert_grantable(actor_membership=actor_membership, role=target.role)
    elif target.status == TenantMembership.Status.ACTIVE:
        _assert_not_final_owner_admin(target)
    before = {"role": target.role, "status": target.status}
    target.status = new_status
    target.save(update_fields=["status", "is_active", "joined_at", "updated_at"])
    action = {
        TenantMembership.Status.ACTIVE: AuditEvent.Action.MEMBER_REACTIVATED,
        TenantMembership.Status.SUSPENDED: AuditEvent.Action.MEMBER_SUSPENDED,
        TenantMembership.Status.REMOVED: AuditEvent.Action.MEMBER_REMOVED,
    }[new_status]
    log_audit_event(
        tenant=target.tenant,
        actor=changed_by,
        action=action,
        target=target,
        before_data=before,
        after_data={"role": target.role, "status": target.status},
    )
    return target


@transaction.atomic
def create_invited_user_and_accept(*, invitation, password):
    """Provision an account only for the invited address, then consume that invite."""
    User = get_user_model()
    email = _normalize_email(invitation.email)
    if User._default_manager.filter(email__iexact=email).exists():
        raise ValueError("An account already exists for this email address. Please sign in to accept the invitation.")

    user = User(username=_username_for_email(email), email=email, is_active=True)
    user.set_password(password)
    user.save()
    accept_tenant_invitation(invitation=invitation, accepted_by=user)
    return user
