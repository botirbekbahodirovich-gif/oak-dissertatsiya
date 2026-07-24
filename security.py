"""Shared security helpers used by authentication-sensitive routes."""
from functools import wraps

from flask import abort
from flask_login import current_user


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
