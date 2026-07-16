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
    token = secrets.token_urlsafe(48)
    EmailVerification.objects.create(user=user, token_hash=_token_hash(token), expires_at=now + timedelta(hours=24), last_sent_at=now)
    log_audit_event(tenant=tenant, actor=user, action=AuditEvent.Action.ROLE_ASSIGNED, target=membership,
                    after_data={"user_id": user.pk, "role": membership.role}, metadata={"event": "signup"})
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


def create_opening_stock(*, tenant, product, quantity, user):
    from inventory.models import Stock, StockMovement
    quantity = int(quantity or 0)
    if quantity <= 0 or not product.track_inventory:
        return None
    stock, _ = Stock.objects.get_or_create(tenant=tenant, product=product, defaults={"quantity": 0})
    before = stock.quantity
    stock.quantity = before + quantity
    stock.last_movement_at = timezone.now()
    stock.save(update_fields=["quantity", "last_movement_at", "updated_at"])
    StockMovement.objects.create(tenant=tenant, stock=stock, product=product,
        movement_type=StockMovement.MovementType.ADJUSTMENT_IN,
        reference_type=StockMovement.ReferenceType.STOCK_ADJUSTMENT, reference_id=product.pk,
        quantity_delta=quantity, quantity_before=before, quantity_after=stock.quantity,
        note="Opening stock from onboarding", created_by=user)
    return stock
