import re
import secrets
from urllib.parse import urlparse

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from extensions import db, limiter
from models import (
    AUTH_EMAIL, AUTH_MICROSOFT, ROLE_ADMIN, ROLE_PARTICIPANT,
    OTPToken, SiteSettings, User, write_audit,
)
from services.email import send_otp_email

bp = Blueprint("auth", __name__, url_prefix="/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OTP_RE = re.compile(r"^\d{6}$")

def _client_ip() -> str:
    from middleware.security import client_ip
    return client_ip(request)

def _mail_cfg() -> dict:

    cfg = current_app.config
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
        "accent": SiteSettings.get("theme_accent", "#f27d00"),
    }

def _is_safe_next(target: str) -> bool:
    if not target:
        return False
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    return target.startswith("/") and not target.startswith("//")

_SETUP_PRIORITY = {"admin": 2, "judge": 1, "participant": 0}

def _needs_setup(user) -> bool:

    if not (user.is_admin or user.is_judge):
        return False
    done = SiteSettings.get(f"setup_done_u{user.id}")
    return _SETUP_PRIORITY.get(user.role, 0) > _SETUP_PRIORITY.get(done, -1)

def _post_login_redirect():
    nxt = request.args.get("next") or session.pop("post_login_next", None)
    if nxt and _is_safe_next(nxt):
        return redirect(nxt)
    if current_user.is_authenticated and (current_user.is_admin or current_user.is_judge):
        if _needs_setup(current_user):
            return redirect(url_for("admin.setup_wizard"))
    if current_user.is_authenticated and current_user.is_admin:
        return redirect(url_for("admin.index"))
    return redirect(url_for("participant.index"))

@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return _post_login_redirect()

    ms_enabled = bool(current_app.config.get("MICROSOFT_CLIENT_ID"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email or not EMAIL_RE.match(email) or len(email) > 254:
            flash("Please enter a valid email address.", "error")
            return render_template("auth/login.html", ms_enabled=ms_enabled), 400

        nxt = request.args.get("next") or request.form.get("next")
        if nxt and _is_safe_next(nxt):
            session["post_login_next"] = nxt

        admin_email = (current_app.config.get("ADMIN_EMAIL") or "").strip().lower()

        user = User.query.filter_by(email=email).first()
        if user is None:
            if not SiteSettings.registration_is_open() and email != admin_email:
                flash("Registration is currently closed.", "warning")
                return render_template("auth/login.html", ms_enabled=ms_enabled), 403

            if email != admin_email and not SiteSettings.email_is_allowed(email):
                flash("Your email domain is not on the allowlist for this site. "
                      "Contact the administrator if you believe this is an error.",
                      "error")
                write_audit("auth.login_denied_domain", target_type="email",
                            target_id=None, detail=email, ip=_client_ip())
                db.session.commit()
                return render_template("auth/login.html", ms_enabled=ms_enabled), 403
            user = User(
                email=email,
                role=ROLE_ADMIN if email == admin_email else ROLE_PARTICIPANT,
                auth_provider=AUTH_EMAIL,
            )
            db.session.add(user)
            db.session.flush()
            write_audit("user.created", actor=user, target_type="user",
                        target_id=user.id, ip=_client_ip())
        else:

            if email == admin_email and user.role != ROLE_ADMIN:
                user.role = ROLE_ADMIN
                write_audit("user.promoted_admin_auto", actor=user,
                            target_type="user", target_id=user.id, ip=_client_ip())
            if user.is_banned:

                flash("If that email exists and is allowed, a code has been sent.", "info")
                write_audit("auth.login_blocked_banned", actor=user, ip=_client_ip())
                db.session.commit()
                return redirect(url_for("auth.login"))

        token, code = OTPToken.generate(
            user,
            current_app.config["OTP_EXPIRY_MINUTES"],
            _client_ip(),
        )
        db.session.commit()

        ok, err = send_otp_email(
            _mail_cfg(),
            user.email,
            code,
            current_app.config["APP_NAME"],
            current_app.config["OTP_EXPIRY_MINUTES"],
        )
        if not ok:
            current_app.logger.error("OTP email failed for %s: %s", user.email, err)
            flash("We could not send your code right now. Please try again shortly.", "error")
            return render_template("auth/login.html", ms_enabled=ms_enabled), 500

        session["otp_user_id"] = user.id
        session["otp_token_id"] = token.id
        flash("We sent a 6-digit code to your email. It expires in "
              f"{current_app.config['OTP_EXPIRY_MINUTES']} minutes.", "info")
        return redirect(url_for("auth.verify"))

    return render_template("auth/login.html", ms_enabled=ms_enabled)

@bp.route("/verify", methods=["GET", "POST"])
@limiter.limit("20 per minute")
def verify():
    if current_user.is_authenticated:
        return _post_login_redirect()

    user_id = session.get("otp_user_id")
    token_id = session.get("otp_token_id")
    if not user_id or not token_id:
        flash("Please start the sign-in flow again.", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if not OTP_RE.match(code):
            flash("Please enter the 6-digit code from your email.", "error")
            return render_template("auth/verify.html"), 400

        token = db.session.get(OTPToken, token_id)
        user = db.session.get(User, user_id)
        if token is None or user is None or token.user_id != user.id:
            session.pop("otp_user_id", None)
            session.pop("otp_token_id", None)
            flash("That code is no longer valid. Please request a new one.", "error")
            return redirect(url_for("auth.login"))

        max_attempts = current_app.config["MAX_OTP_ATTEMPTS"]
        if token.verify(code, max_attempts):
            if user.is_banned:
                db.session.commit()
                flash("Your account has been suspended. Please contact the administrator.", "error")
                return redirect(url_for("auth.login"))
            user.update_login(_client_ip())
            write_audit("auth.login_success", actor=user, ip=_client_ip())
            db.session.commit()
            login_user(user, remember=False)
            session.pop("otp_user_id", None)
            session.pop("otp_token_id", None)
            session.permanent = True
            flash("Signed in successfully.", "success")
            return _post_login_redirect()

        write_audit("auth.login_failed", actor=user, ip=_client_ip(),
                    detail=f"attempts={token.attempts}")
        db.session.commit()
        if token.used:
            session.pop("otp_user_id", None)
            session.pop("otp_token_id", None)
            flash("That code is no longer valid. Please request a new one.", "error")
            return redirect(url_for("auth.login"))
        flash("Incorrect code. Please try again.", "error")
        return render_template("auth/verify.html"), 400

    return render_template("auth/verify.html")

@bp.route("/logout")
def logout():
    if current_user.is_authenticated:
        write_audit("auth.logout", actor=current_user, ip=_client_ip())
        db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("public.index"))

def _msal_app():
    import msal

    client_id = current_app.config.get("MICROSOFT_CLIENT_ID")
    client_secret = current_app.config.get("MICROSOFT_CLIENT_SECRET")
    tenant = current_app.config.get("MICROSOFT_TENANT_ID") or "common"
    authority = f"https://login.microsoftonline.com/{tenant}"
    if not client_id or not client_secret:
        return None
    return msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )

@bp.route("/microsoft")
@limiter.limit("10 per minute")
def microsoft_start():
    msapp = _msal_app()
    if msapp is None:
        flash("Microsoft sign-in is not configured.", "error")
        return redirect(url_for("auth.login"))

    state = secrets.token_urlsafe(32)
    session["ms_state"] = state
    redirect_uri = url_for("auth.microsoft_callback", _external=True)
    auth_url = msapp.get_authorization_request_url(
        scopes=["User.Read"],
        state=state,
        redirect_uri=redirect_uri,
        prompt="select_account",
    )
    return redirect(auth_url)

@bp.route("/microsoft/callback")
@limiter.limit("20 per minute")
def microsoft_callback():
    msapp = _msal_app()
    if msapp is None:
        flash("Microsoft sign-in is not configured.", "error")
        return redirect(url_for("auth.login"))

    expected = session.pop("ms_state", None)
    received = request.args.get("state")
    if not expected or not received or not secrets.compare_digest(expected, received):
        flash("Sign-in session expired. Please try again.", "error")
        return redirect(url_for("auth.login"))

    if request.args.get("error"):
        flash("Microsoft sign-in failed. Please try again.", "error")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    if not code:
        flash("Microsoft sign-in did not return a code.", "error")
        return redirect(url_for("auth.login"))

    redirect_uri = url_for("auth.microsoft_callback", _external=True)
    result = msapp.acquire_token_by_authorization_code(
        code, scopes=["User.Read"], redirect_uri=redirect_uri,
    )
    if not isinstance(result, dict) or "id_token_claims" not in result:
        current_app.logger.error("MS SSO token exchange failed: %s",
                                 result.get("error_description") if isinstance(result, dict) else result)
        flash("Microsoft sign-in failed. Please try again.", "error")
        return redirect(url_for("auth.login"))

    claims = result["id_token_claims"]
    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    name = claims.get("name") or ""
    oid = claims.get("oid") or ""
    if not email or not EMAIL_RE.match(email):
        flash("Microsoft did not return a usable email address.", "error")
        return redirect(url_for("auth.login"))

    admin_email = (current_app.config.get("ADMIN_EMAIL") or "").strip().lower()

    user = None
    if oid:
        user = User.query.filter_by(ms_object_id=oid).first()
    if user is None:
        user = User.query.filter_by(email=email).first()

    if user is None:
        if not SiteSettings.registration_is_open() and email != admin_email:
            flash("Registration is currently closed.", "warning")
            return redirect(url_for("auth.login"))
        user = User(
            email=email,
            name=name[:120] or None,
            role=ROLE_ADMIN if email == admin_email else ROLE_PARTICIPANT,
            auth_provider=AUTH_MICROSOFT,
            ms_object_id=oid or None,
        )
        db.session.add(user)
        db.session.flush()
        write_audit("user.created_microsoft", actor=user, target_type="user",
                    target_id=user.id, ip=_client_ip())
    else:
        if email == admin_email and user.role != ROLE_ADMIN:
            user.role = ROLE_ADMIN
        user.auth_provider = AUTH_MICROSOFT
        if oid and not user.ms_object_id:
            user.ms_object_id = oid
        if name and not user.name:
            user.name = name[:120]

    if user.is_banned:
        flash("Your account has been suspended. Please contact the administrator.", "error")
        write_audit("auth.login_blocked_banned", actor=user, ip=_client_ip())
        db.session.commit()
        return redirect(url_for("auth.login"))

    user.update_login(_client_ip())
    write_audit("auth.login_success_microsoft", actor=user, ip=_client_ip())
    db.session.commit()

    login_user(user, remember=False)
    session.permanent = True
    flash("Signed in via Microsoft.", "success")
    return _post_login_redirect()
