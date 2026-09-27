from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user

def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if getattr(current_user, "is_banned", False):
            return redirect(url_for("auth.logout"))
        if not current_user.is_admin:
            if getattr(current_user, "is_judge", False):
                return redirect(url_for("admin.submissions"))
            abort(403)
        return view(*args, **kwargs)

    return wrapper

def judge_required(view):

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if getattr(current_user, "is_banned", False):
            return redirect(url_for("auth.logout"))
        if not (current_user.is_admin or current_user.is_judge):
            abort(403)
        return view(*args, **kwargs)

    return wrapper

def participant_required(view):

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        if getattr(current_user, "is_banned", False):
            flash("Your account has been suspended. Please contact the administrator.", "error")
            return redirect(url_for("auth.logout"))
        return view(*args, **kwargs)

    return wrapper
