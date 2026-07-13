from django.urls import path

from accounts.views import TenantLoginView, TenantLogoutView, accept_invitation, revoke_invitation, team_members


urlpatterns = [
    path("login/", TenantLoginView.as_view(), name="login"),
    path("logout/", TenantLogoutView.as_view(), name="logout"),
    path("team/", team_members, name="team-members"),
    path("invitations/<str:token>/accept/", accept_invitation, name="accept_tenant_invitation"),
    path("invitations/<int:invitation_id>/revoke/", revoke_invitation, name="revoke_tenant_invitation"),
]
