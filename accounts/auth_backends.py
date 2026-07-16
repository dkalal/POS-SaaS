from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrUsernameModelBackend(ModelBackend):
    """Authenticate the project's default User by username or unique email."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        identifier = str(username).strip()
        UserModel = get_user_model()
        if "@" not in identifier:
            return super().authenticate(request, username=identifier, password=password, **kwargs)

        matches = UserModel._default_manager.filter(email__iexact=identifier)
        # Ambiguous email identity must fail closed; never guess between accounts.
        if matches.count() != 1:
            return None
        user = matches.first()
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
