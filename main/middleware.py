import time
from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date


DASHBOARD_SESSION_COOKIE_NAME = getattr(settings, 'DASHBOARD_SESSION_COOKIE_NAME', 'dashboard_sessionid')
FRONTEND_SESSION_COOKIE_NAME = getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid')


def get_session_cookie_name(request):
    """
    Returns the isolated session cookie name based on request URL prefix.
    /dashboard/... -> 'dashboard_sessionid'
    All other URLs -> 'sessionid'
    """
    path = getattr(request, 'path_info', '') or getattr(request, 'path', '') or ''
    if path.startswith('/dashboard'):
        return DASHBOARD_SESSION_COOKIE_NAME
    return FRONTEND_SESSION_COOKIE_NAME


class ScopedSessionMiddleware(SessionMiddleware):
    """
    Provides complete Session & Authentication Isolation between Dashboard and Frontend.
    Dashboard requests use 'dashboard_sessionid' while Frontend requests use 'sessionid'.
    Logging in or logging out on Dashboard does not affect the Frontend session, and vice versa.
    """

    def process_request(self, request):
        cookie_name = get_session_cookie_name(request)
        request._session_cookie_name = cookie_name
        session_key = request.COOKIES.get(cookie_name)

        # If dashboard session cookie is not set, check if a staff user session exists in default sessionid (e.g. client.force_login)
        if not session_key and cookie_name == DASHBOARD_SESSION_COOKIE_NAME and FRONTEND_SESSION_COOKIE_NAME in request.COOKIES:
            fallback_key = request.COOKIES.get(FRONTEND_SESSION_COOKIE_NAME)
            temp_session = self.SessionStore(fallback_key)
            from django.contrib.auth import SESSION_KEY
            if SESSION_KEY in temp_session:
                user_id = temp_session[SESSION_KEY]
                from django.contrib.auth import get_user_model
                UserModel = get_user_model()
                if UserModel.objects.filter(pk=user_id, is_staff=True).exists():
                    session_key = fallback_key

        request.session = self.SessionStore(session_key)

    def process_response(self, request, response):
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            return response

        cookie_name = getattr(request, '_session_cookie_name', None) or get_session_cookie_name(request)

        if cookie_name in request.COOKIES and empty:
            response.delete_cookie(
                cookie_name,
                path=settings.SESSION_COOKIE_PATH,
                domain=settings.SESSION_COOKIE_DOMAIN,
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
            patch_vary_headers(response, ("Cookie",))
        else:
            if accessed:
                patch_vary_headers(response, ("Cookie",))
            if (modified or settings.SESSION_SAVE_EVERY_REQUEST) and not empty:
                if request.session.get_expire_at_browser_close():
                    max_age = None
                    expires = None
                else:
                    max_age = request.session.get_expiry_age()
                    expires_time = time.time() + max_age
                    expires = http_date(expires_time)

                if response.status_code != 500:
                    try:
                        request.session.save()
                    except Exception:
                        pass
                    response.set_cookie(
                        cookie_name,
                        request.session.session_key,
                        max_age=max_age,
                        expires=expires,
                        domain=settings.SESSION_COOKIE_DOMAIN,
                        path=settings.SESSION_COOKIE_PATH,
                        secure=settings.SESSION_COOKIE_SECURE or None,
                        httponly=settings.SESSION_COOKIE_HTTPONLY or None,
                        samesite=settings.SESSION_COOKIE_SAMESITE,
                    )
        return response


class SecurityHeadersMiddleware:
    """
    Production-grade security headers middleware:
    - Permissions-Policy
    - Cross-Origin-Opener-Policy
    - X-Content-Type-Options: nosniff
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if 'Permissions-Policy' not in response:
            response['Permissions-Policy'] = 'accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), usb=()'
        if 'Cross-Origin-Opener-Policy' not in response:
            response['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
        if 'X-Content-Type-Options' not in response:
            response['X-Content-Type-Options'] = 'nosniff'
        return response
