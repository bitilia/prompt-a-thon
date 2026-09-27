from flask import Blueprint, Response, render_template, request

from models import SiteSettings
from services.academy import get_modules
from services.tasks import get_task_list

import json as _json

bp = Blueprint("public", __name__)

def _get_team():
    raw = SiteSettings.get("team_members", "[]")
    try:
        return _json.loads(raw)
    except Exception:
        return []

@bp.route("/")
def index():
    timeline = {
        "registration_open": SiteSettings.get("registration_open_date", ""),
        "registration_close": SiteSettings.get("registration_close_date", ""),
        "event_day": SiteSettings.get("event_day_date", ""),
        "submission_deadline": SiteSettings.get("submission_deadline_date", ""),
        "results_date": SiteSettings.get("results_date", ""),
    }
    team = _get_team()
    return render_template(
        "index.html",
        tasks=get_task_list(),
        modules=get_modules(),
        timeline=timeline,
        registration_open=SiteSettings.registration_is_open(),
        team_made_by=[m for m in team if m.get("category") == "made_by"],
        team_staff=[m for m in team if m.get("category") == "staff"],
        team_any=bool(team),
    )

@bp.route("/privacy")
def privacy():
    return render_template("static_pages/privacy.html")

@bp.route("/terms")
def terms():
    return render_template("static_pages/terms.html")

import os as _os

def _load_email_png() -> bytes:
    path = _os.path.join(_os.path.dirname(__file__), '..', 'static', 'email-img-logo.png')
    try:
        with open(path, 'rb') as _f:
            return _f.read()
    except Exception:
        return b''

_EMAIL_LOGO_PNG = _load_email_png()

@bp.route("/email-icon.svg")
def email_icon_legacy():

    return email_logo_png()

@bp.route("/email-img-logo.png")
def email_logo_png():

    pixel_id = request.args.get("pixel", "").strip()
    if pixel_id:
        try:
            from extensions import db
            from models import EmailLog, utcnow
            entry = EmailLog.query.filter_by(tracking_pixel_id=pixel_id).first()
            if entry and entry.opened_at is None:
                entry.opened_at = utcnow()
                db.session.commit()
        except Exception:
            try:
                from extensions import db as _db
                _db.session.rollback()
            except Exception:
                pass

    return Response(
        _EMAIL_LOGO_PNG,
        mimetype="image/png",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
