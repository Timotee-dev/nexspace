from rest_framework import authentication, exceptions

from .models import DeviceToken, hash_key


class DeviceTokenAuthentication(authentication.BaseAuthentication):
    """`Authorization: Bearer <token>` from the NexSpace app. No cookies, so no CSRF."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1").split()
        if len(header) != 2 or header[0] != self.keyword:
            return None
        token = DeviceToken.objects.select_related("user").filter(key_hash=hash_key(header[1])).first()
        if token is None or not token.user.is_active:
            raise exceptions.AuthenticationFailed("Your session has ended. Please sign in again.")
        token.touch()
        return token.user, token

    def authenticate_header(self, request):
        # Only the app (which sends a Bearer token) gets 401 "sign in again";
        # the website keeps getting 403 like before.
        header = authentication.get_authorization_header(request).decode("latin-1").split()
        if header and header[0] == self.keyword:
            return self.keyword
        return None
