import re

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user

from extensions import db, limiter
from models import SiteSettings, User, write_audit
from models_gdpr import REQUEST_TYPES, DataRequest
from services.email import _branded_html_email, send_notification_email

bp = Blueprint("gdpr", __name__, url_prefix="/gdpr")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_DEFAULT_ACCENT = "#f27d00"

def _client_ip() -> str:
    from middleware.security import client_ip
    return client_ip(request)

def _mail_cfg() -> dict:
    cfg = current_app.config
    try:
        accent = SiteSettings.get("theme_accent", _DEFAULT_ACCENT) or _DEFAULT_ACCENT
        if not re.match(r'^#[0-9a-fA-F]{6}$', accent):
            accent = _DEFAULT_ACCENT
    except Exception:
        accent = _DEFAULT_ACCENT
    return {
        "provider": cfg.get("MAIL_PROVIDER", "smtp"),
        "host": cfg.get("SMTP_HOST"),
        "port": cfg.get("SMTP_PORT"),
        "user": cfg.get("SMTP_USER"),
        "password": cfg.get("SMTP_PASSWORD"),
        "use_tls": cfg.get("SMTP_USE_TLS", True),
        "brevo_api_key": cfg.get("BREVO_API_KEY"),
        "ses_access_key": cfg.get("SES_ACCESS_KEY_ID"),
        "ses_secret_key": cfg.get("SES_SECRET_ACCESS_KEY"),
        "ses_region": cfg.get("SES_REGION"),
        "from_name": cfg.get("MAIL_FROM_NAME") or cfg.get("SMTP_FROM_NAME"),
        "from_address": cfg.get("MAIL_FROM_ADDRESS"),
        "app_name": cfg.get("APP_NAME", "prompt-a-thon"),
        "accent": accent,
        "base_url": (cfg.get("BASE_URL") or "").rstrip("/"),
        "tracking_enabled": SiteSettings.get("email_tracking_enabled", "1") != "0",
        "log_body": SiteSettings.get("email_log_bodies", "1") != "0",
    }

@bp.route("/data-request", methods=["GET", "POST"])
@limiter.limit("3 per hour; 10 per day")
def data_request():

    if current_user.is_authenticated and (current_user.is_admin or current_user.is_judge):
        contact = current_app.config.get("CONTACT_EMAIL") or "hello@prompt-a-thon.bitilia.com"
        return render_template(
            "gdpr/data_request.html",
            request_types=REQUEST_TYPES,
            blocked=True,
            contact=contact,
        )

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if current_user.is_authenticated and not email:
            email = current_user.email
        request_type = (request.form.get("request_type") or "").strip()
        detail = (request.form.get("detail") or "").strip()[:2000] or None

        if not email or not EMAIL_RE.match(email) or len(email) > 254:
            flash("Please enter a valid email address.", "error")
            return render_template(
                "gdpr/data_request.html", request_types=REQUEST_TYPES,
            ), 400

        if request_type not in REQUEST_TYPES:
            flash("Please choose a valid request type.", "error")
            return render_template(
                "gdpr/data_request.html", request_types=REQUEST_TYPES,
            ), 400

        _target_user = User.query.filter_by(email=email).first()
        _is_participant = _target_user and not (_target_user.is_admin or _target_user.is_judge)
        if not _is_participant:
            contact = current_app.config.get("CONTACT_EMAIL") or "hello@prompt-a-thon.bitilia.com"
            return render_template(
                "gdpr/data_request.html",
                request_types=REQUEST_TYPES,
                blocked=True,
                contact=contact,
            )

        record = DataRequest(
            user_id=current_user.id if current_user.is_authenticated else None,
            email=email,
            request_type=request_type,
            detail=detail,
            status="pending",
        )
        db.session.add(record)
        write_audit(
            "gdpr.data_request",
            actor=current_user if current_user.is_authenticated else None,
            target_type="data_request",
            target_id=None,
            detail=request_type,
            ip=_client_ip(),
        )
        db.session.commit()

        app_name = current_app.config.get("APP_NAME", "prompt-a-thon")
        contact = current_app.config.get("CONTACT_EMAIL") or ""
        req_label = REQUEST_TYPES[request_type]
        body_text = (
            f"We received your {req_label} request.\n\n"
            f"We will respond within 30 days as required by GDPR.\n"
            f"If you need to add information, contact {contact}.\n"
        )
        mail_cfg = _mail_cfg()
        inner_html = (
            f"<p>We have received your <strong>{req_label}</strong> request.</p>"
            f"<p>We will respond within 30 days as required by GDPR.</p>"
            + (f"<p>If you need to add information, contact "
               f'<a href="mailto:{contact}" style="color:#f27d00;">{contact}</a>.</p>'
               if contact else "")
        )
        body_html = _branded_html_email(
            app_name, inner_html,
            accent=mail_cfg.get("accent", _DEFAULT_ACCENT),
            base_url=mail_cfg.get("base_url", ""),
        )
        send_notification_email(
            mail_cfg, email, f"{app_name} — data request received",
            body_html, body_text,
            category="gdpr",
        )

        flash("Your request has been received. We will respond within 30 days.", "success")
        return redirect(url_for("public.index"))

    return render_template("gdpr/data_request.html", request_types=REQUEST_TYPES)
