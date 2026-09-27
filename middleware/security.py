import html
import secrets
import threading
import time

from flask import Flask, g

_LOG_INTERVAL = 30
_LOG_SEEN_MAX = 4096
_log_seen: dict[str, float] = {}
_log_lock = threading.Lock()
_last_event_purge = 0.0

def _trust_cloudflare() -> bool:
    try:
        from flask import current_app
        return bool(current_app.config.get("TRUST_CLOUDFLARE"))
    except Exception:
        return False

def client_ip(request) -> str:
    if _trust_cloudflare():
        cf = request.headers.get("CF-Connecting-IP", "").strip()
        if cf:
            return cf[:45]
    return (request.remote_addr or "")[:45]

def _should_log(ip: str) -> bool:
    now = time.monotonic()
    with _log_lock:
        if len(_log_seen) > _LOG_SEEN_MAX:
            cutoff = now - _LOG_INTERVAL
            for key, seen in list(_log_seen.items()):
                if seen < cutoff:
                    del _log_seen[key]
            if len(_log_seen) > _LOG_SEEN_MAX:
                overflow = len(_log_seen) - _LOG_SEEN_MAX
                for key in sorted(_log_seen, key=_log_seen.get)[:overflow]:
                    del _log_seen[key]
        if now - _log_seen.get(ip, 0) >= _LOG_INTERVAL:
            _log_seen[ip] = now
            return True
    return False

def _build_csp(nonce: str, allow_cdn: bool = False) -> str:

    cdn_script = " https://cdnjs.cloudflare.com" if allow_cdn else ""
    cdn_connect = " https://cdn.jsdelivr.net https://cdnjs.cloudflare.com" if allow_cdn else ""
    return (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' https://www.youtube.com https://www.youtube-nocookie.com{cdn_script}; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "frame-src 'self' data: https://www.youtube.com https://www.youtube-nocookie.com; "
        "font-src 'self'; "
        f"connect-src 'self'{cdn_connect}; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'"
    )

def _get_country(request) -> str | None:
    if not _trust_cloudflare():
        return None
    cc = request.headers.get("CF-IPCountry", "").strip().upper()
    return cc if len(cc) == 2 and cc.isalpha() else None

def init_security(app: Flask) -> None:

    @app.before_request
    def _generate_csp_nonce():
        g.csp_nonce = secrets.token_hex(16)

    @app.before_request
    def _check_blocks_and_log():
        from flask import request, abort, current_app
        from models import SiteSettings, ConnectionEvent, db
        import json

        ip = client_ip(request)
        country = _get_country(request)
        is_blocked = False

        if request.endpoint in ("static", "admin.security_live_log"):
            return

        try:
            raw_ips = SiteSettings.get("blocked_ips")
            if raw_ips:
                blocked_ips = json.loads(raw_ips)
                if ip in blocked_ips:
                    is_blocked = True
        except Exception:
            pass

        _COUNTRY_EXEMPT = {"/email-img-logo.png", "/sitemap.xml", "/robots.txt"}
        if not is_blocked and country and request.path not in _COUNTRY_EXEMPT:
            try:
                restrict_mode = SiteSettings.get("country_restrict_mode", "off")
                raw_cc = SiteSettings.get("country_restrict_list")
                if restrict_mode != "off" and raw_cc:
                    cc_list = json.loads(raw_cc)
                    if cc_list:
                        if restrict_mode == "blocklist" and country in cc_list:
                            is_blocked = True
                        elif restrict_mode == "allowlist" and country not in cc_list:
                            is_blocked = True
            except Exception:
                pass

        if is_blocked:
            try:
                evt = ConnectionEvent(
                    ip=ip, country_code=country,
                    path=request.path[:500], method=request.method,
                    is_blocked=True,
                )
                db.session.add(evt)
                db.session.commit()
            except Exception:
                pass

            try:
                custom_msg = SiteSettings.get("country_block_message") or ""
            except Exception:
                custom_msg = ""
            msg = custom_msg if custom_msg else (
                "Access to this site is not available in your region."
            )
            from flask import make_response
            safe_msg = html.escape(msg, quote=True)
            return make_response(
                f"<!doctype html><html><head><meta charset=utf-8>"
                f"<title>Access restricted</title>"
                f"<style>body{{font-family:system-ui,sans-serif;text-align:center;"
                f"padding:4rem 2rem;color:#374151;}}h1{{font-size:1.5rem;margin-bottom:1rem;}}"
                f"p{{color:#6b7280;}}</style></head><body>"
                f"<h1>Access Restricted</h1><p>{safe_msg}</p></body></html>",
                403
            )

        if request.path == "/health":
            return

        if _should_log(ip):
            try:
                from flask_login import current_user
                uid = current_user.id if current_user.is_authenticated else None
            except Exception:
                uid = None
            try:
                evt = ConnectionEvent(
                    ip=ip, country_code=country,
                    path=request.path[:500], method=request.method,
                    user_id=uid, is_blocked=False,
                )
                db.session.add(evt)
                db.session.commit()
            except Exception as _err:
                db.session.rollback()
                import logging as _lg
                _lg.getLogger("security").warning("connection_log failed: %s", _err)
                try:
                    ConnectionEvent.__table__.create(db.engine, checkfirst=True)
                except Exception:
                    pass

        global _last_event_purge
        now_mono = time.monotonic()
        with _log_lock:
            due = now_mono - _last_event_purge > 600
            if due:
                _last_event_purge = now_mono
        if due:
            try:
                from datetime import timedelta
                from models import utcnow
                cutoff = utcnow() - timedelta(hours=48)
                ConnectionEvent.query.filter(
                    ConnectionEvent.created_at < cutoff
                ).delete()
                db.session.commit()
            except Exception:
                pass

    @app.context_processor
    def _inject_csp_nonce():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.after_request
    def _set_security_headers(response):
        from flask import request as _req
        if _req.endpoint in ("admin.view_file", "admin.view_extra_file"):
            return response
        nonce = getattr(g, "csp_nonce", "")
        allow_cdn = _req.endpoint == "admin.security"
        response.headers["Content-Security-Policy"] = _build_csp(nonce, allow_cdn=allow_cdn)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response
