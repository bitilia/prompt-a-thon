from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
import secrets
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from typing import Tuple

def _generate_pixel_id() -> str:
    return secrets.token_urlsafe(32)

def _pixel_url(base_url: str, pixel_id: str) -> str:
    base = (base_url or "").rstrip("/")
    return f"{base}/email-img-logo.png?pixel={pixel_id}"

def _inject_pixel(html: str, pixel_url: str) -> str:

    img = (
        f'<img src="{pixel_url}" width="1" height="1" alt="" '
        'aria-hidden="true" style="display:block;width:1px;height:1px;'
        'overflow:hidden;opacity:0.01;" loading="eager">'
    )
    if "</body>" in html:
        return html.replace("</body>", img + "\n</body>", 1)
    return html + img

_LOGO_PNG_BYTES: bytes | None = None

def _get_logo_png() -> bytes:

    global _LOGO_PNG_BYTES
    if _LOGO_PNG_BYTES is not None:
        return _LOGO_PNG_BYTES
    import os as _os
    try:
        path = _os.path.join(_os.path.dirname(__file__), '..', 'static', 'email-img-logo.png')
        with open(path, 'rb') as _f:
            _LOGO_PNG_BYTES = _f.read()
    except Exception:
        _LOGO_PNG_BYTES = b''
    return _LOGO_PNG_BYTES

_CID_LOGO_IMG = (
    '<img src="cid:promptathon-logo" width="120" height="30" alt="prompt-a-thon" '
    'style="opacity:0.5;vertical-align:middle;">'
)

def _try_log_email(to_email: str, subject: str, category: str,
                   body_html: str | None = None,
                   tracking_pixel_id: str | None = None) -> None:
    try:
        from flask import current_app
        _ = current_app._get_current_object()
        from extensions import db
        from models import EmailLog
        entry = EmailLog(
            to_email=to_email,
            subject=subject,
            category=category,
            body_html=body_html if category != "otp" else None,
            tracking_pixel_id=tracking_pixel_id,
        )
        db.session.add(entry)
        db.session.commit()
    except RuntimeError:
        pass
    except Exception:
        try:
            from extensions import db as _db
            _db.session.rollback()
        except Exception:
            pass

def _footer_html(app_name: str) -> str:
    return (
        f'<div style="padding:16px 28px;background:#f8fafc;border-top:1px solid #e2e8f0;">'
        f'<table cellpadding="0" cellspacing="0" style="width:100%;border:0;">'
        f'<tr>'
        f'<td style="color:#94a3b8;font-size:12px;">Sent by {app_name}. Do not reply to this message.</td>'
        f'<td style="text-align:right;padding:0;">{_CID_LOGO_IMG}</td>'
        f'</tr>'
        f'</table>'
        f'</div>'
    )

def _branded_html_email(app_name: str, inner_html: str,
                        accent: str = "#f27d00", base_url: str = "") -> str:

    footer = _footer_html(app_name)
    return (
        f'<!DOCTYPE html><html lang="en">'
        f'<head><meta charset="utf-8"></head>'
        f'<body style="margin:0;padding:24px;background:#f1f5f9;'
        f"font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0f172a;\">"
        f'<div style="max-width:560px;margin:0 auto;background:#ffffff;'
        f'border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">'
        f'<div style="padding:20px 28px;border-bottom:1px solid #e2e8f0;">'
        f'<strong style="font-size:16px;color:{accent};">{app_name}</strong>'
        f'</div>'
        f'<div style="padding:32px 28px;color:#374151;font-size:15px;line-height:1.7;">'
        f'{inner_html}'
        f'</div>'
        f'{footer}'
        f'</div>'
        f'</body></html>'
    )

def _build_otp_html(app_name: str, code: str, expiry_minutes: int,
                    accent: str = "#f27d00", base_url: str = "") -> str:
    footer = _footer_html(app_name)
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Sign-in code</title></head>
<body style="margin:0;padding:24px;background:#f1f5f9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0f172a;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
    <div style="padding:20px 28px;border-bottom:1px solid #e2e8f0;">
      <strong style="font-size:16px;color:{accent};">{app_name}</strong>
    </div>
    <div style="padding:32px 28px;">
      <h1 style="margin:0 0 12px;font-size:20px;color:#0f172a;">Your sign-in code</h1>
      <p style="margin:0 0 24px;color:#475569;">Enter the code below on the verification page to complete your sign-in.</p>
      <div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:36px;letter-spacing:8px;text-align:center;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:20px;color:#0f172a;font-weight:700;">{code}</div>
      <p style="margin:24px 0 0;color:#64748b;font-size:14px;">This code expires in {expiry_minutes} minutes. If you did not request this, you can safely ignore this email.</p>
    </div>
    {footer}
  </div>
</body>
</html>"""

def _build_otp_text(app_name: str, code: str, expiry_minutes: int) -> str:
    return (
        f"{app_name} sign-in code\n\n"
        f"Your code: {code}\n\n"
        f"This code expires in {expiry_minutes} minutes.\n"
        "If you did not request this, you can safely ignore this email.\n"
    )

def _build_submission_success_html(app_name: str, submission_title: str,
                                    file_names: list | None = None,
                                    accent: str = "#f27d00") -> str:
    files_html = ""
    if file_names:
        items = "".join(
            f'<li style="margin:4px 0;font-size:14px;color:#166534;">{fn}</li>'
            for fn in file_names
        )
        files_html = (
            f'<ul style="margin:8px 0 0;padding-left:18px;">{items}</ul>'
        )
    footer = _footer_html(app_name)
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Submission received</title></head>
<body style="margin:0;padding:24px;background:#f1f5f9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0f172a;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
    <div style="padding:20px 28px;border-bottom:1px solid #e2e8f0;">
      <strong style="font-size:16px;color:{accent};">{app_name}</strong>
    </div>
    <div style="padding:32px 28px;">
      <h1 style="margin:0 0 12px;font-size:20px;color:#0f172a;">Submission received</h1>
      <p style="margin:0 0 20px;color:#475569;">Your submission has been successfully received and is now in our system.</p>
      <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:16px 20px;color:#166534;font-size:15px;">
        <strong>{submission_title}</strong>{files_html}
      </div>
      <p style="margin:20px 0 0;color:#64748b;font-size:14px;">No further action is needed. We will be in touch with next steps.</p>
    </div>
    {footer}
  </div>
</body>
</html>"""

def _build_submission_success_text(app_name: str, submission_title: str,
                                    file_names: list | None = None) -> str:
    files_text = ""
    if file_names:
        files_text = "\nFiles:\n" + "".join(f"  · {fn}\n" for fn in file_names)
    return (
        f"{app_name} — Submission received\n\n"
        f"Your submission has been successfully received:\n\n"
        f"  {submission_title}\n"
        f"{files_text}\n"
        "No further action is needed. We will be in touch with next steps.\n"
    )

def _build_submission_failed_html(app_name: str, submission_title: str, error_msg: str,
                                   accent: str = "#f27d00") -> str:
    footer = _footer_html(app_name)
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Submission could not be processed</title></head>
<body style="margin:0;padding:24px;background:#f1f5f9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0f172a;">
  <div style="max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
    <div style="padding:20px 28px;border-bottom:1px solid #e2e8f0;">
      <strong style="font-size:16px;color:{accent};">{app_name}</strong>
    </div>
    <div style="padding:32px 28px;">
      <h1 style="margin:0 0 12px;font-size:20px;color:#0f172a;">Submission could not be processed</h1>
      <p style="margin:0 0 20px;color:#475569;">We were unable to process the file for your submission:</p>
      <div style="background:#fef2f2;border:1px solid #fecaca;border-radius:10px;padding:16px 20px;color:#991b1b;font-size:15px;margin-bottom:20px;">
        <strong>{submission_title}</strong>
      </div>
      <p style="margin:0 0 8px;color:#475569;font-size:14px;"><strong>Reason:</strong> {error_msg}</p>
      <p style="margin:16px 0 0;color:#475569;">Please log in and resubmit using a different file. If the problem persists, contact the event organisers.</p>
    </div>
    {footer}
  </div>
</body>
</html>"""

def _build_submission_failed_text(app_name: str, submission_title: str, error_msg: str) -> str:
    return (
        f"{app_name} — Submission could not be processed\n\n"
        f"We were unable to process the file for your submission:\n\n"
        f"  {submission_title}\n\n"
        f"Reason: {error_msg}\n\n"
        "Please log in and resubmit using a different file.\n"
        "If the problem persists, contact the event organisers.\n"
    )

_AWARD_LABELS = {
    "first":  ("1st Place", "#16a34a", "#f0fdf4", "#bbf7d0", "#166534"),
    "second": ("2nd Place", "#d97706", "#fffbeb", "#fde68a", "#92400e"),
    "third":  ("3rd Place", "#9333ea", "#faf5ff", "#e9d5ff", "#6b21a8"),
}

def _build_certificate_html(app_name: str, recipient_name: str,
                              competition_name: str, school_name: str,
                              award_type: str, accent: str = "#f27d00") -> str:
    label, color, bg, border, text_color = _AWARD_LABELS.get(
        award_type, ("Participation", accent, "#f8fafc", "#e2e8f0", "#374151")
    )
    footer = _footer_html(app_name)
    name_display = recipient_name or "Participant"
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Congratulations!</title></head>
<body style="margin:0;padding:24px;background:#f1f5f9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0f172a;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
    <div style="padding:20px 28px;background:{accent};border-bottom:1px solid {accent};">
      <strong style="font-size:16px;color:#ffffff;">{app_name}</strong>
    </div>
    <div style="padding:32px 28px;">
      <h1 style="margin:0 0 8px;font-size:22px;color:#0f172a;">Congratulations, {name_display}!</h1>
      <p style="margin:0 0 24px;color:#475569;font-size:15px;">We are delighted to inform you of your outstanding achievement.</p>

      <div style="background:{bg};border:2px solid {border};border-radius:12px;padding:20px 24px;margin-bottom:24px;">
        <div style="font-size:28px;font-weight:800;color:{text_color};letter-spacing:-0.5px;">{label}</div>
        <div style="font-size:15px;color:{color};margin-top:4px;font-weight:600;">{competition_name}</div>
        <div style="font-size:13px;color:#64748b;margin-top:4px;">{school_name}</div>
      </div>

      <p style="margin:0 0 16px;color:#374151;font-size:15px;">
        Your performance has been exceptional, and this award reflects your hard work and dedication.
      </p>
      <p style="margin:0;color:#64748b;font-size:14px;">
        A certificate is attached to this email. We hope to see you excel again in future competitions.
      </p>
    </div>
    {footer}
  </div>
</body>
</html>"""

def _build_certificate_text(app_name: str, recipient_name: str,
                              competition_name: str, school_name: str,
                              award_type: str) -> str:
    labels = {"first": "1st Place", "second": "2nd Place", "third": "3rd Place"}
    label = labels.get(award_type, "Participant")
    name = recipient_name or "Participant"
    return (
        f"{app_name} — Congratulations, {name}!\n\n"
        f"You have been awarded {label} in {competition_name} ({school_name}).\n\n"
        "Your performance has been exceptional. A certificate is attached to this email.\n"
        "We hope to see you excel again in future competitions.\n"
    )

def _build_sorry_html(app_name: str, recipient_name: str,
                       competition_name: str, school_name: str,
                       message: str, accent: str = "#f27d00") -> str:
    footer = _footer_html(app_name)
    name_display = recipient_name or "Participant"
    msg_html = message.replace("\n", "<br>") if message else (
        "Thank you for participating in this competition. While you were not placed "
        "in the top positions this time, your effort and dedication are truly commendable. "
        "We encourage you to keep challenging yourself and hope to see you again."
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{competition_name} — Results</title></head>
<body style="margin:0;padding:24px;background:#f1f5f9;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#0f172a;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;">
    <div style="padding:20px 28px;background:{accent};border-bottom:1px solid {accent};">
      <strong style="font-size:16px;color:#ffffff;">{app_name}</strong>
    </div>
    <div style="padding:32px 28px;">
      <h1 style="margin:0 0 8px;font-size:22px;color:#0f172a;">Dear {name_display},</h1>
      <p style="margin:0 0 8px;color:#475569;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;">{competition_name} &mdash; {school_name}</p>
      <div style="margin:20px 0;color:#374151;font-size:15px;line-height:1.7;">{msg_html}</div>
      <p style="margin:16px 0 0;color:#64748b;font-size:14px;">
        We wish you all the best in your future endeavours.
      </p>
    </div>
    {footer}
  </div>
</body>
</html>"""

def _build_sorry_text(app_name: str, recipient_name: str,
                       competition_name: str, message: str) -> str:
    name = recipient_name or "Participant"
    body = message or (
        "Thank you for participating. While you were not placed in the top positions "
        "this time, your effort is truly commendable. We hope to see you again."
    )
    return (
        f"{app_name} — {competition_name} Results\n\n"
        f"Dear {name},\n\n"
        f"{body}\n\n"
        "We wish you all the best.\n"
    )

def generate_certificate_pdf(recipient_name: str, recipient_email: str,
                               competition_name: str, school_name: str,
                               award_type: str,
                               accent: str = "#f27d00") -> bytes:

    import base64
    import html as _html_esc
    import os

    _static = os.path.join(os.path.dirname(__file__), "..", "static")

    def _b64file(name: str) -> str:
        try:
            with open(os.path.join(_static, name), "rb") as _f:
                return base64.b64encode(_f.read()).decode("ascii")
        except Exception:
            return ""

    logo_png = _b64file("email-img-logo.png")

    award_labels = {"first": "First Place", "second": "Second Place", "third": "Third Place"}
    award_colors = {"first": "#16a34a", "second": "#d97706", "third": "#9333ea"}

    label      = award_labels.get(award_type, "Participant")
    acolor     = award_colors.get(award_type, accent)
    name_disp  = _html_esc.escape(recipient_name or recipient_email.split("@")[0])
    safe_comp  = _html_esc.escape(competition_name)
    safe_sch   = _html_esc.escape(school_name)
    safe_email = _html_esc.escape(recipient_email)
    safe_label = _html_esc.escape(label)

    logo_footer_tag = (
        f'<img src="data:image/png;base64,{logo_png}" class="footer-logo">'
        if logo_png else ""
    )

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>
@page {{ size: 297mm 210mm; margin: 0; }}
*{{ box-sizing:border-box; margin:0; padding:0; }}
body{{
  width:297mm; height:210mm;
  font-family:Helvetica,Arial,sans-serif;
  background:#f9f7f4;
  overflow:hidden;
}}
.cert{{
  width:297mm; height:210mm;
  display:flex; flex-direction:column;
  border:4pt solid {accent};
}}
.inner{{
  margin:6mm; flex:1;
  display:flex; flex-direction:column;
  border:1pt solid {accent};
  overflow:hidden;
}}
.header{{
  background:{accent};
  padding:7mm 10mm;
  display:flex;
  align-items:center;
  justify-content:space-between;
  flex-shrink:0;
}}
.header-left h1{{
  color:#fff; font-size:22pt; font-weight:900;
  text-transform:uppercase; letter-spacing:-0.3pt; margin:0 0 2mm 0;
}}
.header-left p{{
  color:rgba(255,255,255,0.85); font-size:10pt; margin:0;
}}
.body{{
  flex:1;
  display:flex; flex-direction:column;
  align-items:center; justify-content:center;
  padding:4mm 14mm;
  text-align:center;
}}
.presented{{
  font-size:8.5pt; color:#64748b;
  text-transform:uppercase; letter-spacing:0.18em;
  margin-bottom:5mm;
}}
.name{{
  font-size:38pt; font-weight:900; color:#0f172a;
  letter-spacing:-0.5pt;
  border-bottom:2.5pt solid {accent};
  padding-bottom:3mm; margin-bottom:4mm;
  display:inline-block;
}}
.school{{
  font-size:11pt; color:#64748b; font-style:italic; margin-bottom:5mm;
}}
.award{{
  font-size:16pt; font-weight:800; color:{acolor};
}}
.footer{{
  border-top:1pt solid #e2e8f0;
  padding:3mm 10mm;
  display:flex; align-items:center;
  justify-content:space-between;
  flex-shrink:0;
}}
.footer-email{{
  font-size:7pt; color:#94a3b8;
}}
.footer-logo{{
  height:18pt; width:auto; opacity:0.5;
}}
</style></head>
<body>
<div class="cert"><div class="inner">
  <div class="header">
    <div class="header-left">
      <h1>Certificate of Achievement</h1>
      <p>{safe_comp}</p>
    </div>
  </div>
  <div class="body">
    <p class="presented">This certificate is proudly presented to</p>
    <span class="name">{name_disp}</span>
    <p class="school">from {safe_sch}</p>
    <p class="award">for achieving {safe_label}</p>
  </div>
  <div class="footer">
    <span class="footer-email">{safe_email}</span>
    {logo_footer_tag}
  </div>
</div></div>
</body></html>"""

    from weasyprint import HTML as _WP
    return _WP(string=html).write_pdf()

def _resolve_from(cfg: dict) -> Tuple[str, str]:

    from_name = cfg.get("from_name") or cfg.get("app_name") or "prompt-a-thon"
    from_addr = cfg.get("from_address") or cfg.get("user") or ""
    return from_name, from_addr

def _build_mime_msg(from_str: str, to_email: str, subject: str,
                    text_body: str, html_body: str | None,
                    pdf_data: bytes | None = None,
                    pdf_name: str | None = None,
                    app_name: str = "") -> MIMEMultipart:

    outer = MIMEMultipart("mixed")
    outer["From"] = from_str
    outer["To"] = to_email
    outer["Subject"] = subject
    outer["Message-ID"] = make_msgid()
    outer["Auto-Submitted"] = "auto-generated"
    if app_name:
        outer["X-Mailer"] = app_name

    related = MIMEMultipart("related")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body or "", "plain", "utf-8"))
    if html_body:
        alt.attach(MIMEText(html_body, "html", "utf-8"))
    related.attach(alt)

    logo_png = _get_logo_png()
    if logo_png:
        img_part = MIMEImage(logo_png, _subtype="png")
        img_part.add_header("Content-ID", "<promptathon-logo>")
        img_part.add_header("Content-Disposition", "inline", filename="promptathon-logo.png")
        related.attach(img_part)

    outer.attach(related)

    if pdf_data and pdf_name:
        pdf_part = MIMEApplication(pdf_data, _subtype="pdf")
        pdf_part.add_header("Content-Disposition", "attachment", filename=pdf_name)
        outer.attach(pdf_part)

    return outer

def _smtp_send(cfg: dict, msg) -> Tuple[bool, str | None]:
    host = cfg.get("host") or ""
    port = int(cfg.get("port") or 587)
    user = cfg.get("user") or ""
    password = cfg.get("password") or ""
    _use_tls_raw = cfg.get("use_tls", True)
    if isinstance(_use_tls_raw, str):
        use_tls = _use_tls_raw.strip().lower() not in ("false", "0", "no", "")
    else:
        use_tls = bool(_use_tls_raw)

    localhost = host in ("127.0.0.1", "localhost", "::1")

    if not host:
        return False, "SMTP host is not configured"
    if not localhost and (not user or not password):
        return False, "SMTP credentials are not configured (user/password)"

    context = ssl.create_default_context()
    try:
        if port == 465 and not localhost:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo()
                if use_tls and not localhost:
                    s.starttls(context=context)
                    s.ehlo()
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        return True, None
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        return False, f"SMTP error: {exc.__class__.__name__}: {exc}"

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_ACCOUNT_URL = "https://api.brevo.com/v3/account"

def _brevo_api_send(cfg: dict, to_email: str, subject: str,
                    text_body: str, html_body: str | None,
                    attachment_data: bytes | None = None,
                    attachment_name: str | None = None) -> Tuple[bool, str | None]:
    api_key = cfg.get("brevo_api_key") or ""
    if not api_key:
        return False, "Brevo API key is not configured (BREVO_API_KEY)"

    from_name, from_addr = _resolve_from(cfg)
    if not from_addr:
        return False, ("MAIL_FROM_ADDRESS is required when MAIL_PROVIDER=brevo_api. "
                       "Use a sender you have verified in Brevo.")

    payload: dict = {
        "sender": {"name": from_name, "email": from_addr},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": text_body or "",
    }
    if html_body:
        payload["htmlContent"] = html_body

    logo_png = _get_logo_png()
    attachments = []
    if logo_png:
        attachments.append({
            "content": base64.b64encode(logo_png).decode("ascii"),
            "name": "promptathon-logo.png",
            "contentId": "promptathon-logo",
        })
    if attachment_data and attachment_name:
        attachments.append({
            "content": base64.b64encode(attachment_data).decode("ascii"),
            "name": attachment_name,
        })
    if attachments:
        payload["attachment"] = attachments

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        BREVO_SEND_URL,
        data=data,
        method="POST",
        headers={
            "api-key": api_key,
            "accept": "application/json",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if 200 <= resp.status < 300:
                return True, None
            body = resp.read().decode("utf-8", errors="replace")[:500]
            return False, f"Brevo API HTTP {resp.status}: {body}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
        return False, f"Brevo API error HTTP {exc.code}: {body}"
    except urllib.error.URLError as exc:
        return False, f"Brevo API connection error: {exc.reason}"
    except OSError as exc:
        return False, f"Brevo API network error: {exc.__class__.__name__}: {exc}"

def brevo_account_check(api_key: str) -> Tuple[bool, str]:

    if not api_key:
        return False, "no API key provided"
    req = urllib.request.Request(
        BREVO_ACCOUNT_URL,
        method="GET",
        headers={"api-key": api_key, "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
                email = data.get("email") or "(unknown)"
                first = data.get("firstName") or ""
                last = data.get("lastName") or ""
                name = (first + " " + last).strip() or "(no name)"
                return True, f"key valid; account email={email}, name={name}"
            except json.JSONDecodeError:
                return True, "key valid; (account body not JSON)"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300] if exc.fp else ""
        return False, f"HTTP {exc.code}: {body}"
    except urllib.error.URLError as exc:
        return False, f"network error: {exc.reason}"
    except OSError as exc:
        return False, f"network error: {exc.__class__.__name__}: {exc}"

def _aws_sign_v4(method: str, host: str, region: str, service: str,
                 access_key: str, secret_key: str,
                 payload: bytes,
                 canonical_uri: str = "/",
                 content_type: str = "application/x-www-form-urlencoded; charset=utf-8",
                 ) -> dict:

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-date"
    canonical_query = ""
    canonical_request = (
        f"{method}\n{canonical_uri}\n{canonical_query}\n"
        f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
    )
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = (
        f"{algorithm}\n{amz_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    def _sign(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    auth_header = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Content-Type": content_type,
        "X-Amz-Date": amz_date,
        "Authorization": auth_header,
    }

def _ses_api_send(cfg: dict, to_email: str, subject: str,
                  text_body: str, html_body: str | None) -> Tuple[bool, str | None]:
    access_key = (cfg.get("ses_access_key") or "").strip()
    secret_key = (cfg.get("ses_secret_key") or "").strip()
    region     = (cfg.get("ses_region") or "us-east-1").strip()
    if not access_key or not secret_key:
        return False, "SES credentials not configured (SES_ACCESS_KEY_ID, SES_SECRET_ACCESS_KEY)"

    from_name, from_addr = _resolve_from(cfg)
    if not from_addr:
        return False, ("MAIL_FROM_ADDRESS is required when MAIL_PROVIDER=ses_api. "
                       "Use a sender or domain you have verified in SES.")
    from_str = formataddr((from_name, from_addr)) if from_name else from_addr

    import io as _io
    import email.generator as _eg
    mime_msg = _build_mime_msg(from_str, to_email, subject, text_body or "",
                               html_body, app_name=from_name)
    buf = _io.BytesIO()
    _eg.BytesGenerator(buf).flatten(mime_msg)
    raw_bytes = buf.getvalue()

    params = {
        "Action": "SendRawEmail",
        "Version": "2010-12-01",
        "Destinations.member.1": to_email,
        "RawMessage.Data": base64.b64encode(raw_bytes).decode("ascii"),
    }
    payload = urllib.parse.urlencode(params).encode("utf-8")
    host = f"email.{region}.amazonaws.com"
    url = f"https://{host}/"
    headers = _aws_sign_v4(
        method="POST", host=host, region=region, service="ses",
        access_key=access_key, secret_key=secret_key,
        payload=payload,
    )
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if 200 <= resp.status < 300:
                return True, None
            body = resp.read().decode("utf-8", errors="replace")[:500]
            return False, f"SES HTTP {resp.status}: {body}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500] if exc.fp else ""
        return False, f"SES error HTTP {exc.code}: {body}"
    except urllib.error.URLError as exc:
        return False, f"SES connection error: {exc.reason}"
    except OSError as exc:
        return False, f"SES network error: {exc.__class__.__name__}: {exc}"

def ses_account_check(cfg: dict) -> Tuple[bool, str]:

    access_key = (cfg.get("ses_access_key") or "").strip()
    secret_key = (cfg.get("ses_secret_key") or "").strip()
    region     = (cfg.get("ses_region") or "us-east-1").strip()
    if not access_key or not secret_key:
        return False, "no AWS credentials provided"

    payload = urllib.parse.urlencode({
        "Action": "GetSendQuota",
        "Version": "2010-12-01",
    }).encode("utf-8")
    host = f"email.{region}.amazonaws.com"
    url = f"https://{host}/"
    headers = _aws_sign_v4(
        method="POST", host=host, region=region, service="ses",
        access_key=access_key, secret_key=secret_key,
        payload=payload,
    )
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            import re as _re
            m = _re.search(r"<Max24HourSend>([^<]+)</Max24HourSend>", body)
            quota = m.group(1) if m else "?"
            m2 = _re.search(r"<SentLast24Hours>([^<]+)</SentLast24Hours>", body)
            sent = m2.group(1) if m2 else "?"
            return True, f"credentials valid; sent {sent} of {quota} in last 24h"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300] if exc.fp else ""
        return False, f"HTTP {exc.code}: {body}"
    except urllib.error.URLError as exc:
        return False, f"network error: {exc.reason}"
    except OSError as exc:
        return False, f"network error: {exc.__class__.__name__}: {exc}"

def send_otp_email(cfg: dict, to_email: str, otp_code: str,
                   app_name: str, expiry_minutes: int) -> Tuple[bool, str | None]:

    accent = cfg.get("accent", "#f27d00")
    base_url = cfg.get("base_url", "")
    subject = f"Your {app_name} login code: {otp_code}"
    log_subject = f"Your {app_name} login code"
    text_body = _build_otp_text(app_name, otp_code, expiry_minutes)
    html_body = _build_otp_html(app_name, otp_code, expiry_minutes,
                                accent=accent, base_url=base_url)
    provider = (cfg.get("provider") or "smtp").lower()
    if provider == "postfix":
        provider = "smtp"

    if provider == "brevo_api":
        ok, err = _brevo_api_send(cfg, to_email, subject, text_body, html_body)
    elif provider == "ses_api":
        ok, err = _ses_api_send(cfg, to_email, subject, text_body, html_body)
    else:
        from_name, from_addr = _resolve_from(cfg)
        from_str = formataddr((from_name, from_addr)) if from_name else from_addr
        msg = _build_mime_msg(from_str, to_email, subject, text_body, html_body,
                              app_name=app_name)
        ok, err = _smtp_send(cfg, msg)

    if ok:
        _try_log_email(to_email, log_subject, "otp")
    return ok, err

def send_submission_success_email(cfg: dict, to_email: str, app_name: str,
                                   submission_title: str,
                                   file_names: list | None = None) -> Tuple[bool, str | None]:

    accent = cfg.get("accent", "#f27d00")
    subject = f"Submission received — {submission_title}"
    text_body = _build_submission_success_text(app_name, submission_title, file_names)
    html_body = _build_submission_success_html(app_name, submission_title, file_names, accent=accent)
    ok, err = send_notification_email(cfg, to_email, subject, html_body, text_body, category="submit")
    return ok, err

def send_submission_failed_email(cfg: dict, to_email: str, app_name: str,
                                  submission_title: str, error_msg: str) -> Tuple[bool, str | None]:

    accent = cfg.get("accent", "#f27d00")
    subject = f"Action required: please resubmit — {submission_title}"
    text_body = _build_submission_failed_text(app_name, submission_title, error_msg)
    html_body = _build_submission_failed_html(app_name, submission_title, error_msg, accent=accent)
    ok, err = send_notification_email(cfg, to_email, subject, html_body, text_body, category="submit")
    return ok, err

def send_notification_email(cfg: dict, to_email: str, subject: str,
                             body_html: str, body_text: str,
                             category: str = "notification",
                             _pixel_id: str | None = None) -> Tuple[bool, str | None]:

    provider = (cfg.get("provider") or "smtp").lower()
    if provider == "postfix":
        provider = "smtp"

    base_url = cfg.get("base_url", "")
    tracking_on = bool(base_url) and cfg.get("tracking_enabled", True)
    pixel_id = None
    if tracking_on:
        pixel_id = _pixel_id or _generate_pixel_id()
        if body_html:
            body_html = _inject_pixel(body_html, _pixel_url(base_url, pixel_id))

    if provider == "brevo_api":
        ok, err = _brevo_api_send(cfg, to_email, subject, body_text, body_html)
    elif provider == "ses_api":
        ok, err = _ses_api_send(cfg, to_email, subject, body_text, body_html)
    else:
        from_name, from_addr = _resolve_from(cfg)
        from_str = formataddr((from_name, from_addr)) if from_name else from_addr
        msg = _build_mime_msg(from_str, to_email, subject, body_text or "",
                              body_html, app_name=from_name)
        ok, err = _smtp_send(cfg, msg)

    if ok:
        stored_html = body_html if cfg.get("log_body", True) else None
        _try_log_email(to_email, subject, category,
                       body_html=stored_html,
                       tracking_pixel_id=pixel_id)
    return ok, err

def send_certificate_email(cfg: dict, to_email: str, recipient_name: str,
                            competition_name: str, school_name: str,
                            award_type: str, sorry_message: str = "",
                            include_pdf: bool = True) -> Tuple[bool, str | None]:

    accent = cfg.get("accent", "#f27d00")
    app_name = cfg.get("app_name", "prompt-a-thon")
    base_url = cfg.get("base_url", "")
    provider = (cfg.get("provider") or "smtp").lower()
    if provider == "postfix":
        provider = "smtp"

    tracking_on = bool(base_url) and cfg.get("tracking_enabled", True)

    if award_type == "sorry":
        subject = f"{competition_name} — Results"
        text_body = _build_sorry_text(app_name, recipient_name, competition_name, sorry_message)
        html_body = _build_sorry_html(
            app_name, recipient_name, competition_name, school_name,
            sorry_message, accent=accent
        )
    else:
        labels = {"first": "1st Place", "second": "2nd Place", "third": "3rd Place"}
        label = labels.get(award_type, "Achievement")
        subject = f"Congratulations! {label} — {competition_name}"
        text_body = _build_certificate_text(
            app_name, recipient_name, competition_name, school_name, award_type
        )
        html_body = _build_certificate_html(
            app_name, recipient_name, competition_name, school_name,
            award_type, accent=accent
        )

    pixel_id = None
    if tracking_on:
        pixel_id = _generate_pixel_id()
        if html_body:
            html_body = _inject_pixel(html_body, _pixel_url(base_url, pixel_id))

    pdf_data = None
    pdf_name = None
    if include_pdf and award_type != "sorry":
        try:
            pdf_data = generate_certificate_pdf(
                recipient_name, to_email, competition_name, school_name,
                award_type, accent=accent
            )
            pdf_name = f"certificate_{award_type}.pdf"
        except Exception:
            pdf_data = None

    if provider == "brevo_api":
        ok, err = _brevo_api_send(cfg, to_email, subject, text_body, html_body,
                                   attachment_data=pdf_data, attachment_name=pdf_name)
    elif provider == "ses_api":
        from_name, from_addr = _resolve_from(cfg)
        from_str = formataddr((from_name, from_addr)) if from_name else from_addr
        import io as _io
        import email.generator as _eg
        mime_msg = _build_mime_msg(from_str, to_email, subject, text_body or "",
                                   html_body, pdf_data=pdf_data, pdf_name=pdf_name,
                                   app_name=from_name)
        buf = _io.BytesIO()
        _eg.BytesGenerator(buf).flatten(mime_msg)
        raw_bytes = buf.getvalue()
        params = {
            "Action": "SendRawEmail",
            "Version": "2010-12-01",
            "Destinations.member.1": to_email,
            "RawMessage.Data": base64.b64encode(raw_bytes).decode("ascii"),
        }
        payload = urllib.parse.urlencode(params).encode("utf-8")
        access_key = (cfg.get("ses_access_key") or "").strip()
        secret_key = (cfg.get("ses_secret_key") or "").strip()
        region = (cfg.get("ses_region") or "us-east-1").strip()
        host = f"email.{region}.amazonaws.com"
        headers = _aws_sign_v4("POST", host, region, "ses", access_key, secret_key, payload)
        req = urllib.request.Request(f"https://{host}/", data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                ok = 200 <= resp.status < 300
                err = None if ok else f"SES HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            ok = False
            err = f"SES error HTTP {exc.code}"
        except (urllib.error.URLError, OSError) as exc:
            ok, err = False, f"SES error: {exc}"
    else:
        from_name, from_addr = _resolve_from(cfg)
        from_str = formataddr((from_name, from_addr)) if from_name else from_addr
        msg = _build_mime_msg(from_str, to_email, subject, text_body or "",
                              html_body, pdf_data=pdf_data, pdf_name=pdf_name,
                              app_name=app_name)
        ok, err = _smtp_send(cfg, msg)

    if ok:
        stored_html = html_body if cfg.get("log_body", True) else None
        _try_log_email(to_email, subject, "certificate",
                       body_html=stored_html,
                       tracking_pixel_id=pixel_id)
    return ok, err, pdf_data is not None
