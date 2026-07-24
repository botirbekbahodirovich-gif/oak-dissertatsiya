"""Shared security helpers used by authentication-sensitive routes."""
from functools import wraps
import re
import uuid

from flask import abort, g, has_request_context, request
from flask_login import current_user

_REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
_CSP = "; ".join((
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://telegram.org https://mc.yandex.ru",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net",
    "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net",
    "img-src 'self' data: blob: https:",
    "connect-src 'self' https://www.google-analytics.com https://region1.google-analytics.com https://mc.yandex.ru",
    "frame-src https://oauth.telegram.org https://telegram.org",
    "upgrade-insecure-requests",
))


def init_security(app, production):
    """Attach request IDs, HTTPS enforcement, and production response headers."""
    @app.before_request
    def _security_before_request():
        supplied = request.headers.get('X-Request-ID', '')
        g.request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
        if production and not request.is_secure:
            abort(400, description="Production so'rovi HTTPS orqali yuborilishi kerak")

    @app.after_request
    def _security_after_request(response):
        response.headers['X-Request-ID'] = getattr(g, 'request_id', uuid.uuid4().hex)
        if production:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
            response.headers['Content-Security-Policy'] = _CSP
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
            response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        return response


def log_exception(logger, message):
    """Log the active exception with a correlation ID, never client-visible details."""
    request_id = getattr(g, 'request_id', '-') if has_request_context() else '-'
    logger.exception('%s request_id=%s', message, request_id)


def admin_required(view):
    """Require an authenticated administrator.

    Keep ``@login_required`` outside this decorator so anonymous browser
    requests retain Flask-Login's normal redirect/401 behaviour.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not getattr(current_user, 'is_admin', False):
            abort(403)
        return view(*args, **kwargs)
    return wrapped
