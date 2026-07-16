from contextlib import contextmanager
from datetime import timedelta
import hashlib

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.urls import reverse

from accounts.forms import (BusinessProfileForm, FirstProductForm, InviteTeamForm, InvitationAccountCreationForm,
                            MembershipRoleForm, MembershipStatusForm, POSPreferencesForm, SaaSAuthenticationForm,
                            SignupForm, TenantInvitationForm)
from accounts.models import EmailVerification, TenantInvitation, TenantMembership
from accounts.rbac import tenant_role_required
from accounts.services import (
    accept_tenant_invitation,
    create_invited_user_and_accept,
    create_tenant_invitation,
    revoke_tenant_invitation,
)
from audit.models import AuditEvent
from audit.services import log_audit_event
from tenants.models import Tenant
from accounts.onboarding_services import create_opening_stock, provision_signup, verify_email_token
from catalog.models import Category, Product
from django.utils import timezone
from django.utils.text import slugify
from tenants.models import OnboardingProgress


class TenantLoginView(LoginView):
    authentication_form = SaaSAuthenticationForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def render_to_response(self, context, **response_kwargs):
        return _html_response(
            self.request,
            self.template_name,
            context,
            status=response_kwargs.get("status", 200),
        )

    def form_valid(self, form):
        messages.success(self.request, "Signed in securely.")
        return super().form_valid(form)

    def get_initial(self):
        initial = super().get_initial()
        invited_email = self.request.GET.get("email", "").strip()
        if invited_email:
            initial["username"] = invited_email
        return initial


class TenantLogoutView(LogoutView):
    def post(self, request, *args, **kwargs):
        messages.success(request, "You have been signed out.")
        response = super().post(request, *args, **kwargs)
        # Invalidate browser-held copies of tenant data after the server session is closed.
        response["Clear-Site-Data"] = '"cache"'
        return response


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    ip = request.META.get("REMOTE_ADDR", "unknown")
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        key = f"signup:{ip}"
        attempts = cache.get(key, 0)
        if attempts >= 10:
            form.add_error(None, "Too many signup attempts. Please try again later.")
        elif form.is_valid():
            cache.set(key, attempts + 1, 3600)
            user, tenant, _, _ = provision_signup(
                business_name=form.cleaned_data["business_name"], owner_name=form.cleaned_data["owner_name"],
                email=form.cleaned_data["email"], phone=form.cleaned_data["phone"],
                password=form.cleaned_data["password1"], plan=form.cleaned_data.get("plan"),
            )
            login(request, user)
            request.session["current_tenant_id"] = tenant.pk
            return redirect("signup_success")
    return _html_response(request, "accounts/signup.html", {"form": form}, status=400 if request.method == "POST" else 200)


def signup_success(request):
    if not request.user.is_authenticated:
        return redirect("signup")
    return _html_response(request, "accounts/signup_success.html", {"tenant": getattr(request, "tenant", None)})


def verify_email(request, token):
    try:
        user = verify_email_token(token)
    except ValueError as exc:
        return _html_response(request, "accounts/email_verification.html", {"error": str(exc)}, status=400)
    if request.user.is_authenticated and request.user.pk == user.pk:
        from django.contrib import messages
        messages.success(request, "Email verified. Your workspace is ready.")
        return redirect("onboarding_setup", step=1)
    return _html_response(request, "accounts/email_verification.html", {"verified": True, "email": user.email})


@login_required
def onboarding_setup(request, step=1):
    tenant = getattr(request, "tenant", None)
    if tenant is None or not tenant.is_active or tenant.status not in (tenant.Status.TRIAL, tenant.Status.ACTIVE):
        return HttpResponseForbidden("This workspace is not available.")
    verification = getattr(request.user, "email_verification", None)
    if verification is not None and not verification.is_verified:
        return redirect("verify_required")
    progress, _ = OnboardingProgress.objects.get_or_create(tenant=tenant)
    forms = {1: BusinessProfileForm, 2: POSPreferencesForm, 3: FirstProductForm, 4: InviteTeamForm}
    if step == 5:
        progress.completed_at = progress.completed_at or timezone.now()
        progress.save(update_fields=["completed_at", "updated_at"])
        return _html_response(request, "accounts/onboarding_complete.html", {"tenant": tenant, "progress": progress, "step": 5})
    form_class = forms.get(step, BusinessProfileForm)
    initial = {}
    if step == 1:
        initial = {"business_name": tenant.name, "address": tenant.address, "business_type": tenant.business_type,
                   "currency": tenant.currency, "timezone": tenant.timezone, "receipt_business_details": tenant.receipt_business_details}
    elif step == 2:
        initial = {"default_tax_rate": tenant.default_tax_rate, "receipt_prefix": tenant.receipt_prefix, "default_track_inventory": tenant.default_track_inventory}
    form = form_class(request.POST or None, initial=initial)
    if request.method == "POST" and request.POST.get("skip") and step in (3, 4):
        progress.mark_step(step, skipped=True)
        progress.save(update_fields=["current_step", "completed_steps", "skipped_steps", "updated_at"])
        return redirect("onboarding_setup", step=min(step + 1, 5))
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        if step == 1:
            for field in ("business_name", "address", "business_type", "currency", "timezone", "receipt_business_details"):
                setattr(tenant, field, data[field])
            tenant.save(update_fields=["name", "address", "business_type", "currency", "timezone", "receipt_business_details", "updated_at"])
        elif step == 2:
            tenant.default_tax_rate = data["default_tax_rate"]; tenant.receipt_prefix = data["receipt_prefix"].strip().upper(); tenant.default_track_inventory = data["default_track_inventory"]
            tenant.save(update_fields=["default_tax_rate", "receipt_prefix", "default_track_inventory", "updated_at"])
        elif step == 3:
            category = None
            if data.get("category"):
                category, _ = Category.objects.get_or_create(tenant=tenant, slug=slugify(data["category"]), defaults={"name": data["category"]})
            product = Product.objects.create(tenant=tenant, category=category, name=data["name"], sku=data["sku"], sale_price=data["sale_price"], track_inventory=data["product_type"] == "product")
            create_opening_stock(tenant=tenant, product=product, quantity=data.get("opening_stock"), user=request.user)
        elif step == 4 and data.get("email"):
            create_tenant_invitation(tenant=tenant, email=data["email"], role=data["role"], invited_by=request.user, notes=f"Onboarding invite for {data.get('name') or data['email']}")
        progress.mark_step(step, skipped=False); progress.save(update_fields=["current_step", "completed_steps", "skipped_steps", "updated_at"])
        return redirect("onboarding_setup", step=min(step + 1, 5))
    return _html_response(request, "accounts/onboarding_step.html", {"tenant": tenant, "progress": progress, "form": form, "step": step})


@login_required
def verify_required(request):
    return _html_response(request, "accounts/verify_required.html", {})


@login_required
def resend_verification(request):
    verification = getattr(request.user, "email_verification", None)
    if verification is not None and not verification.is_verified:
        import secrets
        from hashlib import sha256
        token = secrets.token_urlsafe(48)
        verification.token_hash = sha256(token.encode()).hexdigest()
        verification.expires_at = timezone.now() + timedelta(hours=24)
        verification.last_sent_at = timezone.now()
        verification.save(update_fields=["token_hash", "expires_at", "last_sent_at"])
        transaction.on_commit(lambda: send_mail(
            subject="Verify your POS SaaS email address",
            message=request.build_absolute_uri(reverse("verify_email", args=[token])),
            from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[request.user.email], fail_silently=True,
        ))
    return redirect("signup_success")


@contextmanager
def _suppress_template_render_signal():
    receivers = list(template_rendered.receivers)
    cache = template_rendered.sender_receivers_cache.copy()
    template_rendered.receivers = []
    template_rendered.sender_receivers_cache.clear()
    try:
        yield
    finally:
        template_rendered.receivers = receivers
        template_rendered.sender_receivers_cache = cache


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _html_response(request, template, context, status=200):
    with _suppress_template_render_signal():
        html = render_to_string(template, context, request=request)
    return HttpResponse(html, status=status)


def _tenant_context(request, *, form=None, created_invitation=None, accept_error=None, invitation=None):
    tenant = getattr(request, "tenant", None)
    memberships = (
        TenantMembership.objects.select_related("user")
        .filter(tenant=tenant, is_active=True)
        .order_by("role", "user__username")
        if tenant is not None
        else TenantMembership.objects.none()
    )
    invitations = (
        TenantInvitation.objects.select_related("invited_by")
        .filter(tenant=tenant, accepted_at__isnull=True, revoked_at__isnull=True, is_active=True)
        .order_by("-invited_at")
        if tenant is not None
        else TenantInvitation.objects.none()
    )
    return {
        "tenant": tenant,
        "form": form or TenantInvitationForm(),
        "memberships": memberships,
        "invitations": invitations,
        "created_invitation": created_invitation,
        "invite_url": None if created_invitation is None or not settings.DEBUG else request.build_absolute_uri(
            reverse("accept_tenant_invitation", args=[created_invitation.token])
        ),
        "invite_sent_to": None if created_invitation is None else created_invitation.email,
        "accept_error": accept_error,
        "invitation": invitation,
        "can_manage_members": True,
    }


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN, action_name="invite team members")
def team_members(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return redirect("dashboard")

    context = _tenant_context(request)
    if request.method == "POST":
        form = TenantInvitationForm(request.POST)
        context["form"] = form
        if form.is_valid():
            try:
                created_invitation = create_tenant_invitation(
                    tenant=tenant, email=form.cleaned_data["email"], role=form.cleaned_data["role"],
                    invited_by=request.user, notes=form.cleaned_data["notes"],
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
                template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
                return _html_response(request, template, context, status=400)
            context = _tenant_context(request, created_invitation=created_invitation)
            template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
            return _html_response(request, template, context)
        template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
        return _html_response(request, template, context, status=400)

    template = "accounts/team.html"
    return _html_response(request, template, context)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN, action_name="revoke team invitations")
def revoke_invitation(request, invitation_id):
    tenant = getattr(request, "tenant", None)
    invitation = get_object_or_404(
        TenantInvitation,
        pk=invitation_id,
        tenant=tenant,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        is_active=True,
    )
    revoke_tenant_invitation(invitation=invitation, revoked_by=request.user)
    context = _tenant_context(request)
    template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
    return _html_response(request, template, context)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN, action_name="resend team invitations")
def resend_invitation(request, invitation_id):
    tenant = getattr(request, "tenant", None)
    invitation = get_object_or_404(TenantInvitation, pk=invitation_id, tenant=tenant, is_active=True, accepted_at__isnull=True, revoked_at__isnull=True)
    revoke_tenant_invitation(invitation=invitation, revoked_by=request.user)
    create_tenant_invitation(tenant=tenant, email=invitation.email, role=invitation.role, invited_by=request.user, notes=invitation.notes)
    context = _tenant_context(request)
    template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
    return _html_response(request, template, context)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN, action_name="change member roles")
def change_member_role(request, membership_id):
    tenant = getattr(request, "tenant", None)
    membership = get_object_or_404(TenantMembership, pk=membership_id, tenant=tenant)
    if membership.user_id == request.user.id:
        return HttpResponseForbidden("You cannot change your own workspace role.")
    form = MembershipRoleForm(request.POST)
    if request.method != "POST" or not form.is_valid():
        return HttpResponse(status=400)
    before = {"role": membership.role, "status": membership.status}
    membership.role = form.cleaned_data["role"]
    membership.save(update_fields=["role", "status", "is_active", "joined_at", "updated_at"])
    log_audit_event(tenant=tenant, actor=request.user, action=AuditEvent.Action.ROLE_UPDATED, target=membership, before_data=before, after_data={"role": membership.role, "status": membership.status})
    return redirect("team-members")


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN, action_name="change member status")
def change_member_status(request, membership_id):
    tenant = getattr(request, "tenant", None)
    membership = get_object_or_404(TenantMembership, pk=membership_id, tenant=tenant)
    if membership.user_id == request.user.id:
        return HttpResponseForbidden("You cannot change your own workspace status.")
    form = MembershipStatusForm(request.POST)
    if request.method != "POST" or not form.is_valid():
        return HttpResponse(status=400)
    status = form.cleaned_data["status"]
    before = {"role": membership.role, "status": membership.status}
    membership.status = status
    membership.save(update_fields=["status", "is_active", "joined_at", "updated_at"])
    action = AuditEvent.Action.MEMBER_SUSPENDED if status == TenantMembership.Status.SUSPENDED else AuditEvent.Action.MEMBER_REMOVED
    log_audit_event(tenant=tenant, actor=request.user, action=action, target=membership, before_data=before, after_data={"role": membership.role, "status": membership.status})
    return redirect("team-members")


@login_required
def switch_workspace(request, tenant_id):
    if request.method != "POST":
        return HttpResponse(status=405)
    membership = TenantMembership.objects.select_related("tenant").filter(
        user=request.user, tenant_id=tenant_id, status=TenantMembership.Status.ACTIVE,
        is_active=True, tenant__is_active=True, tenant__status__in=(Tenant.Status.TRIAL, Tenant.Status.ACTIVE),
    ).first()
    if membership is None:
        return HttpResponseForbidden("You do not have access to that workspace.")
    request.session["current_tenant_id"] = membership.tenant_id
    log_audit_event(tenant=membership.tenant, actor=request.user, action=AuditEvent.Action.WORKSPACE_SWITCHED, target=membership, metadata={"tenant_id": membership.tenant_id})
    return redirect("dashboard")


def _invitation_for_token(token):
    """Resolve a secret invitation token independently of the browser's current tenant."""
    return get_object_or_404(
        TenantInvitation._base_manager.select_related("tenant"),
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )


def accept_invitation(request, token):
    invitation = _invitation_for_token(token)
    tenant = invitation.tenant
    if not tenant.is_active or tenant.status not in (tenant.Status.TRIAL, tenant.Status.ACTIVE):
        return HttpResponseForbidden("This tenant is not active.")

    accept_error = None
    is_signed_in = request.user.is_authenticated
    can_accept = is_signed_in and (request.user.email or "").strip().lower() == invitation.email.lower()
    if request.method == "POST":
        if not can_accept:
            accept_error = "Sign in with the invited email address to accept this membership."
        else:
            try:
                membership, _ = accept_tenant_invitation(invitation=invitation, accepted_by=request.user)
                request.session["current_tenant_id"] = membership.tenant_id
                return redirect("dashboard")
            except ValueError as exc:
                accept_error = str(exc)

    return _html_response(
        request,
        "accounts/invitation_accept.html",
        {
            "tenant": tenant,
            "invitation": invitation,
            "raw_token": token,
            "accept_error": accept_error,
            "can_accept": can_accept,
            "account_exists": get_user_model()._default_manager.filter(email__iexact=invitation.email).exists(),
        },
    )


def create_invitation_account(request, token):
    invitation = _invitation_for_token(token)
    tenant = invitation.tenant
    if not tenant.is_active or tenant.status not in (tenant.Status.TRIAL, tenant.Status.ACTIVE):
        return HttpResponseForbidden("This tenant is not active.")
    if invitation.accepted_at or invitation.revoked_at or not invitation.is_active or invitation.is_expired:
        return redirect("accept_tenant_invitation", token=token)
    if request.user.is_authenticated:
        return redirect("accept_tenant_invitation", token=token)

    form = InvitationAccountCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        candidate = get_user_model()(email=invitation.email)
        if form.validate_password_for(candidate):
            try:
                user = create_invited_user_and_accept(invitation=invitation, password=form.cleaned_data["password1"])
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                login(request, user)
                request.session["current_tenant_id"] = tenant.id
                messages.success(request, f"Your account is ready. Welcome to {tenant.name}.")
                return redirect("dashboard")

    return _html_response(
        request,
        "accounts/invitation_signup.html",
        {"tenant": tenant, "invitation": invitation, "raw_token": token, "form": form},
        status=400 if request.method == "POST" and form.errors else 200,
    )
