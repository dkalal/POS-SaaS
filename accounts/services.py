import secrets
import re
import hashlib
from datetime import timedelta

from django.db import transaction
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse

from accounts.models import TenantInvitation, TenantMembership
from audit.models import AuditEvent
from audit.services import log_audit_event


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


@transaction.atomic
def create_tenant_invitation(*, tenant, email, role, invited_by, notes=""):
    email = _normalize_email(email)
    if TenantMembership.objects.filter(
        tenant=tenant, user__email__iexact=email, is_active=True,
    ).exists():
        raise ValueError("This person already has an active membership in this workspace.")
    if TenantInvitation.objects.filter(
        tenant=tenant,
        email__iexact=email,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
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
    invite_url = reverse("accept_tenant_invitation", args=[raw_token])
    transaction.on_commit(
        lambda: send_mail(
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
    )
    return invitation


@transaction.atomic
def revoke_tenant_invitation(*, invitation, revoked_by):
    invitation.is_active = False
    invitation.status = TenantInvitation.Status.REVOKED
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["is_active", "revoked_at", "status", "updated_at"])
    log_audit_event(tenant=invitation.tenant, actor=revoked_by, action=AuditEvent.Action.INVITATION_REVOKED, target=invitation, metadata={"email": invitation.email})
    return invitation


@transaction.atomic
def accept_tenant_invitation(*, invitation, accepted_by):
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
