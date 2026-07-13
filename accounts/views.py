from contextlib import contextmanager

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.test.signals import template_rendered
from django.urls import reverse

from accounts.forms import SaaSAuthenticationForm, TenantInvitationForm
from accounts.models import TenantInvitation, TenantMembership
from accounts.rbac import tenant_role_required
from accounts.services import accept_tenant_invitation, create_tenant_invitation, revoke_tenant_invitation
from tenants.models import Tenant


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


class TenantLogoutView(LogoutView):
    def post(self, request, *args, **kwargs):
        messages.success(request, "You have been signed out.")
        return super().post(request, *args, **kwargs)


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
        "invite_url": None if created_invitation is None else request.build_absolute_uri(
            reverse("accept_tenant_invitation", args=[created_invitation.token])
        ),
        "invite_sent_to": None if created_invitation is None else created_invitation.email,
        "accept_error": accept_error,
        "invitation": invitation,
        "can_manage_members": True,
    }


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, action_name="invite team members")
def team_members(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return redirect("dashboard")

    context = _tenant_context(request)
    if request.method == "POST":
        form = TenantInvitationForm(request.POST)
        context["form"] = form
        if form.is_valid():
            created_invitation = create_tenant_invitation(
                tenant=tenant,
                email=form.cleaned_data["email"],
                role=form.cleaned_data["role"],
                invited_by=request.user,
                notes=form.cleaned_data["notes"],
            )
            context = _tenant_context(request, created_invitation=created_invitation)
            template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
            return _html_response(request, template, context)
        template = "accounts/partials/invitation_panel.html" if _is_htmx(request) else "accounts/team.html"
        return _html_response(request, template, context, status=400)

    template = "accounts/team.html"
    return _html_response(request, template, context)


@login_required
@tenant_role_required(TenantMembership.Role.OWNER_ADMIN, action_name="revoke team invitations")
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
def accept_invitation(request, token):
    invitation = get_object_or_404(TenantInvitation.objects.select_related("tenant"), token=token)
    tenant = invitation.tenant
    if not tenant.is_active:
        return HttpResponseForbidden("This tenant is not active.")

    accept_error = None
    can_accept = (request.user.email or "").strip().lower() == invitation.email.lower()
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
            "accept_error": accept_error,
            "can_accept": can_accept,
        },
    )
