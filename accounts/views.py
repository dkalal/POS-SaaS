from contextlib import contextmanager
from datetime import timedelta
import hashlib
import logging
from urllib.parse import urljoin

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.forms import (BusinessProfileForm, FirstProductForm, InviteTeamForm, InvitationAccountCreationForm,
                            MembershipRoleForm, MembershipStatusForm, POSPreferencesForm, SaaSAuthenticationForm,
                            SignupForm, TenantInvitationForm, WorkspaceBusinessProfileForm,
                            WorkspaceOperationalForm, WorkspaceReceiptForm, WorkspaceRegionalForm)
from accounts.models import EmailVerification, TenantInvitation, TenantMembership
from accounts.rbac import OWNER_ROLES, active_membership_for, grantable_roles_for, role_level, tenant_role_required
from accounts.services import (
    accept_tenant_invitation,
    change_membership_role as change_membership_role_service,
    change_membership_status as change_membership_status_service,
    create_invited_user_and_accept,
    create_tenant_invitation,
    revoke_tenant_invitation,
)
from audit.models import AuditEvent
from audit.services import log_audit_event
from tenants.models import Tenant
from accounts.onboarding_services import (
    SignupConflict,
    onboarding_checklist,
    outbound_email_is_configured,
    provision_signup,
    verify_email_token,
)
from catalog.models import Category, Product
from django.utils import timezone
from django.utils.text import slugify
from tenants.models import OnboardingProgress
from tenants.services import update_workspace_settings
from core.rate_limits import clear as clear_rate_limit
from core.rate_limits import client_ip, consume, is_limited, opaque, record


security_logger = logging.getLogger("pos_saas.security")


def _external_url(request, path):
    base_url = settings.PUBLIC_BASE_URL or request.build_absolute_uri("/")
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip("/"))


def _rate_limited_response(request, retry_after):
    response = _html_response(
        request,
        "errors/429.html",
        {
            "title": "Too many attempts",
            "message": "Too many requests were received. Please wait before trying again.",
        },
        status=429,
    )
    return response


class TenantLoginView(LoginView):
    authentication_form = SaaSAuthenticationForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        self._rate_ip = client_ip(request)
        self._rate_identity = opaque(request.POST.get("username", ""))
        if is_limited("login_ip", self._rate_ip) or is_limited("login_identity", self._rate_identity):
            security_logger.warning("login_rate_limited ip=%s identity=%s", self._rate_ip, self._rate_identity)
            return _rate_limited_response(request, 900)
        return super().post(request, *args, **kwargs)

    def render_to_response(self, context, **response_kwargs):
        return _html_response(
            self.request,
            self.template_name,
            context,
            status=response_kwargs.get("status", 200),
        )

    def form_valid(self, form):
        clear_rate_limit("login_identity", getattr(self, "_rate_identity", ""))
        messages.success(self.request, "Signed in securely.")
        return super().form_valid(form)

    def form_invalid(self, form):
        ip = getattr(self, "_rate_ip", client_ip(self.request))
        identity = getattr(self, "_rate_identity", opaque(self.request.POST.get("username", "")))
        ip_limited, ip_window = record("login_ip", ip)
        identity_limited, identity_window = record("login_identity", identity)
        security_logger.warning("login_failed ip=%s identity=%s", ip, identity)
        if ip_limited or identity_limited:
            return _rate_limited_response(self.request, max(ip_window, identity_window))
        return super().form_invalid(form)

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
    form = SignupForm(request.POST or None)
    if request.method == "POST":
        limited, retry_after = consume("signup", client_ip(request))
        if limited:
            security_logger.warning("signup_rate_limited ip=%s", client_ip(request))
            return _rate_limited_response(request, retry_after)
        if form.is_valid():
            try:
                user, tenant, _, _ = provision_signup(
                    business_name=form.cleaned_data["business_name"], owner_name=form.cleaned_data["owner_name"],
                    email=form.cleaned_data["email"], phone=form.cleaned_data["phone"],
                    password=form.cleaned_data["password1"], plan=form.cleaned_data.get("plan"),
                )
            except (SignupConflict, IntegrityError):
                form.add_error(
                    None,
                    "We could not create a workspace with these details. Sign in if you already have an account.",
                )
            else:
                login(request, user)
                request.session["current_tenant_id"] = tenant.pk
                if hasattr(user, "email_verification"):
                    return redirect("signup_success")
                messages.success(request, "Workspace created. Start with the short setup checklist.")
                return redirect("onboarding_setup", step=1)
    return _html_response(request, "accounts/signup.html", {"form": form}, status=400 if request.method == "POST" else 200)


def signup_success(request):
    if not request.user.is_authenticated:
        return redirect("signup")
    return _html_response(
        request,
        "accounts/signup_success.html",
        {"tenant": getattr(request, "tenant", None), "email_delivery_configured": outbound_email_is_configured()},
    )


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
@tenant_role_required(*OWNER_ROLES, action_name="manage workspace onboarding")
def onboarding_setup(request, step=1):
    tenant = getattr(request, "tenant", None)
    if tenant is None or not tenant.is_active or tenant.status not in (tenant.Status.TRIAL, tenant.Status.ACTIVE):
        return HttpResponseForbidden("This workspace is not available.")
    verification = getattr(request.user, "email_verification", None)
    if verification is not None and not verification.is_verified:
        return redirect("verify_required")
    progress, _ = OnboardingProgress.objects.get_or_create(tenant=tenant)
    if request.method == "POST" and step == 1:
        progress.mark_step(1)
        progress.save(update_fields=["current_step", "completed_steps", "skipped_steps", "updated_at"])
        messages.success(request, "Business profile confirmed. You can refine it any time in workspace settings.")
        return redirect("onboarding_setup", step=1)
    checklist = onboarding_checklist(tenant=tenant, actor=request.user)
    template = "accounts/onboarding_complete.html" if checklist["is_complete"] else "accounts/onboarding_step.html"
    return _html_response(request, template, {"tenant": tenant, **checklist, "step": step})


@login_required
@tenant_role_required(*OWNER_ROLES, action_name="dismiss workspace onboarding")
@require_POST
def dismiss_onboarding(request):
    progress, _ = OnboardingProgress.objects.get_or_create(tenant=request.tenant)
    if progress.completed_at is None and progress.dismissed_at is None:
        progress.dismiss()
        progress.save(update_fields=["dismissed_at", "updated_at"])
        log_audit_event(
            tenant=request.tenant,
            actor=request.user,
            action=AuditEvent.Action.ONBOARDING_DISMISSED,
            target=progress,
            after_data={"dismissed": True},
        )
    messages.info(request, "Setup checklist dismissed. You can resume it from the dashboard.")
    return redirect("dashboard")


@login_required
@tenant_role_required(*OWNER_ROLES, action_name="resume workspace onboarding")
@require_POST
def resume_onboarding(request):
    progress, _ = OnboardingProgress.objects.get_or_create(tenant=request.tenant)
    if progress.dismissed_at is not None and progress.completed_at is None:
        progress.resume()
        progress.save(update_fields=["dismissed_at", "updated_at"])
        log_audit_event(
            tenant=request.tenant,
            actor=request.user,
            action=AuditEvent.Action.ONBOARDING_RESUMED,
            target=progress,
            after_data={"dismissed": False},
        )
    return redirect("onboarding_setup", step=1)


@login_required
def verify_required(request):
    return _html_response(request, "accounts/verify_required.html", {})


@login_required
@require_POST
def resend_verification(request):
    limited, retry_after = consume(
        "verification_resend", f"user:{request.user.pk}", f"ip:{client_ip(request)}"
    )
    if limited:
        return _rate_limited_response(request, retry_after)
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
            message=_external_url(request, reverse("verify_email", args=[token])),
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
    query = (request.GET.get("q") or "").strip()
    role_filter = request.GET.get("role") or ""
    status_filter = request.GET.get("status") or ""
    memberships = (
        TenantMembership.objects.select_related("user")
        .filter(tenant=tenant)
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
    if query:
        memberships = memberships.filter(
            Q(user__username__icontains=query) | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query) | Q(user__email__icontains=query)
        )
        invitations = invitations.filter(email__icontains=query)
    if role_filter:
        memberships = memberships.filter(role=role_filter)
        invitations = invitations.filter(role=role_filter)
    if status_filter == "active":
        memberships = memberships.filter(status=TenantMembership.Status.ACTIVE, is_active=True)
        invitations = invitations.none()
    elif status_filter == "inactive":
        memberships = memberships.exclude(status=TenantMembership.Status.ACTIVE)
        invitations = invitations.none()
    elif status_filter == "pending":
        memberships = memberships.none()

    actor_membership = active_membership_for(request.user, tenant)
    allowed_roles = grantable_roles_for(actor_membership)
    role_labels = dict(TenantMembership.Role.choices)
    member_rows = []
    for membership in memberships:
        member_rows.append({
            "kind": "member",
            "membership": membership,
            "name": membership.user.get_full_name() or membership.user.get_username(),
            "email": membership.user.email,
            "role": membership.role,
            "role_label": membership.get_role_display(),
            "status": membership.status,
            "status_label": membership.get_status_display(),
            "date": membership.joined_at or membership.created_at,
            "can_manage": bool(
                actor_membership and membership.pk != actor_membership.pk
                and role_level(membership.role) < role_level(actor_membership.role)
            ),
        })
    invitation_rows = [{
        "kind": "invitation",
        "invitation": pending,
        "name": "Pending invitation",
        "email": pending.email,
        "role": pending.role,
        "role_label": pending.get_role_display(),
        "status": pending.status,
        "status_label": pending.get_status_display(),
        "date": pending.invited_at,
        "can_manage": True,
    } for pending in invitations]
    return {
        "tenant": tenant,
        "form": form or TenantInvitationForm(),
        "memberships": memberships,
        "invitations": invitations,
        "team_rows": sorted(member_rows + invitation_rows, key=lambda row: (row["email"].lower(), row["kind"])),
        "query": query,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "role_choices": TenantMembership.Role.choices,
        "grantable_role_choices": [(role, role_labels[role]) for role in allowed_roles],
        "created_invitation": created_invitation,
        "invite_url": None if created_invitation is None or not (
            settings.DEBUG or getattr(created_invitation, "_delivery_failed", False)
        ) else _external_url(request, reverse("accept_tenant_invitation", args=[created_invitation.token])),
        "invite_sent_to": None if created_invitation is None else created_invitation.email,
        "invite_delivery_failed": bool(created_invitation and getattr(created_invitation, "_delivery_failed", False)),
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
        limited, retry_after = consume(
            "invitation", f"tenant:{tenant.pk}", f"user:{request.user.pk}", f"ip:{client_ip(request)}"
        )
        if limited:
            security_logger.warning("invitation_rate_limited tenant=%s actor=%s", tenant.pk, request.user.pk)
            return _rate_limited_response(request, retry_after)
        form = TenantInvitationForm(request.POST)
        context["form"] = form
        if form.is_valid():
            try:
                created_invitation = create_tenant_invitation(
                    tenant=tenant, email=form.cleaned_data["email"], role=form.cleaned_data["role"],
                    invited_by=request.user, notes=form.cleaned_data["notes"],
                    base_url=settings.PUBLIC_BASE_URL or request.build_absolute_uri("/"),
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
@require_POST
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
@require_POST
def resend_invitation(request, invitation_id):
    tenant = getattr(request, "tenant", None)
    limited, retry_after = consume(
        "invitation", f"tenant:{tenant.pk}", f"user:{request.user.pk}", f"ip:{client_ip(request)}"
    )
    if limited:
        return _rate_limited_response(request, retry_after)
    invitation = get_object_or_404(TenantInvitation, pk=invitation_id, tenant=tenant, is_active=True, accepted_at__isnull=True, revoked_at__isnull=True)
    old_id = invitation.pk
    revoke_tenant_invitation(invitation=invitation, revoked_by=request.user)
    replacement = create_tenant_invitation(
        tenant=tenant, email=invitation.email, role=invitation.role, invited_by=request.user,
        notes=invitation.notes, base_url=settings.PUBLIC_BASE_URL or request.build_absolute_uri("/"),
    )
    log_audit_event(tenant=tenant, actor=request.user, action=AuditEvent.Action.INVITATION_RESENT, target=replacement, metadata={"replaces_invitation_id": old_id})
    context = _tenant_context(request, created_invitation=replacement)
    template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
    return _html_response(request, template, context)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN, action_name="change member roles")
@require_POST
def change_member_role(request, membership_id):
    tenant = getattr(request, "tenant", None)
    membership = get_object_or_404(TenantMembership, pk=membership_id, tenant=tenant)
    actor_membership = active_membership_for(request.user, tenant)
    form = MembershipRoleForm(request.POST, allowed_roles=grantable_roles_for(actor_membership))
    if not form.is_valid():
        return HttpResponse(status=400)
    try:
        change_membership_role_service(membership=membership, new_role=form.cleaned_data["role"], changed_by=request.user)
    except ValueError as exc:
        return HttpResponseForbidden(str(exc))
    return redirect("team-members")


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, TenantMembership.Role.OWNER, TenantMembership.Role.ADMIN, action_name="change member status")
@require_POST
def change_member_status(request, membership_id):
    tenant = getattr(request, "tenant", None)
    membership = get_object_or_404(TenantMembership, pk=membership_id, tenant=tenant)
    form = MembershipStatusForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    try:
        change_membership_status_service(membership=membership, new_status=form.cleaned_data["status"], changed_by=request.user)
    except ValueError as exc:
        return HttpResponseForbidden(str(exc))
    return redirect("team-members")


@login_required
@tenant_role_required(*OWNER_ROLES, action_name="view team members")
def member_detail(request, membership_id):
    membership = get_object_or_404(
        TenantMembership.objects.select_related("user", "invited_by"),
        pk=membership_id,
        tenant=getattr(request, "tenant", None),
    )
    return _html_response(request, "accounts/member_detail.html", {"membership": membership, "tenant": request.tenant})


def _settings_forms(tenant, *, bound_section=None, data=None):
    initial = {
        "business": {field: getattr(tenant, field) for field in (
            "name", "contact_email", "contact_phone", "address", "tax_identification_number", "vat_registration_number"
        )},
        "regional": {field: getattr(tenant, field) for field in ("currency", "timezone")},
        "receipt": {field: getattr(tenant, field) for field in ("receipt_business_details", "receipt_footer", "receipt_prefix")},
        "operational": {field: getattr(tenant, field) for field in ("default_track_inventory", "default_reorder_level")},
    }
    classes = {
        "business": WorkspaceBusinessProfileForm,
        "regional": WorkspaceRegionalForm,
        "receipt": WorkspaceReceiptForm,
        "operational": WorkspaceOperationalForm,
    }
    forms = {}
    for section, form_class in classes.items():
        kwargs = {"initial": initial[section]}
        if section == bound_section:
            kwargs["data"] = data
        if section == "business":
            kwargs["tenant"] = tenant
        forms[section] = form_class(**kwargs)
    return forms


@login_required
@tenant_role_required(*OWNER_ROLES, action_name="manage workspace settings")
def workspace_settings(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return redirect("dashboard")
    section = request.POST.get("section") if request.method == "POST" else None
    forms = _settings_forms(tenant, bound_section=section, data=request.POST if section else None)
    if request.method == "POST":
        form = forms.get(section)
        if form is None:
            return HttpResponse(status=400)
        if form.is_valid():
            update_workspace_settings(tenant=tenant, actor=request.user, section=section, values=form.cleaned_data)
            messages.success(request, f"{section.title()} settings updated.")
            return redirect("workspace-settings")
        return _html_response(request, "tenants/settings.html", {"tenant": tenant, "settings_forms": forms}, status=400)
    return _html_response(request, "tenants/settings.html", {"tenant": tenant, "settings_forms": forms})


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
    previous_tenant_id = request.session.get("current_tenant_id")
    request.session.cycle_key()
    request.session["current_tenant_id"] = membership.tenant_id
    log_audit_event(tenant=membership.tenant, actor=request.user, action=AuditEvent.Action.WORKSPACE_SWITCHED, target=membership, metadata={"from_tenant_id": previous_tenant_id, "to_tenant_id": membership.tenant_id})
    return redirect("dashboard")


def _invitation_for_token(token):
    """Resolve a secret invitation token independently of the browser's current tenant."""
    return get_object_or_404(
        TenantInvitation._base_manager.select_related("tenant"),
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )


def accept_invitation(request, token):
    if request.method == "POST":
        limited, retry_after = consume(
            "invitation_token", f"ip:{client_ip(request)}", f"token:{opaque(token)}"
        )
        if limited:
            return _rate_limited_response(request, retry_after)
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
    if request.method == "POST":
        limited, retry_after = consume(
            "invitation_token", f"ip:{client_ip(request)}", f"token:{opaque(token)}"
        )
        if limited:
            return _rate_limited_response(request, retry_after)
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
