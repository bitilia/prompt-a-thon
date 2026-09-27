import logging
import os
from datetime import timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import click
from flask import Flask, render_template, send_from_directory
from sqlalchemy import func, text
from werkzeug.middleware.proxy_fix import ProxyFix

from config import get_config
from extensions import csrf, db, limiter, login_manager
from middleware.security import init_security

def _naive_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)

def purge_unverified_participants() -> int:
    from datetime import timedelta, timezone
    from flask import current_app
    from models import ROLE_PARTICIPANT, User

    cutoff = _naive_utc() - timedelta(minutes=15)
    admin_email = (current_app.config.get("ADMIN_EMAIL") or "").strip().lower()
    stale = User.query.filter(
        User.last_login_at.is_(None),
        User.created_at < cutoff,
        User.role == ROLE_PARTICIPANT,
    ).all()
    deleted = 0
    for user in stale:
        if (user.email or "").strip().lower() == admin_email:
            continue
        db.session.delete(user)
        deleted += 1
    if deleted:
        db.session.commit()
    return deleted

def _site_domain(app: Flask) -> str:

    from urllib.parse import urlparse
    raw = app.config.get("BASE_URL") or ""
    try:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
        return host
    except Exception:
        return ""

def _setup_logging(app: Flask) -> None:
    log_dir = Path(app.root_path) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:

        return
    handler = RotatingFileHandler(
        str(log_dir / "app.log"), maxBytes=5 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    if not app.debug:
        app.logger.setLevel(logging.INFO)

    for _svc in ("services.file_pipeline", "services.conversion_queue"):
        _lg = logging.getLogger(_svc)
        _lg.addHandler(handler)
        if not app.debug:
            _lg.setLevel(logging.INFO)

def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def _400(_e):
        return render_template("errors/400.html"), 400

    @app.errorhandler(403)
    def _403(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def _404(_e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def _413(_e):
        return render_template("errors/413.html"), 413

    @app.errorhandler(429)
    def _429(_e):
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def _500(_e):
        try:
            db.session.rollback()
        except Exception:
            pass
        app.logger.exception("Unhandled 500")
        return render_template("errors/500.html"), 500

def _register_cli(app: Flask) -> None:
    @app.cli.command("init-db")
    def init_db():

        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
        db.create_all()

        from sqlalchemy import inspect, text
        insp = inspect(db.engine)

        def _add_col_if_missing(table: str, col: str, ddl: str) -> None:
            if not insp.has_table(table):
                return
            existing = {c["name"] for c in insp.get_columns(table)}
            if col in existing:
                return
            with db.engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            click.echo(f"Migrated: added {table}.{col}")

        _add_col_if_missing("submissions", "score1", "INTEGER")
        _add_col_if_missing("submissions", "score2", "INTEGER")
        _add_col_if_missing("submissions", "score3", "INTEGER")

        _add_col_if_missing("email_logs", "to_email", "TEXT NOT NULL DEFAULT ''")
        _add_col_if_missing("email_logs", "subject", "TEXT NOT NULL DEFAULT ''")
        _add_col_if_missing("email_logs", "category", "TEXT NOT NULL DEFAULT 'notification'")
        _add_col_if_missing("email_logs", "sent_at", "DATETIME")
        _add_col_if_missing("email_logs", "tracking_pixel_id", "TEXT")
        _add_col_if_missing("email_logs", "opened_at", "DATETIME")
        _add_col_if_missing("email_logs", "body_html", "TEXT")

        _add_col_if_missing("certificates", "recipient_email", "TEXT NOT NULL DEFAULT ''")
        _add_col_if_missing("certificates", "recipient_name", "TEXT")
        _add_col_if_missing("certificates", "competition_name", "TEXT NOT NULL DEFAULT ''")
        _add_col_if_missing("certificates", "school_name", "TEXT NOT NULL DEFAULT ''")
        _add_col_if_missing("certificates", "award_type", "TEXT NOT NULL DEFAULT 'sorry'")
        _add_col_if_missing("certificates", "sent_at", "DATETIME")
        _add_col_if_missing("certificates", "pdf_generated", "BOOLEAN NOT NULL DEFAULT 0")
        _add_col_if_missing("certificates", "email_log_id", "INTEGER")

        from models import SiteSettings
        if SiteSettings.get("email_allowlist_domains") is None:
            admin_email = (app.config.get("ADMIN_EMAIL") or "").strip().lower()
            if admin_email and "@" in admin_email:
                domain = admin_email.rsplit("@", 1)[-1]
                SiteSettings.set_email_allowlist_domains([domain])
                click.echo(f"Seeded email allowlist with domain: {domain}")
        if SiteSettings.get("email_allowlist_mode") is None:
            SiteSettings.set("email_allowlist_mode", "allowlist")
        db.session.commit()
        click.echo("Database initialised.")

    @app.cli.command("create-admin")
    @click.argument("email")
    def create_admin(email):

        from models import ROLE_ADMIN, User, write_audit

        email = (email or "").strip().lower()
        if not email:
            click.echo("Email is required.")
            return
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(email=email, role=ROLE_ADMIN)
            db.session.add(user)
            click.echo(f"Created admin: {email}")
        else:
            user.role = ROLE_ADMIN
            user.is_banned = False
            click.echo(f"Promoted to admin: {email}")
        write_audit("user.promoted_admin_cli", target_type="user")
        db.session.commit()

    @app.cli.command("purge-expired-otps")
    def purge_expired_otps():

        from datetime import datetime, timezone
        from models import OTPToken

        now = _naive_utc()
        deleted = OTPToken.query.filter(
            (OTPToken.used == True) | (OTPToken.expires_at < now)
        ).delete(synchronize_session=False)
        db.session.commit()
        click.echo(f"Deleted {deleted} OTP tokens.")

    @app.cli.command("purge-audit-log")
    def purge_audit_log():

        from datetime import datetime, timedelta as _td, timezone
        from models import AuditLog

        days = int(app.config.get("AUDIT_LOG_RETENTION_DAYS") or 90)
        cutoff = _naive_utc() - _td(days=days)
        deleted = AuditLog.query.filter(
            AuditLog.created_at < cutoff
        ).delete(synchronize_session=False)
        db.session.commit()
        click.echo(f"Deleted {deleted} audit log entries older than {days} days.")

    @app.cli.command("check-update-manifest")
    def check_update_manifest():

        from services.update_checker import check_manifest
        status = check_manifest()
        installed = status.get("installed_version", "unknown")
        inst_status = status.get("installed_status", "unknown")
        latest = status.get("latest")
        click.echo(f"Installed: {installed} ({inst_status})")
        if latest:
            click.echo(f"Latest available: {latest['version']}")
        if "last_check_error" in status:
            click.echo(f"Warning: {status['last_check_error']}")

    @app.cli.command("fail-stuck-submissions")
    def fail_stuck_submissions():

        from datetime import datetime, timedelta, timezone
        from models import Submission
        cutoff = _naive_utc() - timedelta(minutes=15)
        stuck = Submission.query.filter(
            Submission.status == "processing",
            Submission.submitted_at < cutoff,
        ).all()
        if not stuck:
            click.echo("No stuck submissions.")
            return
        for sub in stuck:
            sub.status = "failed"
        db.session.commit()
        click.echo(f"Marked {len(stuck)} submission(s) as failed.")

        try:
            from services.email import send_submission_failed_email
            from services.conversion_queue import _build_mail_cfg
            mail_cfg = _build_mail_cfg(app)
            app_name = app.config.get("APP_NAME", "prompt-a-thon")
            from models import User
            for sub in stuck:
                user = db.session.get(User, sub.user_id)
                if user and user.email:
                    send_submission_failed_email(
                        mail_cfg, user.email, app_name,
                        sub.title or "",
                        "Processing timed out. Please try again.",
                    )
        except Exception as exc:
            click.echo(f"Warning: email notification failed: {exc}")

    @app.cli.command("purge-unverified-users")
    def purge_unverified_users():

        deleted = purge_unverified_participants()
        if deleted:
            click.echo(f"Deleted {deleted} unverified user(s).")
        else:
            click.echo("No stale unverified users found.")

    @app.cli.command("test-mail")
    @click.argument("recipient")
    @click.option("--provider", "provider_override",
                  type=click.Choice(["smtp", "brevo_api", "ses_api"]),
                  default=None, help="Override MAIL_PROVIDER for this test only.")
    @click.option("--port", "port_override", type=int, default=None,
                  help="(SMTP only) Override SMTP_PORT for this test only.")
    @click.option("--probe-only", is_flag=True,
                  help="Only check connectivity / credentials; do not send.")
    def test_mail(recipient, provider_override, port_override, probe_only):

        import socket as _socket
        from services.email import (
            send_otp_email, brevo_account_check, ses_account_check,
        )

        env_path = Path(app.root_path) / ".env"
        click.echo(f".env path: {env_path}")
        if env_path.exists():
            click.echo(".env mail-related lines (verbatim from disk):")
            for line in env_path.read_text().splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                key = stripped.split("=", 1)[0]
                if key in {"MAIL_PROVIDER", "MAIL_FROM_ADDRESS", "MAIL_FROM_NAME",
                          "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM_NAME",
                          "SMTP_USE_TLS", "BREVO_API_KEY",
                          "SES_ACCESS_KEY_ID", "SES_REGION",
                          "ADMIN_EMAIL", "BASE_URL"}:
                    if key in {"BREVO_API_KEY", "SES_SECRET_ACCESS_KEY"}:
                        val = stripped.split("=", 1)[1]
                        click.echo(f"  {key}=<set, {len(val)} chars>" if val else f"  {key}=<empty>")
                    else:
                        click.echo(f"  {stripped}")
        else:
            click.echo("  (.env not found at expected path)")
        click.echo("")

        provider = (provider_override or app.config.get("MAIL_PROVIDER") or "smtp").lower()
        click.echo(f"Active provider: {provider!r}"
                   f"{' (overridden by --provider)' if provider_override else ''}")
        click.echo("")

        if provider == "brevo_api":
            return _run_brevo_diagnostics(app, recipient, probe_only,
                                          send_otp_email, brevo_account_check)
        elif provider == "ses_api":
            return _run_ses_diagnostics(app, recipient, probe_only,
                                        send_otp_email, ses_account_check)
        elif provider in ("smtp", "postfix"):
            return _run_smtp_diagnostics(app, recipient,
                                         port_override or (25 if provider == "postfix" else None),
                                         probe_only, send_otp_email, _socket)
        else:
            click.echo(f"FAILED: unknown provider {provider!r} "
                       f"(expected 'smtp', 'brevo_api', 'ses_api', or 'postfix')",
                       err=True)
            raise SystemExit(1)

    @app.cli.command("test-smtp")
    @click.argument("recipient")
    @click.option("--port", "port_override", type=int, default=None)
    @click.option("--probe-only", is_flag=True)
    @click.pass_context
    def test_smtp_alias(ctx, recipient, port_override, probe_only):

        ctx.invoke(test_mail, recipient=recipient,
                   provider_override="smtp",
                   port_override=port_override,
                   probe_only=probe_only)

def _run_smtp_diagnostics(app, recipient, port_override, probe_only,
                          send_otp_email, _socket):
    cfg = {
        "provider": "smtp",
        "host": app.config.get("SMTP_HOST"),
        "port": port_override if port_override else app.config.get("SMTP_PORT"),
        "user": app.config.get("SMTP_USER"),
        "password": app.config.get("SMTP_PASSWORD"),
        "use_tls": app.config.get("SMTP_USE_TLS"),
        "from_name": app.config.get("MAIL_FROM_NAME") or app.config.get("SMTP_FROM_NAME"),
        "from_address": app.config.get("MAIL_FROM_ADDRESS"),
    }
    click.echo("Loaded SMTP configuration:")
    click.echo(f"  host          = {cfg['host']!r}")
    click.echo(f"  port          = {cfg['port']!r}"
               f"{' (overridden by --port)' if port_override else ''}")
    click.echo(f"  use_tls       = {cfg['use_tls']!r}")
    click.echo(f"  user (login)  = {cfg['user']!r}")
    click.echo(f"  from_name     = {cfg['from_name']!r}")
    click.echo(f"  from_address  = {cfg['from_address']!r}"
               f"{' (will fall back to user)' if not cfg['from_address'] else ''}")
    click.echo(f"  password      = {'<set, ' + str(len(cfg['password'] or '')) + ' chars>' if cfg['password'] else '<EMPTY>'}")
    click.echo("")

    host = cfg["host"]
    primary_port = int(cfg["port"]) if cfg["port"] else 587

    click.echo(f"DNS lookup for {host}...")
    try:
        addrs = _socket.getaddrinfo(host, primary_port, type=_socket.SOCK_STREAM)
        unique_ips = sorted({a[4][0] for a in addrs})
        click.echo(f"  resolves to: {', '.join(unique_ips)}")
    except _socket.gaierror as exc:
        click.echo(f"  DNS FAILED: {exc}", err=True)
        click.echo("  Check that SMTP_HOST is correct and your server has DNS.", err=True)
        raise SystemExit(1)
    click.echo("")

    candidate_ports = [primary_port]
    if port_override is None:
        for p in (465, 2525, 587):
            if p not in candidate_ports:
                candidate_ports.append(p)

    click.echo("TCP reachability probe (timeout 8s each):")
    reachable = []
    for p in candidate_ports:
        try:
            with _socket.create_connection((host, p), timeout=8):
                click.echo(f"  port {p:>4}  REACHABLE")
                reachable.append(p)
        except (_socket.timeout, OSError) as exc:
            click.echo(f"  port {p:>4}  unreachable ({exc.__class__.__name__}: {exc})")
    click.echo("")

    if not reachable:
        click.echo(
            "All probed SMTP ports are blocked from this server.\n"
            "This is almost certainly your hosting provider blocking outbound\n"
            "SMTP — common on AWS, GCP, Oracle Cloud, DigitalOcean, etc.\n"
            "\n"
            "Recommended: switch to the Brevo HTTPS API (port 443, not blocked).\n"
            "  1. In your .env, set:\n"
            "       MAIL_PROVIDER=brevo_api\n"
            "       BREVO_API_KEY=<your Brevo v3 API key>\n"
            "       MAIL_FROM_ADDRESS=<verified-sender@yourdomain>\n"
            "  2. sudo systemctl restart prompt-a-thon\n"
            "  3. flask test-mail you@example.com\n"
            "Or re-run setup.py and pick 'Brevo HTTPS API' at the prompt.",
            err=True,
        )
        raise SystemExit(1)

    if primary_port not in reachable:
        click.echo(
            f"Configured port {primary_port} is BLOCKED, but {reachable[0]} is open.\n"
            f"Re-run with --port {reachable[0]} to confirm, then update .env:\n"
            f"  SMTP_PORT={reachable[0]}\n"
            f"  SMTP_USE_TLS={'true' if reachable[0] != 465 else 'false'}\n"
            f"and: sudo systemctl restart prompt-a-thon",
            err=True,
        )

    if probe_only:
        click.echo("Probe-only mode; skipping actual send.")
        return

    click.echo(f"Attempting SMTP send to {recipient} via port {primary_port}...")
    ok_, err_ = send_otp_email(
        cfg, recipient, "123456",
        app.config.get("APP_NAME", "prompt-a-thon"),
        int(app.config.get("OTP_EXPIRY_MINUTES") or 10),
    )
    if ok_:
        click.echo("SUCCESS: test email sent.")
    else:
        click.echo(f"FAILED: {err_}", err=True)
        raise SystemExit(1)

def _run_brevo_diagnostics(app, recipient, probe_only,
                           send_otp_email, brevo_account_check):
    cfg = {
        "provider": "brevo_api",
        "brevo_api_key": app.config.get("BREVO_API_KEY"),
        "from_name": app.config.get("MAIL_FROM_NAME") or app.config.get("SMTP_FROM_NAME"),
        "from_address": app.config.get("MAIL_FROM_ADDRESS"),
    }
    click.echo("Loaded Brevo API configuration:")
    api_key = cfg["brevo_api_key"] or ""
    click.echo(f"  api_key       = {'<set, ' + str(len(api_key)) + ' chars>' if api_key else '<EMPTY>'}")
    click.echo(f"  from_address  = {cfg['from_address']!r}"
               f"{'  <-- REQUIRED for Brevo API' if not cfg['from_address'] else ''}")
    click.echo(f"  from_name     = {cfg['from_name']!r}")
    click.echo("")

    if not api_key:
        click.echo("FAILED: BREVO_API_KEY is empty in .env.", err=True)
        click.echo(
            "Get a v3 API key from: https://app.brevo.com/settings/keys/api\n"
            "Then add to /opt/prompt-a-thon/.env:\n"
            "  BREVO_API_KEY=xkeysib-...your-key...",
            err=True,
        )
        raise SystemExit(1)

    if not cfg["from_address"]:
        click.echo("FAILED: MAIL_FROM_ADDRESS is required for Brevo API.", err=True)
        click.echo(
            "Brevo only allows sending from senders/domains you have verified.\n"
            "  1. Add and verify a sender at: https://app.brevo.com/senders/list\n"
            "  2. Add to /opt/prompt-a-thon/.env:\n"
            "       MAIL_FROM_ADDRESS=verified@yourdomain.com",
            err=True,
        )
        raise SystemExit(1)

    click.echo("Validating API key against https://api.brevo.com/v3/account ...")
    ok_, msg_ = brevo_account_check(api_key)
    if ok_:
        click.echo(f"  OK: {msg_}")
    else:
        click.echo(f"  FAILED: {msg_}", err=True)
        click.echo(
            "Common causes:\n"
            "  HTTP 401 - the API key is wrong or has been revoked.\n"
            "  HTTP 403 - the account is suspended; check Brevo dashboard.\n"
            "  network error - your server cannot reach api.brevo.com:443.\n"
            "                  (Caddy etc. would not be affected; this is\n"
            "                  outbound HTTPS — try `curl -v https://api.brevo.com/`)",
            err=True,
        )
        raise SystemExit(1)
    click.echo("")

    if probe_only:
        click.echo("Probe-only mode; skipping actual send.")
        return

    click.echo(f"Sending test email to {recipient} via Brevo API...")
    ok_, err_ = send_otp_email(
        cfg, recipient, "123456",
        app.config.get("APP_NAME", "prompt-a-thon"),
        int(app.config.get("OTP_EXPIRY_MINUTES") or 10),
    )
    if ok_:
        click.echo("SUCCESS: test email queued by Brevo.")
        click.echo("Check the recipient inbox. If it's missing:")
        click.echo("  - look in spam/junk;")
        click.echo("  - check Brevo > Transactional > Logs for the message;")
        click.echo("  - confirm the sender domain is verified.")
    else:
        click.echo(f"FAILED: {err_}", err=True)
        click.echo(
            "Common Brevo API errors:\n"
            "  HTTP 400 'invalid_parameter' / 'sender' - the from address is\n"
            "    not a verified sender on this Brevo account.\n"
            "  HTTP 401 'unauthorized' - bad API key.\n"
            "  HTTP 402 'plan_limit_exceeded' - free tier quota reached.\n"
            "  HTTP 403 'account_suspended' - check Brevo dashboard.",
            err=True,
        )
        raise SystemExit(1)

def _run_ses_diagnostics(app, recipient, probe_only,
                         send_otp_email, ses_account_check):
    cfg = {
        "provider": "ses_api",
        "ses_access_key": app.config.get("SES_ACCESS_KEY_ID"),
        "ses_secret_key": app.config.get("SES_SECRET_ACCESS_KEY"),
        "ses_region": app.config.get("SES_REGION") or "us-east-1",
        "from_name": app.config.get("MAIL_FROM_NAME") or app.config.get("SMTP_FROM_NAME"),
        "from_address": app.config.get("MAIL_FROM_ADDRESS"),
    }
    click.echo("Loaded Amazon SES configuration:")
    ak = cfg["ses_access_key"] or ""
    sk = cfg["ses_secret_key"] or ""
    click.echo(f"  region        = {cfg['ses_region']!r}")
    click.echo(f"  access_key_id = {'<set, ' + str(len(ak)) + ' chars>' if ak else '<EMPTY>'}")
    click.echo(f"  secret_key    = {'<set, ' + str(len(sk)) + ' chars>' if sk else '<EMPTY>'}")
    click.echo(f"  from_address  = {cfg['from_address']!r}"
               f"{'  <-- REQUIRED for SES' if not cfg['from_address'] else ''}")
    click.echo(f"  from_name     = {cfg['from_name']!r}")
    click.echo("")

    if not ak or not sk:
        click.echo("FAILED: SES_ACCESS_KEY_ID and SES_SECRET_ACCESS_KEY must be set in .env.",
                   err=True)
        click.echo(
            "Create them in AWS Console -> IAM -> Users -> your-user -> Security credentials.\n"
            "The IAM user needs the policy 'AmazonSESFullAccess' or a scoped policy\n"
            "permitting ses:SendEmail and ses:GetSendQuota.",
            err=True,
        )
        raise SystemExit(1)

    if not cfg["from_address"]:
        click.echo("FAILED: MAIL_FROM_ADDRESS is required for SES.", err=True)
        click.echo(
            "SES only allows sending from senders/domains you have verified.\n"
            "  1. Verify a sender in AWS Console -> SES -> Verified identities.\n"
            "  2. Add to /opt/prompt-a-thon/.env:\n"
            "       MAIL_FROM_ADDRESS=verified@yourdomain.com",
            err=True,
        )
        raise SystemExit(1)

    region = cfg["ses_region"]
    click.echo(f"Validating credentials against email.{region}.amazonaws.com ...")
    ok_, msg_ = ses_account_check(cfg)
    if ok_:
        click.echo(f"  OK: {msg_}")
    else:
        click.echo(f"  FAILED: {msg_}", err=True)
        click.echo(
            "Common causes:\n"
            "  HTTP 403 'InvalidClientTokenId' - access key is wrong or disabled.\n"
            "  HTTP 403 'SignatureDoesNotMatch' - secret key is wrong (or has whitespace).\n"
            "  HTTP 403 'AccessDenied' - IAM user lacks ses:GetSendQuota permission.\n"
            "  Network error - server cannot reach email.<region>.amazonaws.com:443.",
            err=True,
        )
        raise SystemExit(1)
    click.echo("")

    if probe_only:
        click.echo("Probe-only mode; skipping actual send.")
        return

    click.echo(f"Sending test email to {recipient} via SES ({region})...")
    ok_, err_ = send_otp_email(
        cfg, recipient, "123456",
        app.config.get("APP_NAME", "prompt-a-thon"),
        int(app.config.get("OTP_EXPIRY_MINUTES") or 10),
    )
    if ok_:
        click.echo("SUCCESS: test email accepted by SES.")
        click.echo("If the message does not arrive: check SES sandbox status (new SES")
        click.echo("accounts can only send to verified addresses until you request")
        click.echo("production access in the SES console).")
    else:
        click.echo(f"FAILED: {err_}", err=True)
        click.echo(
            "Common SES errors:\n"
            "  MessageRejected 'Email address is not verified' - new accounts are\n"
            "    in 'sandbox' mode and can only send TO verified addresses. Either\n"
            "    verify the recipient or request production access in the SES console.\n"
            "  MessageRejected 'sending paused' - account on hold; check SES dashboard.",
            err=True,
        )
        raise SystemExit(1)

def _get_page_content():

    try:
        from routes.admin import _get_content
        return _get_content()
    except Exception:
        return {}

def _get_faqs_content():

    try:
        from routes.admin import _get_faqs
        return _get_faqs()
    except Exception:
        return []

def create_app() -> Flask:
    cfg_class = get_config()

    if cfg_class.__name__ == "Production":
        cfg_class.validate()

    app = Flask(
        __name__,
        instance_path=str(Path(__file__).resolve().parent / "instance"),
        instance_relative_config=False,
    )
    app.config.from_object(cfg_class)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=app.config.get("PROXY_FIX_X_FOR", 1),
        x_proto=app.config.get("PROXY_FIX_X_PROTO", 1),
        x_host=app.config.get("PROXY_FIX_X_HOST", 1),
    )

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    try:
        from sqlalchemy import event as _sa_event
        from sqlalchemy.engine import Engine as _Engine
        import sqlite3 as _sqlite3

        @_sa_event.listens_for(_Engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):
            if not isinstance(dbapi_conn, _sqlite3.Connection):
                return
            cur = dbapi_conn.cursor()
            for stmt in (
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA cache_size=-65536",
                "PRAGMA busy_timeout=60000",
                "PRAGMA foreign_keys=ON",
            ):
                cur.execute(stmt)
            cur.close()
    except Exception:
        pass

    init_security(app)

    @app.get("/health")
    @limiter.exempt
    def health():
        try:
            db.session.execute(text("SELECT 1"))
            return {"ok": True}, 200
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
            return {"ok": False}, 503

    import threading as _threading
    _unverified_cleanup_lock = _threading.Lock()
    _unverified_last_run = [None]

    @app.before_request
    def _purge_unverified_on_request():
        from flask import request
        if request.path == "/health":
            return None
        now = _naive_utc()
        with _unverified_cleanup_lock:
            last = _unverified_last_run[0]
            if last and (now - last).total_seconds() < 60:
                return None
            _unverified_last_run[0] = now
        try:
            purge_unverified_participants()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

    @app.before_request
    def _make_session_permanent():
        from flask import request, session
        if request.path == "/health":
            return None
        session.permanent = True

    @app.before_request
    def _lockdown_gate():
        from flask import request, render_template
        from flask_login import current_user
        from models import SiteSettings

        if not SiteSettings.lockdown_enabled():
            return None

        path = request.path or ""
        if path == "/health":
            return None
        allow_prefixes = ("/static/", "/auth/", "/admin")
        if any(path.startswith(p) for p in allow_prefixes):
            return None

        try:
            if current_user.is_authenticated and current_user.is_admin:
                return None
        except Exception:
            pass

        message = SiteSettings.lockdown_message()
        return render_template("lockdown.html", message=message), 503

    def _get_admin_notif():

        result = {"submissions": 0, "data_requests": 0, "updates": 0}
        try:
            from flask_login import current_user as _cu
            if not _cu.is_authenticated:
                return result
            if _cu.is_admin or _cu.is_judge:
                from models import Submission
                result["submissions"] = (
                    db.session.query(func.count(Submission.id))
                    .filter(Submission.status == "submitted")
                    .scalar() or 0
                )
            if _cu.is_admin:
                from models_gdpr import DataRequest
                result["data_requests"] = (
                    db.session.query(func.count(DataRequest.id))
                    .filter(DataRequest.status == "pending")
                    .scalar() or 0
                )

                try:
                    if not app.config.get("MANAGED_MODE"):
                        from services.update_checker import get_update_status
                        if get_update_status().get("installed_status") in ("ok", "critically_outdated"):
                            result["updates"] = 1
                except Exception:
                    pass
        except Exception:
            pass
        return result

    @app.context_processor
    def _inject_brand():
        from models import SiteSettings
        import hashlib as _hl, os as _os
        try:
            ann_on = SiteSettings.announcement_enabled()
            ann_text = SiteSettings.announcement_text()
        except Exception:
            ann_on, ann_text = False, ""

        try:
            _js = _os.path.join(app.static_folder, "js", "main.js")
            _css = _os.path.join(app.static_folder, "css", "main.css")
            _seed = str(int(_os.path.getmtime(_js))) + str(int(_os.path.getmtime(_css)))
            _ver = _hl.md5(_seed.encode()).hexdigest()[:8]
        except Exception:
            _ver = "1"

        try:
            from routes.admin import _build_theme_css, _LEGACY_ACCENT_HEX
            import re as _re
            _mode = SiteSettings.get("theme_mode", "light")
            _accent = SiteSettings.get("theme_accent", "blue")
            _accent = _LEGACY_ACCENT_HEX.get(_accent, _accent)
            if not _re.match(r'^#[0-9a-fA-F]{6}$', _accent):
                _accent = "#2563eb"
            _anim = SiteSettings.get("theme_animation", "rise")
            _theme_css = _build_theme_css(_mode, _accent, _anim)
        except Exception:
            _mode = "light"
            _theme_css = ""
        return {
            "APP_NAME": app.config.get("APP_NAME"),
            "TAGLINE": app.config.get("TAGLINE"),
            "COLLEGE_NAME": app.config.get("COLLEGE_NAME"),
            "COLLEGE_URL": app.config.get("COLLEGE_URL"),
            "CONTACT_EMAIL": app.config.get("CONTACT_EMAIL"),
            "SITE_DOMAIN": _site_domain(app),
            "MS_SSO_ENABLED": bool(app.config.get("MICROSOFT_CLIENT_ID")),
            "ANNOUNCEMENT_ENABLED": ann_on,
            "ANNOUNCEMENT_TEXT": ann_text,
            "ASSET_VER": _ver,
            "THEME_CSS": _theme_css,
            "THEME_MODE": _mode,
            "ADMIN_NOTIF": _get_admin_notif(),
            "MANAGED_MODE": bool(app.config.get("MANAGED_MODE")),
            "ACCEPTED_FILES": SiteSettings.get("accepted_files", "pdf") or "pdf",
            "PAGE_CONTENT": _get_page_content(),
            "FAQS": _get_faqs_content(),
        }

    from datetime import date as _date, datetime as _datetime, timezone

    import mistune as _mistune
    _md_renderer = _mistune.create_markdown(
        plugins=["table", "strikethrough"],
    )

    @app.template_filter("markdown")
    def _md_filter(text):
        if not text:
            return ""
        import bleach as _bleach
        allowed_tags = list(_bleach.sanitizer.ALLOWED_TAGS) + [
            "p", "pre", "code", "h1", "h2", "h3", "h4", "h5", "h6",
            "table", "thead", "tbody", "tr", "th", "td",
            "ul", "ol", "li", "blockquote", "hr", "br",
            "strong", "em", "del", "s",
        ]
        allowed_attrs = {"*": ["class"], "a": ["href", "title", "rel"], "td": ["align"], "th": ["align"]}
        from markupsafe import Markup
        raw_html = _md_renderer(text)
        cleaned = _bleach.clean(raw_html, tags=allowed_tags, attributes=allowed_attrs)
        return Markup(cleaned)

    def _long_date(value):

        if not value:
            return ""
        d = None
        if isinstance(value, _datetime):
            d = value.date()
        elif isinstance(value, _date):
            d = value
        elif isinstance(value, str):
            try:

                s = value.rstrip("Z") if value.endswith("Z") else value
                if "T" in s or " " in s and len(s) > 10:
                    d = _datetime.fromisoformat(s).date()
                else:
                    d = _date.fromisoformat(s)
            except (ValueError, AttributeError):
                return value
        if d is None:
            return ""

        return f"{d.day} {d.strftime('%B')} {d.year}"

    app.jinja_env.filters["long_date"] = _long_date

    def _md_to_html(text):

        from markupsafe import Markup, escape
        import re as _re
        if not text:
            return Markup("")
        out = escape(str(text))

        url_re = _re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
        def _link(m):
            label = m.group(1)
            url = m.group(2)
            if url.startswith(("http://", "https://", "mailto:")):
                return f'<a href="{escape(url)}" target="_blank" rel="noopener noreferrer">{label}</a>'
            return escape(m.group(0))
        out = url_re.sub(_link, str(out))

        out = _re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
        out = _re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
        out = _re.sub(r"`([^`]+)`", r"<code>\1</code>", out)

        paragraphs = _re.split(r"\n\s*\n", out.strip())
        rendered = "".join(
            "<p>" + p.replace("\n", "<br>") + "</p>" for p in paragraphs if p.strip()
        )
        return Markup(rendered)

    app.jinja_env.filters["md_to_html"] = _md_to_html

    from routes import admin as admin_routes
    from routes import auth as auth_routes
    from routes import gdpr as gdpr_routes
    from routes import participant as participant_routes
    from routes import public as public_routes

    app.register_blueprint(public_routes.bp)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(participant_routes.bp)
    app.register_blueprint(admin_routes.bp)
    app.register_blueprint(gdpr_routes.bp)

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            app.static_folder, "favicon.ico",
            mimetype="image/x-icon", max_age=86400,
        )

    import models
    import models_gdpr

    _register_error_handlers(app)
    _register_cli(app)
    _setup_logging(app)

    return app

if __name__ == "__main__":
    application = create_app()
    application.run(host="127.0.0.1", port=5000, debug=application.config.get("DEBUG", False))
