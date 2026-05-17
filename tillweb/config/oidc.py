from django.http import HttpResponseRedirect
from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView


# This context processor is only added to the list of context processors
# when OIDC is configured
def context_processor(request):
    oidc_user = request.session.get(BACKEND_SESSION_KEY) \
        == "tillweb.config.oidc.TillOIDCAuthenticationBackend"
    oidc_user_home = settings.OIDC_USER_HOME_PAGE_TEMPLATE.format(
        user=request.user) if oidc_user else ""
    return {
        'OIDC_ENABLED': True,
        'OIDC_PROVIDER_NAME': settings.OIDC_PROVIDER_NAME,
        'OIDC_USER': oidc_user,
        'OIDC_USER_HOME': oidc_user_home,
    }


class TillOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    def filter_users_by_claims(self, claims):
        # Try to look up an existing user based on the claims
        username = claims.get('preferred_username')
        if not username:
            return self.UserModel.objects.none()

        try:
            user = self.UserModel.objects.get(username=username)
            return [user]
        except self.UserModel.DoesNotExist:
            return self.UserModel.objects.none()

    def verify_claims(self, claims):
        verified = super().verify_claims(claims)
        if 'preferred_username' not in claims:
            verified = False
        groups = frozenset(claims.get("groups", []))
        if any(g not in groups for g in settings.OIDC_REQUIRED_GROUPS):
            verified = False
        return verified

    def _update_user_common(self, user, claims):
        groups = frozenset(claims.get("groups", []))

        # Claims may include "name", "given_name", "family_name" in any
        # combination.
        if "name" in claims:
            names = claims["name"].rsplit(' ', maxsplit=1)
            if len(names) > 1:
                user.first_name, user.last_name = names
            else:
                user.first_name = ''
                user.last_name = full_name
        else:
            user.first_name = ''
            user.last_name = ''
        user.first_name = claims.get('given_name', user.first_name)
        user.last_name = claims.get('family_name', user.last_name)
        user.is_superuser = bool(settings.OIDC_SUPERUSER_GROUPS and all(
            g in groups for g in settings.OIDC_SUPERUSER_GROUPS))
        user.is_staff = bool(settings.OIDC_STAFF_GROUPS and all(
            g in groups for g in settings.OIDC_STAFF_GROUPS))

    def create_user(self, claims):
        user = super().create_user(claims)
        self._update_user_common(user, claims)
        user.username = claims['preferred_username']
        user.save()

        return user

    def update_user(self, user, claims):
        self._update_user_common(user, claims)
        user.save()

        return user


class TillOIDCAuthenticationCallbackView(OIDCAuthenticationCallbackView):
    def get(self, request):
        self.next_url = self.request.session.get("oidc_login_next", None)
        return super().get(request)

    def login_failure(self):
        if self.request.GET.get("error") == "login_required":
            # Default redirects to a static failure URL, but this is
            # just an expired SSO session and we want to redirect back
            # to login to allow re-login

            # See also
            # https://github.com/mozilla/mozilla-django-oidc/issues/458

            return HttpResponseRedirect(self.next_url or self.failure_url)

        return HttpResponseRedirect(self.failure_url)
