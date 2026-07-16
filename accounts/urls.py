from django.urls import path

from accounts.views import (TenantLoginView, TenantLogoutView, accept_invitation, change_member_role, change_member_status,
    create_invitation_account, onboarding_setup, resend_invitation, resend_verification, revoke_invitation, signup,
    signup_success, switch_workspace, team_members, verify_email, verify_required)


urlpatterns = [
    path("signup/", signup, name="signup"),
    path("signup/success/", signup_success, name="signup_success"),
    path("verify/<str:token>/", verify_email, name="verify_email"),
    path("verify-required/", verify_required, name="verify_required"),
    path("verify/resend/", resend_verification, name="resend_verification"),
    path("onboarding/<int:step>/", onboarding_setup, name="onboarding_setup"),
    path("login/", TenantLoginView.as_view(), name="login"),
    path("logout/", TenantLogoutView.as_view(), name="logout"),
    path("team/", team_members, name="team-members"),
    path("invitations/<str:token>/accept/", accept_invitation, name="accept_tenant_invitation"),
    path("invitations/<str:token>/create-account/", create_invitation_account, name="create_invitation_account"),
    path("invitations/<int:invitation_id>/revoke/", revoke_invitation, name="revoke_tenant_invitation"),
    path("invitations/<int:invitation_id>/resend/", resend_invitation, name="resend_tenant_invitation"),
    path("team/members/<int:membership_id>/role/", change_member_role, name="change_member_role"),
    path("team/members/<int:membership_id>/status/", change_member_status, name="change_member_status"),
    path("workspaces/<int:tenant_id>/switch/", switch_workspace, name="switch_workspace"),
]
