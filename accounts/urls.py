from django.urls import path

from accounts.views import (TenantLoginView, TenantLogoutView, accept_invitation, change_member_role, change_member_status,
    create_invitation_account, member_detail, onboarding_setup, resend_invitation, resend_verification, revoke_invitation,
    dismiss_onboarding, resume_onboarding, signup, signup_success, switch_workspace, team_members, verify_email,
    verify_required, workspace_settings)


urlpatterns = [
    path("signup/", signup, name="signup"),
    path("signup/success/", signup_success, name="signup_success"),
    path("verify/<str:token>/", verify_email, name="verify_email"),
    path("verify-required/", verify_required, name="verify_required"),
    path("verify/resend/", resend_verification, name="resend_verification"),
    path("onboarding/<int:step>/", onboarding_setup, name="onboarding_setup"),
    path("onboarding/dismiss/", dismiss_onboarding, name="dismiss_onboarding"),
    path("onboarding/resume/", resume_onboarding, name="resume_onboarding"),
    path("login/", TenantLoginView.as_view(), name="login"),
    path("logout/", TenantLogoutView.as_view(), name="logout"),
    path("team/", team_members, name="team-members"),
    path("invitations/<str:token>/accept/", accept_invitation, name="accept_tenant_invitation"),
    path("invitations/<str:token>/create-account/", create_invitation_account, name="create_invitation_account"),
    path("invitations/<int:invitation_id>/revoke/", revoke_invitation, name="revoke_tenant_invitation"),
    path("invitations/<int:invitation_id>/resend/", resend_invitation, name="resend_tenant_invitation"),
    path("team/members/<int:membership_id>/role/", change_member_role, name="change_member_role"),
    path("team/members/<int:membership_id>/status/", change_member_status, name="change_member_status"),
    path("team/members/<int:membership_id>/", member_detail, name="member-detail"),
    path("settings/", workspace_settings, name="workspace-settings"),
    path("workspaces/<int:tenant_id>/switch/", switch_workspace, name="switch_workspace"),
]
