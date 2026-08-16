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
