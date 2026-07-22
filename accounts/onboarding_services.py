import hashlib
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import EmailVerification, TenantMembership
from audit.models import AuditEvent
from audit.services import log_audit_event
from tenants.models import OnboardingProgress, Tenant, TenantSubscription


class SignupConflict(ValueError):
    pass


def outbound_email_is_configured():
    """Return true only when the selected backend can deliver beyond this process."""
    return bool(getattr(settings, "OUTBOUND_EMAIL_ENABLED", False))


def _username(email):
    base = re.sub(r"[^\w.@+-]", "-", email)[:140] or "owner"
    candidate, suffix = base, 1
    User = get_user_model()
    while User._default_manager.filter(username=candidate).exists():
        suffix += 1
        candidate = f"{base[:145]}-{suffix}"
    return candidate


def _unique_slug(name):
    base = slugify(name) or "business"
    candidate, suffix = base, 2
    while Tenant.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@transaction.atomic
def provision_signup(*, business_name, owner_name, email, phone, password, plan=None):
    User = get_user_model()
    email = email.strip().lower()
    if User._default_manager.filter(email__iexact=email).exists():
        raise SignupConflict("Unable to create this workspace. Sign in if you already have an account.")
    user = User(username=_username(email), email=email, first_name=owner_name.strip(), is_active=True)
    if hasattr(user, "last_name"):
        parts = owner_name.strip().split(maxsplit=1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ""
    user.set_password(password)
    user.save()
    trial_days = plan.trial_days if plan else 14
    now = timezone.now()
    tenant = Tenant.objects.create(
        name=business_name.strip(), slug=_unique_slug(business_name),
        contact_name=owner_name.strip(), contact_email=email, contact_phone=phone.strip(),
        status=Tenant.Status.TRIAL, trial_ends_at=now + timedelta(days=trial_days),
        subscription_plan=plan,
    )
    membership = TenantMembership.objects.create(tenant=tenant, user=user, role=TenantMembership.Role.OWNER_ADMIN)
    if plan:
        TenantSubscription.objects.create(
            tenant=tenant, plan=plan, status=TenantSubscription.Status.TRIAL if trial_days else TenantSubscription.Status.ACTIVE,
            started_at=now, current_period_ends_at=now + timedelta(days=trial_days),
        )
    progress = OnboardingProgress.objects.create(tenant=tenant)
    log_audit_event(
        tenant=tenant,
        actor=user,
        action=AuditEvent.Action.WORKSPACE_CREATED,
        target=tenant,
        after_data={"tenant_id": tenant.pk, "currency": tenant.currency, "timezone": tenant.timezone},
        metadata={"source": "public_signup"},
    )
    log_audit_event(tenant=tenant, actor=user, action=AuditEvent.Action.ROLE_ASSIGNED, target=membership,
                    after_data={"user_id": user.pk, "role": membership.role}, metadata={"event": "signup"})
    if outbound_email_is_configured():
        token = secrets.token_urlsafe(48)
        EmailVerification.objects.create(
            user=user,
            token_hash=_token_hash(token),
            expires_at=now + timedelta(hours=24),
            last_sent_at=now,
        )
        verification_url = reverse("verify_email", args=[token])
        transaction.on_commit(lambda: send_mail(
            subject="Verify your POS SaaS email address",
            message=f"Welcome to {tenant.name}. Verify your email here: {verification_url}",
            from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[email], fail_silently=False,
        ))
    return user, tenant, membership, progress


@transaction.atomic
def verify_email_token(token):
    verification = EmailVerification.objects.select_related("user").filter(token_hash=_token_hash(token)).first()
    if verification is None or verification.is_expired or verification.verified_at:
        raise ValueError("This verification link is invalid or has expired.")
    verification.verified_at = timezone.now()
    verification.save(update_fields=["verified_at"])
    return verification.user


def onboarding_checklist(*, tenant, actor=None):
    """Evaluate onboarding from real tenant-scoped operational records."""
    from accounts.models import TenantInvitation
    from catalog.models import Product
    from purchasing.models import Purchase
    from sales.models import Sale

    progress, _ = OnboardingProgress.objects.get_or_create(tenant=tenant)
    products = Product.objects.filter(tenant=tenant, is_active=True)
    has_product = products.exists()
    has_physical_product = products.filter(track_inventory=True).exists()
    has_service = products.filter(track_inventory=False).exists()
    has_received_stock = Purchase.objects.filter(tenant=tenant, status=Purchase.Status.RECEIVED).exists()
    latest_sale = (
        Sale.objects.filter(tenant=tenant, status=Sale.Status.COMPLETED)
        .select_related("receipt")
        .order_by("-created_at", "-id")
        .first()
    )
    has_team_activity = (
        TenantMembership.objects.filter(
            tenant=tenant, status=TenantMembership.Status.ACTIVE, is_active=True
        ).count() > 1
        or TenantInvitation.objects.filter(tenant=tenant, status=TenantInvitation.Status.PENDING, is_active=True).exists()
    )
    steps = [
        {"key": "profile", "title": "Confirm business profile", "complete": 1 in (progress.completed_steps or []), "optional": False, "url_name": "onboarding_setup", "url_arg": 1},
        {"key": "product", "title": "Add your first product or service", "complete": has_product, "optional": False, "url_name": "catalog:product-create"},
        {
            "key": "stock",
            "title": "Receive first physical stock",
            "complete": has_received_stock or (has_product and has_service and not has_physical_product),
            "not_applicable": has_product and has_service and not has_physical_product,
            "optional": not has_physical_product,
            "url_name": "purchasing:purchase-create",
        },
        {"key": "sale", "title": "Complete your first sale", "complete": latest_sale is not None, "optional": False, "url_name": "sales:register"},
        {"key": "team", "title": "Invite a team member", "complete": has_team_activity, "optional": True, "url_name": "team-members"},
    ]
    required_complete = all(step["complete"] or step.get("optional") for step in steps)
    if latest_sale is not None and required_complete and progress.completed_at is None:
        progress.completed_at = timezone.now()
        progress.dismissed_at = None
        progress.save(update_fields=["completed_at", "dismissed_at", "updated_at"])
        if actor is not None:
            log_audit_event(
                tenant=tenant,
                actor=actor,
                action=AuditEvent.Action.ONBOARDING_COMPLETED,
                target=progress,
                after_data={"completed": True, "first_sale_id": latest_sale.pk},
            )
    return {
        "progress": progress,
        "steps": steps,
        "completed_count": sum(1 for step in steps if step["complete"]),
        "total_count": len(steps),
        "latest_sale": latest_sale,
        "is_complete": progress.completed_at is not None,
        "is_dismissed": progress.is_dismissed,
    }
