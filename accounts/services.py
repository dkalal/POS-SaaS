import secrets
from datetime import timedelta

from django.db import transaction
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.urls import reverse

from accounts.models import TenantInvitation, TenantMembership
from audit.models import AuditEvent
from audit.services import log_audit_event


def _normalize_email(email):
    return email.strip().lower()


@transaction.atomic
def create_tenant_invitation(*, tenant, email, role, invited_by, notes=""):
    email = _normalize_email(email)
    TenantInvitation.objects.filter(
        tenant=tenant,
        email__iexact=email,
        role=role,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        is_active=True,
    ).update(is_active=False, revoked_at=timezone.now())

    invitation = TenantInvitation.objects.create(
        tenant=tenant,
        email=email,
        role=role,
        token=secrets.token_urlsafe(32),
        invited_by=invited_by,
        notes=notes.strip(),
        expires_at=timezone.now() + timedelta(days=7),
    )
    invite_url = reverse("accept_tenant_invitation", args=[invitation.token])
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
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["is_active", "revoked_at", "updated_at"])
    return invitation


@transaction.atomic
def accept_tenant_invitation(*, invitation, accepted_by):
    normalized_email = _normalize_email(accepted_by.email or "")
    if normalized_email != invitation.email.lower():
        raise ValueError("This invitation is for a different email address.")
    if not invitation.is_active or invitation.revoked_at is not None or invitation.accepted_at is not None:
        raise ValueError("This invitation is no longer available.")
    if invitation.expires_at <= timezone.now():
        raise ValueError("This invitation has expired.")

    membership, _ = TenantMembership.objects.update_or_create(
        tenant=invitation.tenant,
        user=accepted_by,
        defaults={
            "role": invitation.role,
            "is_active": True,
        },
    )
    invitation.accepted_by = accepted_by
    invitation.accepted_at = timezone.now()
    invitation.is_active = False
    invitation.save(update_fields=["accepted_by", "accepted_at", "is_active", "updated_at"])
    log_audit_event(
        tenant=invitation.tenant,
        actor=accepted_by,
        action=AuditEvent.Action.ROLE_ASSIGNED,
        target=membership,
        after_data={
            "tenant_id": membership.tenant_id,
            "user_id": membership.user_id,
            "role": membership.role,
            "is_active": membership.is_active,
        },
        metadata={"invitation_id": invitation.pk, "email": invitation.email},
    )
    return membership, invitation
