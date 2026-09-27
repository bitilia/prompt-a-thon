import json
import re
from datetime import date, timezone

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from decorators import admin_required, judge_required
from extensions import db, limiter
from models import (
    AuditLog, ROLE_ADMIN, ROLE_JUDGE, ROLE_PARTICIPANT, SCORE_FIELDS, SUBMISSION_STATUSES,
    SiteSettings, Submission, SubmissionFile, TaskSelection, User, VALID_ROLES, write_audit,
)
from models_gdpr import DataRequest
from services.academy import save_academy
from services.tasks import get_task_list, save_tasks

bp = Blueprint("admin", __name__, url_prefix="/admin")

def _client_ip() -> str:
    from middleware.security import client_ip
    return client_ip(request)

def _is_iso_date(s: str) -> bool:
    if not s:
        return True
    try:
        date.fromisoformat(s.strip())
        return True
    except (ValueError, AttributeError):
        return False

@bp.route("/")
@login_required
@admin_required
def index():
    user_count = db.session.query(func.count(User.id)).scalar() or 0
    submission_count = db.session.query(func.count(Submission.id)).scalar() or 0
    reviewed_count = (
        db.session.query(func.count(Submission.id))
        .filter(Submission.status == "reviewed").scalar() or 0
    )
    pending_count = (
        db.session.query(func.count(Submission.id))
        .filter(Submission.status.in_(["submitted", "under_review"])).scalar() or 0
    )
    audit = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(20).all()
    return render_template(
        "admin/index.html",
        user_count=user_count,
        submission_count=submission_count,
        reviewed_count=reviewed_count,
        pending_count=pending_count,
        audit=audit,
        registration_open=SiteSettings.registration_is_open(),
        submission_open=SiteSettings.submission_is_open(),
        update_status=_get_update_status(),
    )

_SETUP_ROLE_PRIORITY = {"admin": 2, "judge": 1, "participant": 0}
_SETUP_ADMIN_STEPS = 4
_SETUP_JUDGE_STEPS = 2

def _needs_setup(user) -> bool:

    if not (user.is_admin or user.is_judge):
        return False
    done = SiteSettings.get(f"setup_done_u{user.id}")
    current_prio = _SETUP_ROLE_PRIORITY.get(user.role, 0)
    done_prio = _SETUP_ROLE_PRIORITY.get(done, -1)
    return current_prio > done_prio

@bp.route("/setup")
@login_required
def setup_wizard():
    if not (current_user.is_admin or current_user.is_judge):
        return redirect(url_for("participant.index"))
    if not _needs_setup(current_user):
        if current_user.is_admin:
            return redirect(url_for("admin.index"))
        return redirect(url_for("admin.submissions"))

    step_key = f"setup_step_u{current_user.id}"
    try:
        step = int(SiteSettings.get(step_key) or 1)
    except (ValueError, TypeError):
        step = 1
    total_steps = _SETUP_ADMIN_STEPS if current_user.is_admin else _SETUP_JUDGE_STEPS
    step = max(1, min(step, total_steps))

    if current_user.is_admin and step == 1:
        if SiteSettings.get("registration_override") is None and \
                SiteSettings.get("registration_open_date") is None:
            SiteSettings.set("registration_override", "0")
        if SiteSettings.get("submission_override") is None and \
                SiteSettings.get("submission_open_date") is None:
            SiteSettings.set("submission_override", "0")
        db.session.commit()

    date_keys = [
        "registration_open_date", "registration_close_date",
        "event_day_date", "submission_open_date",
        "submission_deadline_date", "results_date",
    ]
    dates = {k: SiteSettings.get(k, "") or "" for k in date_keys}

    return render_template(
        "admin/setup.html",
        step=step,
        total_steps=total_steps,
        dates=dates,
        email_allowlist_domains=SiteSettings.email_allowlist_domains(),
        email_allowlist_mode=SiteSettings.email_allowlist_mode(),
    )

@bp.route("/setup/step/<int:step>", methods=["POST"])
@login_required
def setup_wizard_step(step):
    if not (current_user.is_admin or current_user.is_judge):
        return redirect(url_for("participant.index"))

    total_steps = _SETUP_ADMIN_STEPS if current_user.is_admin else _SETUP_JUDGE_STEPS
    step_key = f"setup_step_u{current_user.id}"

    if current_user.is_admin:
        if step == 2:
            date_keys = [
                "registration_open_date", "registration_close_date",
                "event_day_date", "submission_open_date",
                "submission_deadline_date", "results_date",
            ]
            has_dates = False
            for k in date_keys:
                v = (request.form.get(k) or "").strip()
                if v:
                    if not _is_iso_date(v):
                        flash(f"Invalid date format. Use YYYY-MM-DD.", "error")
                        SiteSettings.set(step_key, str(step))
                        db.session.commit()
                        return redirect(url_for("admin.setup_wizard"))
                    SiteSettings.set(k, v)
                    has_dates = True
                else:
                    SiteSettings.set(k, None)
            if has_dates:
                SiteSettings.set("registration_override", None)
                SiteSettings.set("submission_override", None)
            else:
                SiteSettings.set("registration_override", "0")
                SiteSettings.set("submission_override", "0")
            write_audit("setup.timeline_configured", actor=current_user, ip=_client_ip(),
                        detail=f"has_dates={has_dates}")

        elif step == 3:
            mode = (request.form.get("email_allowlist_mode") or "allowlist").strip()
            if mode not in ("allowlist", "allow_all"):
                mode = "allowlist"
            SiteSettings.set("email_allowlist_mode", mode)
            if mode == "allowlist":
                raw = (request.form.get("domains") or "").strip()
                domains = [
                    d.strip().lower().lstrip("@")
                    for d in raw.replace(",", "\n").splitlines()
                    if d.strip()
                ]
                valid = [d for d in domains if _DOMAIN_RE.match(d)]
                if valid:
                    SiteSettings.set_email_allowlist_domains(valid)
                write_audit("setup.allowlist_configured", actor=current_user, ip=_client_ip(),
                            detail=f"mode={mode} domains={','.join(valid) if mode == 'allowlist' else ''}")

    next_step = step + 1
    if next_step > total_steps:
        SiteSettings.set(f"setup_done_u{current_user.id}", current_user.role)
        SiteSettings.set(step_key, None)
        write_audit("setup.completed", actor=current_user, ip=_client_ip(),
                    detail=f"role={current_user.role}")
        db.session.commit()
        if current_user.is_admin:
            flash("Setup complete. Welcome to the admin panel!", "success")
            return redirect(url_for("admin.index"))
        flash("Setup complete. You're ready to review submissions.", "success")
        return redirect(url_for("admin.submissions"))

    SiteSettings.set(step_key, str(next_step))
    db.session.commit()
    return redirect(url_for("admin.setup_wizard"))

@bp.route("/setup/back/<int:step>")
@login_required
def setup_wizard_back(step):
    if not (current_user.is_admin or current_user.is_judge):
        return redirect(url_for("participant.index"))
    new_step = max(1, step - 1)
    SiteSettings.set(f"setup_step_u{current_user.id}", str(new_step))
    db.session.commit()
    return redirect(url_for("admin.setup_wizard"))

@bp.route("/setup/skip", methods=["POST"])
@login_required
def setup_wizard_skip():
    if not (current_user.is_admin or current_user.is_judge):
        return redirect(url_for("participant.index"))
    SiteSettings.set(f"setup_done_u{current_user.id}", current_user.role)
    SiteSettings.set(f"setup_step_u{current_user.id}", None)
    write_audit("setup.skipped", actor=current_user, ip=_client_ip())
    db.session.commit()
    if current_user.is_admin:
        return redirect(url_for("admin.index"))
    return redirect(url_for("admin.submissions"))

@bp.route("/audit-log")
@login_required
@admin_required
def audit_log():
    q = request.args.get("q", "").strip()
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 50

    query = AuditLog.query.order_by(AuditLog.created_at.desc())
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                AuditLog.action.ilike(like),
                AuditLog.actor_email.ilike(like),
                AuditLog.detail.ilike(like),
                AuditLog.target_type.ilike(like),
                AuditLog.target_id.ilike(like),
                AuditLog.ip_address.ilike(like),
            )
        )

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return render_template(
        "admin/audit_log.html",
        entries=pagination.items,
        pagination=pagination,
        q=q,
    )

@bp.route("/users")
@login_required
@admin_required
def users():
    q = (request.args.get("q") or "").strip()
    role = (request.args.get("role") or "").strip()
    query = User.query
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(or_(
            func.lower(User.email).like(like),
            func.lower(User.name).like(like),
        ))
    if role in VALID_ROLES:
        query = query.filter_by(role=role)
    rows = query.order_by(User.created_at.desc()).limit(500).all()
    return render_template("admin/users.html", users=rows, q=q, role=role,
                           valid_roles=sorted(VALID_ROLES))

@bp.route("/users.csv")
@login_required
@admin_required
def users_csv():

    import csv
    import io
    from datetime import datetime, timezone
    from flask import Response

    rows = User.query.order_by(User.created_at.asc()).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "email", "name", "role", "auth_provider", "ms_object_id",
        "is_banned", "ban_reason", "created_at_utc",
        "last_login_at_utc", "last_login_ip",
    ])
    for u in rows:
        writer.writerow([
            u.id,
            u.email,
            u.name or "",
            u.role,
            u.auth_provider,
            u.ms_object_id or "",
            "1" if u.is_banned else "0",
            (u.ban_reason or "").replace("\n", " ").replace("\r", " "),
            u.created_at.isoformat() + "Z" if u.created_at else "",
            u.last_login_at.isoformat() + "Z" if u.last_login_at else "",
            u.last_login_ip or "",
        ])

    write_audit("users.exported_csv", actor=current_user, ip=_client_ip(),
                detail=f"rows={len(rows)}")
    db.session.commit()

    stamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d_%H%M%S")
    filename = f"users_{stamp}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )

@bp.route("/server")
@login_required
@admin_required
def server_status():

    import os, shutil, time, subprocess
    from pathlib import Path

    root_dir = Path(current_app.root_path).resolve()
    upload_dir = Path(current_app.config.get("UPLOAD_FOLDER") or "/")
    instance_dir = Path(current_app.instance_path)

    try:
        total, used, free = shutil.disk_usage(str(root_dir))
        disk = {
            "total": total, "used": used, "free": free,
            "percent": round((used / total) * 100, 1) if total else 0,
        }
    except OSError:
        disk = None

    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                rest = rest.strip().split()[0]
                if k in ("MemTotal", "MemAvailable", "MemFree", "Buffers", "Cached", "SwapTotal", "SwapFree"):
                    mem[k] = int(rest) * 1024
        if mem.get("MemTotal") and mem.get("MemAvailable") is not None:
            mem["used"] = mem["MemTotal"] - mem["MemAvailable"]
            mem["percent"] = round((mem["used"] / mem["MemTotal"]) * 100, 1)
    except OSError:
        mem = {}

    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    cpu_count = os.cpu_count() or 1
    try:
        with open("/proc/cpuinfo") as f:
            cpu_model = "(unknown)"
            for line in f:
                if line.lower().startswith("model name"):
                    cpu_model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        cpu_model = "(unknown)"

    try:
        with open("/proc/uptime") as f:
            uptime_seconds = int(float(f.read().split()[0]))
    except OSError:
        uptime_seconds = 0

    def _dir_size(p: Path, cap_files: int = 10000) -> int:
        if not p.exists():
            return 0
        total = 0
        n = 0
        try:
            for entry in p.rglob("*"):
                if entry.is_file():
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
                    n += 1
                    if n >= cap_files:
                        break
        except OSError:
            pass
        return total

    app_sizes = {
        "uploads": _dir_size(upload_dir),
        "instance": _dir_size(instance_dir),
        "logs": _dir_size(root_dir / "logs"),
    }

    counts = {
        "users": User.query.count(),
        "submissions": Submission.query.count(),
        "audit_log": AuditLog.query.count(),
    }

    from services.file_pipeline import SOCKET_PATH, CONVERSION_DIR
    convert_status = {"running": False, "detail": "Socket not found"}
    if SOCKET_PATH.exists():
        try:
            import socket as _sock
            s = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
            s.settimeout(2)
            s.connect(str(SOCKET_PATH))
            s.close()
            convert_status = {"running": True, "detail": "Daemon responding"}
        except Exception as e:
            convert_status = {"running": False, "detail": f"Socket exists but not responding: {e}"}

    try:
        r = subprocess.run(
            ["systemctl", "is-active", "prompt-a-thon-convert"],
            capture_output=True, text=True, timeout=3
        )
        convert_status["systemd"] = r.stdout.strip()
    except Exception:
        convert_status["systemd"] = "unknown"

    log_lines = []
    log_filter = request.args.get("log_filter", "").strip().lower()
    log_path = root_dir / "logs" / "error.log"
    if not log_path.exists():
        alt = root_dir / "logs" / "app.log"
        if alt.exists():
            log_path = alt
    try:
        if log_path.exists():
            with open(log_path, "r", errors="replace") as lf:
                all_lines = lf.readlines()

            all_lines = all_lines[-500:][::-1]
            for line in all_lines:
                if not log_filter or log_filter in line.lower():
                    log_lines.append(line.rstrip())
                if len(log_lines) >= 200:
                    break
    except OSError:
        log_lines = ["(log file not readable)"]

    return render_template(
        "admin/server.html",
        disk=disk, mem=mem,
        convert_status=convert_status,
        load1=load1, load5=load5, load15=load15,
        cpu_count=cpu_count, cpu_model=cpu_model,
        uptime_seconds=uptime_seconds,
        app_sizes=app_sizes,
        counts=counts,
        log_lines=log_lines,
        log_filter=log_filter,
        log_path=str(log_path),
    )

@bp.route("/users/<int:user_id>/toggle-ban", methods=["POST"])
@login_required
@admin_required
def toggle_ban(user_id):
    target = db.session.get(User, user_id)
    if not target:
        abort(404)
    if target.id == current_user.id:
        flash("You cannot ban your own account.", "error")
        return redirect(url_for("admin.users"))

    reason = (request.form.get("reason") or "").strip()[:500] or None
    target.is_banned = not target.is_banned
    target.ban_reason = reason if target.is_banned else None
    write_audit(
        "user.banned" if target.is_banned else "user.unbanned",
        actor=current_user,
        target_type="user",
        target_id=target.id,
        detail=reason,
        ip=_client_ip(),
    )
    db.session.commit()
    flash(("User banned." if target.is_banned else "User unbanned."), "success")
    return redirect(url_for("admin.users"))

@bp.route("/users/<int:user_id>/set-role", methods=["POST"])
@login_required
@admin_required
def set_role(user_id):
    target = db.session.get(User, user_id)
    if not target:
        abort(404)
    new_role = (request.form.get("role") or "").strip()
    if new_role not in VALID_ROLES:
        flash("Invalid role.", "error")
        return redirect(url_for("admin.users"))
    if target.id == current_user.id and new_role != ROLE_ADMIN:
        flash("You cannot demote your own admin account.", "error")
        return redirect(url_for("admin.users"))

    old = target.role
    target.role = new_role

    if new_role in (ROLE_JUDGE, ROLE_ADMIN) and new_role != old:
        SiteSettings.set(f"setup_done_u{target.id}", None)
        SiteSettings.set(f"setup_step_u{target.id}", None)
    write_audit(
        "user.role_changed",
        actor=current_user,
        target_type="user",
        target_id=target.id,
        detail=f"{old} -> {new_role}",
        ip=_client_ip(),
    )
    db.session.commit()
    flash("Role updated.", "success")
    return redirect(url_for("admin.users"))

@bp.route("/users/<int:user_id>/set-judge-fields", methods=["POST"])
@login_required
@admin_required
def set_judge_fields(user_id):
    target = db.session.get(User, user_id)
    if not target:
        abort(404)
    if not target.is_judge:
        flash("User is not a judge.", "error")
        return redirect(url_for("admin.users"))
    selected = [f for f in request.form.getlist("score_fields") if f in SCORE_FIELDS]
    target.judge_score_fields = ",".join(selected) if selected else None
    write_audit(
        "user.judge_fields_updated",
        actor=current_user,
        target_type="user",
        target_id=target.id,
        detail=",".join(selected) or "none",
        ip=_client_ip(),
    )
    db.session.commit()
    flash("Judge score access updated.", "success")
    return redirect(url_for("admin.users"))

@bp.route("/submissions")
@login_required
@judge_required
def submissions():
    _FILTER_STATUSES = {"processing": "Processing", "failed": "Failed",
                        **SUBMISSION_STATUSES}

    _DEFAULT_HIDDEN = {"processing", "failed"}
    task_id = (request.args.get("task_id") or "").strip()

    requested = [s for s in request.args.getlist("status") if s in _FILTER_STATUSES]
    selected = requested or [k for k in _FILTER_STATUSES if k not in _DEFAULT_HIDDEN]

    query = Submission.query.filter(Submission.status.in_(selected))
    if task_id:
        query = query.filter_by(task_id=task_id)
    rows = query.order_by(Submission.submitted_at.desc()).limit(500).all()
    user_ids = {r.user_id for r in rows}
    users_map = {
        u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()
    } if user_ids else {}
    task_titles = {t["id"]: t["title"] for t in get_task_list()}

    judges = User.query.filter_by(role=ROLE_JUDGE).all()
    field_judges = {"score1": [], "score2": [], "score3": []}
    for j in judges:
        for f in j.judge_score_fields_list:
            if f in field_judges:
                field_judges[f].append(j)

    def _missing_fields(sub):
        missing = []
        for field, fjudges in field_judges.items():
            if fjudges and getattr(sub, field) is None:
                missing.append(field)
        return missing

    return render_template(
        "admin/submissions.html",
        submissions=rows,
        users_map=users_map,
        task_titles=task_titles,
        statuses=SUBMISSION_STATUSES,
        filter_statuses=_FILTER_STATUSES,
        selected_statuses=selected,
        default_hidden=_DEFAULT_HIDDEN,
        task_id=task_id,
        tasks=get_task_list(),
        field_judges=field_judges,
        missing_fields_fn=_missing_fields,
    )

@bp.route("/submissions/<anon_id>/review", methods=["GET", "POST"])
@login_required
@judge_required
def review(anon_id):
    sub = Submission.query.filter_by(anonymous_id=anon_id).first_or_404()

    submitter = db.session.get(User, sub.user_id)
    task_titles = {t["id"]: t["title"] for t in get_task_list()}

    if request.method == "POST":
        status = (request.form.get("status") or "").strip()
        if status not in SUBMISSION_STATUSES:
            flash("Invalid status.", "error")
            return redirect(url_for("admin.review", anon_id=anon_id))

        scores: list[int | None] = []
        for n in (1, 2, 3):
            raw = (request.form.get(f"score{n}") or "").strip()
            if not raw:
                scores.append(None)
                continue
            try:
                v = int(raw)
            except ValueError:
                flash(f"Score {n} must be a whole number 1-20.", "error")
                return redirect(url_for("admin.review", anon_id=anon_id))
            if v < 1 or v > 20:
                flash(f"Score {n} must be between 1 and 20.", "error")
                return redirect(url_for("admin.review", anon_id=anon_id))
            scores.append(v)

        notes = (request.form.get("notes") or "").strip()[:5000] or None

        sub.status = status

        post_allowed = (
            SCORE_FIELDS if current_user.is_admin
            else current_user.judge_score_fields_list
        )
        for i, field in enumerate(("score1", "score2", "score3")):
            if field in post_allowed:
                setattr(sub, field, scores[i])

        all_scores = [sub.score1, sub.score2, sub.score3]
        if all(s is None for s in all_scores):
            sub.score = None
        else:
            sub.score = sum((s or 0) for s in all_scores)
        sub.judge_notes = notes
        sub.reviewed_by = current_user.id
        write_audit(
            "submission.reviewed",
            actor=current_user,
            target_type="submission",
            target_id=sub.id,
            detail=f"status={status} scores={scores}",
            ip=_client_ip(),
        )
        db.session.commit()
        flash("Review saved.", "success")
        return redirect(url_for("admin.submissions"))

    allowed_fields = (
        SCORE_FIELDS if current_user.is_admin
        else current_user.judge_score_fields_list
    )
    return render_template(
        "admin/review.html",
        sub=sub,
        submitter=submitter,
        task_titles=task_titles,
        statuses=SUBMISSION_STATUSES,
        allowed_fields=allowed_fields,
    )

@bp.route("/submissions/<anon_id>/terminate", methods=["POST"])
@login_required
@admin_required
@limiter.limit("30 per minute")
def terminate_submission(anon_id):

    sub = Submission.query.filter_by(anonymous_id=anon_id).first_or_404()
    if sub.status != "processing":
        flash("This submission is not currently processing.", "warning")
        return redirect(url_for("admin.submissions"))
    sub.status = "failed"
    write_audit("submission.terminated", actor=current_user,
                target_type="submission", target_id=sub.id, ip=_client_ip())
    submitter_email = None
    try:
        submitter = db.session.get(User, sub.user_id)
        if submitter:
            submitter_email = submitter.email
    except Exception:
        pass
    db.session.commit()
    try:
        from services.conversion_queue import get_queue
        get_queue().mark_terminated(sub.id)
    except Exception:
        pass
    try:
        if submitter_email:
            from services.conversion_queue import _build_mail_cfg
            from services.email import send_submission_failed_email
            send_submission_failed_email(
                _build_mail_cfg(current_app._get_current_object()),
                submitter_email,
                current_app.config.get("APP_NAME", "prompt-a-thon"),
                sub.title or "",
                "Your submission was terminated by an administrator.",
            )
    except Exception as exc:
        current_app.logger.warning("terminate email failed: %s", exc)
    flash(f"Submission {anon_id} has been terminated.", "success")
    return redirect(url_for("admin.submissions"))

def _get_update_status() -> dict:

    if current_app.config.get("MANAGED_MODE"):
        return {}
    try:
        from services.update_checker import get_update_status, check_manifest
        status = get_update_status()
        if not status or not status.get("checked_at") or status.get("last_check_error"):
            status = check_manifest()
        return status
    except Exception as exc:
        return {"last_check_error": str(exc)}

@bp.route("/update")
@login_required
@admin_required
def updates():

    if current_app.config.get("MANAGED_MODE"):
        abort(404)
    status = _get_update_status()
    try:
        from services.update_checker import get_installed_version
        installed = status.get("installed_version") or get_installed_version()
    except Exception:
        installed = status.get("installed_version") or "unknown"
    return render_template(
        "admin/updates.html",
        status=status,
        releases=status.get("releases") or [],
        latest=status.get("latest"),
        installed_version=installed,
        installed_status=status.get("installed_status", ""),
        checked_at=status.get("checked_at"),
        last_check_error=status.get("last_check_error"),
        update_status=status,
    )

@bp.route("/download-update", methods=["POST"])
@login_required
@admin_required
@limiter.limit("10 per hour")
def download_update():

    if current_app.config.get("MANAGED_MODE"):
        abort(404)
    return jsonify({
        "ok": False,
        "error": "This server does not download or run update files.",
    }), 410

@bp.route("/recheck-updates", methods=["POST"])
@login_required
@admin_required
@limiter.limit("8 per hour")
def recheck_updates():

    if current_app.config.get("MANAGED_MODE"):
        abort(404)
    try:
        from services.update_checker import check_manifest
        status = check_manifest()
        return jsonify({
            "ok": True,
            "has_error": bool(status.get("last_check_error")),
            "error": status.get("last_check_error"),
            "checked_at": status.get("checked_at"),
        })
    except Exception as exc:
        current_app.logger.error("recheck-updates failed: %s", exc)
        return jsonify({"ok": False, "has_error": True, "error": str(exc)}), 500

def _docx_conversion_available() -> bool:
    import shutil as _sh
    from services.file_pipeline import SOCKET_PATH
    has_lo = bool(_sh.which("libreoffice") or _sh.which("soffice"))
    return has_lo and SOCKET_PATH.exists()

@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    keys = [
        "registration_open_date",
        "registration_close_date",
        "event_day_date",
        "submission_open_date",
        "submission_deadline_date",
        "results_date",
    ]
    overrides = ["registration_override", "submission_override"]

    if request.method == "POST":

        for k in keys:
            v = (request.form.get(k) or "").strip()
            if not _is_iso_date(v):
                flash(f"Invalid date for {k}. Use YYYY-MM-DD.", "error")
                return redirect(url_for("admin.settings"))

        for k in keys:
            v = (request.form.get(k) or "").strip()
            SiteSettings.set(k, v or None)

        for k in overrides:
            v = (request.form.get(k) or "").strip()
            if v in ("0", "1", ""):
                SiteSettings.set(k, v or None)
            else:
                SiteSettings.set(k, None)

        success_msg = (request.form.get("submission_success_message") or "").strip()
        SiteSettings.set("submission_success_message", success_msg or None)

        accepted_files = (request.form.get("accepted_files") or "pdf").strip()
        if accepted_files not in ("pdf", "pdf_docx", "none"):
            accepted_files = "pdf"
        SiteSettings.set("accepted_files", accepted_files)

        mode = (request.form.get("email_allowlist_mode") or "").strip()
        if mode in ("allowlist", "allow_all"):
            SiteSettings.set("email_allowlist_mode", mode)

        ann_on = "1" if request.form.get("announcement_enabled") == "1" else "0"
        SiteSettings.set("announcement_enabled", ann_on)
        ann_text = (request.form.get("announcement_text") or "").strip()[:280]
        SiteSettings.set("announcement_text", ann_text or None)

        lockdown_on = "1" if request.form.get("lockdown_enabled") == "1" else "0"

        previous_lockdown = SiteSettings.get("lockdown_enabled") or "0"
        SiteSettings.set("lockdown_enabled", lockdown_on)
        if lockdown_on != previous_lockdown:
            write_audit(
                f"lockdown.{'enabled' if lockdown_on == '1' else 'disabled'}",
                actor=current_user, ip=_client_ip(),
            )
        lockdown_msg = (request.form.get("lockdown_message") or "").strip()[:280]
        SiteSettings.set("lockdown_message", lockdown_msg or None)

        write_audit("settings.updated", actor=current_user, ip=_client_ip())
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    values = {k: SiteSettings.get(k, "") or "" for k in keys}
    overrides_v = {k: SiteSettings.get(k, "") or "" for k in overrides}
    return render_template(
        "admin/settings.html",
        values=values, overrides=overrides_v,
        submission_success_message=SiteSettings.submission_success_message(),
        email_allowlist_mode=SiteSettings.email_allowlist_mode(),
        email_allowlist_domains=SiteSettings.email_allowlist_domains(),
        announcement_enabled=SiteSettings.announcement_enabled(),
        announcement_text=SiteSettings.announcement_text(),
        lockdown_enabled=SiteSettings.lockdown_enabled(),
        lockdown_message=SiteSettings.lockdown_message(),
        accepted_files=SiteSettings.get("accepted_files", "pdf") or "pdf",
        docx_available=_docx_conversion_available(),
        update_status=_get_update_status(),
    )

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})+$")

@bp.route("/allowlist/add", methods=["POST"])
@login_required
@admin_required
def allowlist_add():
    domain = (request.form.get("domain") or "").strip().lower().lstrip("@")
    if not domain or not _DOMAIN_RE.match(domain):
        flash(f"{domain!r} doesn't look like a valid domain.", "error")
        return redirect(url_for("admin.settings") + "#allowlist")
    domains = SiteSettings.email_allowlist_domains()
    if domain in domains:
        flash(f"{domain} is already on the allowlist.", "info")
    else:
        domains.append(domain)
        SiteSettings.set_email_allowlist_domains(domains)
        write_audit("allowlist.add", actor=current_user, target_type="domain",
                    detail=domain, ip=_client_ip())
        db.session.commit()
        flash(f"Added {domain} to the allowlist.", "success")
    return redirect(url_for("admin.settings") + "#allowlist")

@bp.route("/allowlist/remove", methods=["POST"])
@login_required
@admin_required
def allowlist_remove():
    domain = (request.form.get("domain") or "").strip().lower().lstrip("@")
    domains = SiteSettings.email_allowlist_domains()
    if domain in domains:
        domains.remove(domain)
        SiteSettings.set_email_allowlist_domains(domains)
        write_audit("allowlist.remove", actor=current_user, target_type="domain",
                    detail=domain, ip=_client_ip())
        db.session.commit()
        flash(f"Removed {domain} from the allowlist.", "success")
    else:
        flash(f"{domain} was not on the allowlist.", "info")
    return redirect(url_for("admin.settings") + "#allowlist")

@bp.route("/config/tasks", methods=["GET", "POST"])
@login_required
@admin_required
def config_tasks():
    error = None
    raw_text = ""
    if request.method == "POST":
        raw_text = request.form.get("config") or ""
        try:
            data = json.loads(raw_text)
            save_tasks(data)
            write_audit("config.tasks_updated", actor=current_user, ip=_client_ip())
            db.session.commit()
            flash("tasks.json saved.", "success")
            return redirect(url_for("admin.config_tasks"))
        except json.JSONDecodeError as e:
            error = f"JSON parse error: {e}"
        except ValueError as e:
            error = str(e)
    else:

        from pathlib import Path as _Path
        path = _Path(current_app.root_path) / "config" / "tasks.json"
        if path.exists():
            try:
                raw_text = path.read_text(encoding="utf-8")
            except OSError:
                raw_text = ""

    return render_template(
        "admin/tasks_config.html", config_text=raw_text, error=error
    )

@bp.route("/config/academy", methods=["GET", "POST"])
@login_required
@admin_required
def config_academy():
    error = None
    raw_text = ""
    if request.method == "POST":
        raw_text = request.form.get("config") or ""
        try:
            data = json.loads(raw_text)
            save_academy(data)
            write_audit("config.academy_updated", actor=current_user, ip=_client_ip())
            db.session.commit()
            flash("academy.json saved.", "success")
            return redirect(url_for("admin.config_academy"))
        except json.JSONDecodeError as e:
            error = f"JSON parse error: {e}"
        except ValueError as e:
            error = str(e)
    else:
        from pathlib import Path as _Path
        path = _Path(current_app.root_path) / "config" / "academy.json"
        if path.exists():
            try:
                raw_text = path.read_text(encoding="utf-8")
            except OSError:
                raw_text = ""

    return render_template(
        "admin/academy_config.html", config_text=raw_text, error=error
    )

@bp.route("/data-requests")
@login_required
@admin_required
def data_requests():
    from datetime import datetime as _datetime, timezone
    rows = DataRequest.query.order_by(DataRequest.created_at.desc()).limit(500).all()
    return render_template("admin/data_requests.html", requests=rows, now=_datetime.now(timezone.utc).replace(tzinfo=None))

@bp.route("/data-requests/<int:req_id>/resolve", methods=["POST"])
@login_required
@admin_required
def data_request_resolve(req_id):
    req_row = db.session.get(DataRequest, req_id)
    if req_row is None:
        abort(404)
    req_row.status = "resolved"
    write_audit("gdpr.request_resolved", actor=current_user, target_type="data_request",
                target_id=req_id, ip=_client_ip())
    db.session.commit()
    flash(f"Marked data request #{req_id} as resolved.", "success")
    return redirect(url_for("admin.data_requests"))

def _mail_cfg() -> dict:
    cfg = current_app.config
    accent = SiteSettings.get("theme_accent", _DEFAULT_ACCENT)
    accent = _LEGACY_ACCENT_HEX.get(accent, accent)
    if not re.match(r'^#[0-9a-fA-F]{6}$', accent):
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

def _collect_user_data(user):

    from models import Submission, AcademyProgress, OTPToken
    from models_gdpr import ConsentRecord
    from services.tasks import get_task_list

    task_titles = {t["id"]: t["title"] for t in get_task_list()}

    submissions = Submission.query.filter_by(user_id=user.id).order_by(Submission.submitted_at).all()
    academy = AcademyProgress.query.filter_by(user_id=user.id).all()
    consents = ConsentRecord.query.filter_by(user_id=user.id).order_by(ConsentRecord.granted_at).all()

    return {
        "user": user,
        "submissions": submissions,
        "academy": academy,
        "consents": consents,
        "task_titles": task_titles,
    }

def _build_access_email_body(data, app_name, base_url="", accent="#f27d00"):

    u = data["user"]
    lines = [
        f"{app_name} – Your Personal Data",
        "=" * 50,
        "",
        "ACCOUNT INFORMATION",
        f"  Email:          {u.email}",
        f"  Name:           {u.name or '(not set)'}",
        f"  Role:           {u.role}",
        f"  Account created:{u.created_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"  Last login:     {u.last_login_at.strftime('%Y-%m-%d %H:%M UTC') if u.last_login_at else '(never)'}",
        "",
        "SUBMISSIONS",
    ]
    if data["submissions"]:
        for s in data["submissions"]:
            lines += [
                f"  #{s.id} – {s.title}",
                f"    Task:      {data['task_titles'].get(s.task_id, s.task_id)}",
                f"    Status:    {s.status_label}",
                f"    Submitted: {s.submitted_at.strftime('%Y-%m-%d %H:%M UTC')}",
                f"    File:      {s.filename or '(none)'}",
                "",
            ]
    else:
        lines += ["  (no submissions)", ""]

    lines += ["ACADEMY PROGRESS"]
    if data["academy"]:
        for ap in data["academy"]:
            lines.append(f"  {ap.item_id}: completed {ap.completed_at.strftime('%Y-%m-%d') if ap.completed_at else '—'}")
        lines.append("")
    else:
        lines += ["  (no academy progress)", ""]

    lines += ["CONSENT RECORDS"]
    if data["consents"]:
        for c in data["consents"]:
            lines.append(f"  {c.granted_at.strftime('%Y-%m-%d %H:%M UTC')} – analytics={c.analytics}, preferences={c.preferences}")
        lines.append("")
    else:
        lines += ["  (none)", ""]

    lines += [
        "—",
        "This report was generated in response to your Subject Access Request.",
        "If you have questions, contact us at the address you submitted your request to.",
    ]

    text = "\n".join(lines)
    import html as _html
    from services.email import _branded_html_email
    inner = (
        '<p style="margin:0 0 16px;color:#475569;">Your personal data held by '
        f'<strong>{app_name}</strong>, provided in response to your Subject Access Request.</p>'
        '<pre style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
        'font-size:13px;line-height:1.6;background:#f8fafc;border:1px solid #e2e8f0;'
        'border-radius:8px;padding:16px;overflow-x:auto;white-space:pre-wrap;'
        'word-break:break-word;color:#0f172a;">'
        + _html.escape(text)
        + '</pre>'
        '<p style="margin:16px 0 0;font-size:13px;color:#64748b;">'
        'If you have questions, reply to the address you submitted your request from.</p>'
    )
    html_body = _branded_html_email(app_name, inner, accent=accent, base_url=base_url)
    subject = f"{app_name}: Your personal data (Subject Access Request)"
    return subject, text, html_body

def _build_portability_csv(data):

    import csv, io
    out = io.StringIO()
    w = csv.writer(out)

    u = data["user"]
    w.writerow(["section", "field", "value"])
    w.writerow(["account", "email", u.email])
    w.writerow(["account", "name", u.name or ""])
    w.writerow(["account", "role", u.role])
    w.writerow(["account", "created_at", u.created_at.isoformat()])
    w.writerow(["account", "last_login_at", u.last_login_at.isoformat() if u.last_login_at else ""])

    for s in data["submissions"]:
        w.writerow(["submission", "id", s.id])
        w.writerow(["submission", "title", s.title])
        w.writerow(["submission", "task", data["task_titles"].get(s.task_id, s.task_id)])
        w.writerow(["submission", "status", s.status])
        w.writerow(["submission", "submitted_at", s.submitted_at.isoformat()])
        w.writerow(["submission", "filename", s.filename or ""])
        w.writerow(["submission", "content_chars",
                    len(s.content) if s.content else 0])

    for ap in data["academy"]:
        w.writerow(["academy", "item_id", ap.item_id])
        w.writerow(["academy", "completed_at",
                    ap.completed_at.isoformat() if ap.completed_at else ""])

    return out.getvalue()

def _describe_erasure(user):

    from models import Submission, AcademyProgress, OTPToken
    from models_gdpr import ConsentRecord, DataRequest

    subs = Submission.query.filter_by(user_id=user.id).all()
    academy = AcademyProgress.query.filter_by(user_id=user.id).count()
    otps = OTPToken.query.filter_by(user_id=user.id).count()
    consents = ConsentRecord.query.filter_by(user_id=user.id).count()
    data_reqs = DataRequest.query.filter_by(user_id=user.id).count()

    return {
        "user": user,
        "submissions": subs,
        "academy_count": academy,
        "otp_count": otps,
        "consent_count": consents,
        "data_request_count": data_reqs,
    }

@bp.route("/data-requests/<int:req_id>/preview")
@login_required
@admin_required
def data_request_preview(req_id):

    from models import User
    req_row = db.session.get(DataRequest, req_id)
    if req_row is None:
        abort(404)
    if req_row.status == "resolved":
        flash("This request is already resolved.", "info")
        return redirect(url_for("admin.data_requests"))

    rtype = req_row.request_type
    if rtype == "rectification":
        flash("Rectification requests must be handled manually.", "info")
        return redirect(url_for("admin.data_requests"))

    user = User.query.filter_by(email=req_row.email).first()
    app_name = current_app.config.get("APP_NAME", "prompt-a-thon")

    ctx = {
        "req": req_row,
        "user": user,
        "rtype": rtype,
        "app_name": app_name,
    }

    if rtype == "access":
        if user:
            data = _collect_user_data(user)
            subject, text_body, _ = _build_access_email_body(data, app_name)
            ctx["email_subject"] = subject
            ctx["email_body"] = text_body
        else:
            ctx["no_account"] = True

    elif rtype == "erasure":
        if user:
            ctx["erasure_info"] = _describe_erasure(user)
        else:
            ctx["no_account"] = True

    elif rtype == "portability":
        if user:
            data = _collect_user_data(user)
            csv_text = _build_portability_csv(data)
            ctx["csv_text"] = csv_text
            import urllib.parse as _up
            app_name_q = _up.quote(app_name)
            email_q = _up.quote(req_row.email)
            subject_q = _up.quote(f"{app_name}: Your data export (Portability Request)")
            body_q = _up.quote(
                f"Dear data subject,\n\nPlease find your data export attached as a CSV file.\n\n"
                f"This was generated in response to your portability request.\n\n"
                f"If you have questions, please reply to this email.\n\n{app_name} Team"
            )
            ctx["mailto_link"] = f"mailto:{email_q}?subject={subject_q}&body={body_q}"
        else:
            ctx["no_account"] = True

    return render_template("admin/gdpr_preview.html", **ctx)

@bp.route("/data-requests/<int:req_id>/auto-resolve", methods=["POST"])
@login_required
@admin_required
def data_request_auto_resolve(req_id):

    from models import User
    req_row = db.session.get(DataRequest, req_id)
    if req_row is None:
        abort(404)
    if req_row.status == "resolved":
        flash("This request is already resolved.", "info")
        return redirect(url_for("admin.data_requests"))

    rtype = req_row.request_type
    user = User.query.filter_by(email=req_row.email).first()
    app_name = current_app.config.get("APP_NAME", "prompt-a-thon")
    mail_cfg = _mail_cfg()

    if rtype == "access":
        if user:
            data = _collect_user_data(user)
            subject, text_body, html_body = _build_access_email_body(
                data, app_name,
                base_url=mail_cfg.get("base_url", ""),
                accent=mail_cfg.get("accent", _DEFAULT_ACCENT),
            )
            from services.email import send_notification_email
            ok, err = send_notification_email(
                mail_cfg, req_row.email, subject, html_body, text_body,
                category="gdpr",
            )
            if not ok:
                flash(f"Email failed to send: {err}. Please send manually.", "error")
                return redirect(url_for("admin.data_request_preview", req_id=req_id))
            flash(f"Access data emailed to {req_row.email}.", "success")
        else:
            flash("No account found for this email address; request marked resolved.", "info")

    elif rtype == "erasure":
        action = request.form.get("confirm_erasure")
        if action != "confirmed":
            flash("Erasure not confirmed — no data was deleted.", "warning")
            return redirect(url_for("admin.data_request_preview", req_id=req_id))
        if user:

            db.session.delete(user)
            flash(f"User record and all associated data for {req_row.email} has been permanently deleted.", "success")
        else:
            flash("No account found for this email; request marked resolved.", "info")

    elif rtype == "portability":

        flash(
            "Portability request marked resolved. Ensure you have sent the CSV to the data subject.",
            "success",
        )

    elif rtype == "rectification":
        flash("Rectification requests must be handled manually.", "info")
        return redirect(url_for("admin.data_requests"))

    req_row.status = "resolved"
    from models import utcnow
    req_row.resolved_at = utcnow()
    req_row.resolved_by = current_user.id
    write_audit(
        "gdpr.request_auto_resolved",
        actor=current_user,
        target_type="data_request",
        target_id=req_id,
        detail=f"type={rtype}",
        ip=_client_ip(),
    )
    db.session.commit()
    return redirect(url_for("admin.data_requests"))

_ACCENT_PRESETS = {
    "blue":   {"--primary": "#2563eb", "--primary-dark": "#1d4ed8", "--primary-light": "#dbeafe"},
    "violet": {"--primary": "#7c3aed", "--primary-dark": "#6d28d9", "--primary-light": "#ede9fe"},
    "rose":   {"--primary": "#e11d48", "--primary-dark": "#be123c", "--primary-light": "#ffe4e6"},
    "teal":   {"--primary": "#0d9488", "--primary-dark": "#0f766e", "--primary-light": "#ccfbf1"},
    "orange": {"--primary": "#f27d00", "--primary-dark": "#c86200", "--primary-light": "#fff0e0"},
    "green":  {"--primary": "#16a34a", "--primary-dark": "#15803d", "--primary-light": "#dcfce7"},
}

_LEGACY_ACCENT_HEX = {k: v["--primary"] for k, v in _ACCENT_PRESETS.items()}

_DEFAULT_ACCENT = "#f27d00"

def _derive_accent_vars(hex_color: str) -> dict:

    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "f27d00"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    dr = max(0, int(r * 0.80))
    dg = max(0, int(g * 0.80))
    db = max(0, int(b * 0.80))
    lr = min(255, int(r * 0.14 + 255 * 0.86))
    lg = min(255, int(g * 0.14 + 255 * 0.86))
    lb = min(255, int(b * 0.14 + 255 * 0.86))
    return {
        "--primary":       hex_color,
        "--primary-dark":  f"#{dr:02x}{dg:02x}{db:02x}",
        "--primary-light": f"#{lr:02x}{lg:02x}{lb:02x}",
    }

_DARK_VARS = {
    "--bg": "#0f172a",
    "--bg-soft": "#1e293b",
    "--bg-softer": "#334155",
    "--border": "#334155",
    "--border-strong": "#475569",
    "--text": "#f1f5f9",
    "--text-muted": "#94a3b8",
    "--text-light": "#64748b",
    "--shadow-sm": "0 1px 2px rgba(0,0,0,0.4)",
    "--shadow": "0 2px 8px rgba(0,0,0,0.5)",
    "--shadow-lg": "0 12px 32px rgba(0,0,0,0.6)",
}

_LIGHT_VARS = {
    "--bg": "#ffffff",
    "--bg-soft": "#f8fafc",
    "--bg-softer": "#f1f5f9",
    "--border": "#e2e8f0",
    "--border-strong": "#cbd5e1",
    "--text": "#0f172a",
    "--text-muted": "#64748b",
    "--text-light": "#94a3b8",
    "--shadow-sm": "0 1px 2px rgba(15, 23, 42, 0.06)",
    "--shadow": "0 2px 8px rgba(15, 23, 42, 0.08)",
    "--shadow-lg": "0 12px 32px rgba(15, 23, 42, 0.12)",
}

_ANIMATION_PRESETS = {
    "none":  {
        "reveal": ".reveal { opacity: 1; transform: none; transition: none; }\n.reveal.visible { opacity: 1; transform: none; }",
    },
    "fade":  {
        "reveal": ".reveal {\n  opacity: 0;\n  transition: opacity 500ms ease-out;\n}\n.reveal.visible {\n  opacity: 1;\n  transform: none;\n}",
    },
    "rise":  {
        "reveal": ".reveal {\n  opacity: 0;\n  transform: translateY(12px);\n  transition: opacity 500ms ease-out, transform 500ms ease-out;\n}\n.reveal.visible {\n  opacity: 1;\n  transform: none;\n}",
    },
    "scale": {
        "reveal": ".reveal {\n  opacity: 0;\n  transform: scale(0.96);\n  transition: opacity 400ms ease-out, transform 400ms ease-out;\n}\n.reveal.visible {\n  opacity: 1;\n  transform: none;\n}",
    },
}

def _build_theme_css(mode, accent, animation):

    lines = [":root {"]
    if accent.startswith("#"):
        accent_vars = _derive_accent_vars(accent)
    else:
        accent_vars = _ACCENT_PRESETS.get(accent, _ACCENT_PRESETS["orange"])
    for k, v in accent_vars.items():
        lines.append(f"  {k}: {v};")
    bg_vars = _DARK_VARS if mode == "dark" else _LIGHT_VARS
    for k, v in bg_vars.items():
        lines.append(f"  {k}: {v};")
    lines.append("}")
    anim = _ANIMATION_PRESETS.get(animation, _ANIMATION_PRESETS["rise"])["reveal"]
    lines.append(anim)
    return "\n".join(lines)

def _font_display_name(filename: str) -> str:

    import re as _re
    stem = filename.rsplit(".", 1)[0]

    spaced = _re.sub(r'([a-z])([A-Z])', r'\1 \2', stem)

    spaced = spaced.replace("-", " ").replace("_", " ")
    return spaced.strip()

def _rewrite_main_css(css_path, mode, accent, animation, font_filename=None):

    import re as _re
    try:
        original = css_path.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"Could not read main.css: {e}"

    css = original
    if font_filename:
        font_name = _font_display_name(font_filename)

        _fmt = 'opentype' if font_filename.lower().endswith('.otf') else 'truetype'
        _src = f'url("../fonts/{font_filename}") format("{_fmt}")'
        new_font_face = (
            f'@font-face {{\n'
            f'  font-family: "{font_name}";\n'
            f'  src: {_src};\n'
            f'  font-weight: 400;\n'
            f'  font-style: normal;\n'
            f'  font-display: swap;\n'
            f'}}\n'
            f'@font-face {{\n'
            f'  font-family: "{font_name}";\n'
            f'  src: {_src};\n'
            f'  font-weight: 700;\n'
            f'  font-style: normal;\n'
            f'  font-display: swap;\n'
            f'}}'
        )

        face_pattern = _re.compile(
            r'@font-face\s*\{[^}]+\}(\s*@font-face\s*\{[^}]+\})?',
            _re.DOTALL
        )
        if face_pattern.search(css):
            css = face_pattern.sub(new_font_face, css, count=1)
        else:
            css = new_font_face + "\n\n" + css
    else:

        face_match = _re.search(r'font-family:\s*"([^"]+)"', css)
        font_name = face_match.group(1) if face_match else "Roboto Mono"

    accent_vars = _derive_accent_vars(accent) if accent.startswith("#") else _ACCENT_PRESETS.get(accent, _ACCENT_PRESETS["orange"])
    bg_vars = _DARK_VARS if mode == "dark" else _LIGHT_VARS

    root_match = _re.search(r':root\s*\{([^}]+)\}', css)
    if not root_match:
        return False, "Could not locate :root block in main.css."

    existing_vars = {}
    for line in root_match.group(1).splitlines():
        m = _re.match(r'\s*(--[\w-]+)\s*:\s*(.+?);', line)
        if m:
            existing_vars[m.group(1)] = m.group(2).strip()

    font_fallback = f'"{font_name}", ui-sans-serif, system-ui, sans-serif'

    merged = dict(existing_vars)
    merged.update(accent_vars)
    merged.update(bg_vars)
    merged["--font-sans"] = font_fallback
    merged["--font-mono"] = font_fallback

    def replace_var(line):
        m = _re.match(r'(\s*)(--[\w-]+)(\s*:\s*)(.+?)(;.*)', line)
        if m and m.group(2) in merged:
            return f"{m.group(1)}{m.group(2)}{m.group(3)}{merged[m.group(2)]}{m.group(5)}"
        return line

    new_root_inner = "\n".join(replace_var(l) for l in root_match.group(1).splitlines())
    new_root_block = f":root {{{new_root_inner}}}"
    css = css[:root_match.start()] + new_root_block + css[root_match.end():]

    anim_css = _ANIMATION_PRESETS.get(animation, _ANIMATION_PRESETS["rise"])["reveal"]
    reduced_motion = (
        "@media (prefers-reduced-motion: reduce) {\n"
        "  .reveal { opacity: 1; transform: none; transition: none; }\n"
        "  * { transition: none !important; animation: none !important; }\n"
        "}"
    )
    new_reveal_section = (
        "/* ---------- Reveal animation ---------- */\n"
        + anim_css + "\n"
        + reduced_motion
    )
    reveal_pattern = _re.compile(
        r'/\* -{3,} Reveal animation -{3,} \*/.*?(?=\n/\* |$)',
        _re.DOTALL
    )
    if reveal_pattern.search(css):
        css = reveal_pattern.sub(new_reveal_section, css)
    else:
        css += "\n" + new_reveal_section

    try:
        css_path.write_text(css, encoding="utf-8")
    except OSError as e:
        return False, f"Could not write main.css: {e}"

    try:
        written = css_path.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"Write verification failed (could not re-read): {e}"

    for var, val in accent_vars.items():
        if val not in written:
            return False, f"Verification failed: {var} not found in saved file."
    if ".reveal" not in written:
        return False, "Verification failed: .reveal block missing from saved file."
    if font_filename and font_filename not in written:
        return False, f"Verification failed: font filename '{font_filename}' not in saved file."

    return True, None

@bp.route("/config/style", methods=["GET", "POST"])
@login_required
@admin_required
def config_style():
    from pathlib import Path as _Path
    css_path = _Path(current_app.root_path) / "static" / "css" / "main.css"
    fonts_dir = _Path(current_app.root_path) / "static" / "fonts"

    if request.method == "POST":
        mode = request.form.get("mode") or "light"
        accent = (request.form.get("accent") or _DEFAULT_ACCENT).strip()
        animation = request.form.get("animation") or "rise"
        if mode not in ("light", "dark"):
            mode = "light"
        if not re.match(r'^#[0-9a-fA-F]{6}$', accent):
            accent = _DEFAULT_ACCENT
        if animation not in _ANIMATION_PRESETS:
            animation = "rise"

        active_font = SiteSettings.get("theme_font", "RobotoMono.ttf")
        ok, err = _rewrite_main_css(css_path, mode, accent, animation, active_font)
        if not ok:
            flash(f"Failed to save theme: {err}", "error")
            return redirect(url_for("admin.config_style"))

        SiteSettings.set("theme_mode", mode)
        SiteSettings.set("theme_accent", accent)
        SiteSettings.set("theme_animation", animation)
        write_audit("config.theme_updated", actor=current_user,
                    detail=f"{mode}/{accent}/{animation}", ip=_client_ip())
        db.session.commit()
        flash("Theme saved.", "success")
        return redirect(url_for("admin.config_style"))

    mode = SiteSettings.get("theme_mode", "light")
    accent = SiteSettings.get("theme_accent", _DEFAULT_ACCENT)

    accent = _LEGACY_ACCENT_HEX.get(accent, accent)
    if not re.match(r'^#[0-9a-fA-F]{6}$', accent):
        accent = _DEFAULT_ACCENT
    animation = SiteSettings.get("theme_animation", "rise")
    active_font = SiteSettings.get("theme_font", "RobotoMono.ttf")

    fonts = sorted(
        [f.name for f in fonts_dir.iterdir() if f.suffix.lower() in (".ttf", ".otf")],
        key=str.lower,
    ) if fonts_dir.exists() else []

    return render_template(
        "admin/style_config.html",
        mode=mode,
        accent=accent,
        animation=animation,
        active_font=active_font,
        fonts=fonts,
        animation_presets=_ANIMATION_PRESETS,
    )

@bp.route("/config/style/reset", methods=["POST"])
@login_required
@admin_required
def config_style_reset():
    from pathlib import Path as _Path
    css_path = _Path(current_app.root_path) / "static" / "css" / "main.css"
    mode, accent, animation, font = "light", _DEFAULT_ACCENT, "rise", "RobotoMono.ttf"
    ok, err = _rewrite_main_css(css_path, mode, accent, animation, font)
    if not ok:
        flash(f"Reset failed: {err}", "error")
        return redirect(url_for("admin.config_style"))
    SiteSettings.set("theme_mode", mode)
    SiteSettings.set("theme_accent", accent)
    SiteSettings.set("theme_animation", animation)
    SiteSettings.set("theme_font", font)
    write_audit("config.theme_reset", actor=current_user, ip=_client_ip())
    db.session.commit()
    flash("Theme restored to defaults.", "success")
    return redirect(url_for("admin.config_style"))

import os as _os
from pathlib import Path as _ViewPath

def _contained_upload(upload_dir, file_path_str: str):

    from werkzeug.utils import secure_filename as _sf
    if not file_path_str:
        return None
    safe = _sf(file_path_str)
    if not safe or safe != file_path_str:
        return None
    root = upload_dir.resolve()
    target = (root / safe).resolve()
    if not target.is_relative_to(root):
        return None
    return target

def _resolve_upload_path(file_path_str: str):

    upload_dir = _ViewPath(current_app.config["UPLOAD_FOLDER"])
    target = _contained_upload(upload_dir, file_path_str)
    if target is None:
        if file_path_str:
            current_app.logger.warning("view_file: suspicious path rejected: %r", file_path_str)
        return None
    return target if target.is_file() else None

def _pdf_response(target):

    from flask import send_file
    if not target.is_file():
        abort(404)
    if target.stat().st_size > 100 * 1024 * 1024:
        abort(413)
    resp = send_file(
        target,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=target.name,
        conditional=True,
        max_age=0,
    )
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'none';"
        " object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    )
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store, no-transform"
    return resp

@bp.route("/submissions/<anon_id>/view-file")
@login_required
@judge_required
def view_file(anon_id):
    sub = Submission.query.filter_by(anonymous_id=anon_id).first_or_404()
    if not sub.file_path:
        abort(404)
    target = _resolve_upload_path(sub.file_path)
    if target is None:
        abort(404)
    return _pdf_response(target)

@bp.route("/files/<int:file_id>/view")
@login_required
@judge_required
def view_extra_file(file_id):
    from models import SubmissionFile as _SF
    sf = db.session.get(_SF, file_id)
    if sf is None:
        abort(404)
    target = _resolve_upload_path(sf.file_path)
    if target is None:
        abort(404)
    return _pdf_response(target)

_CRITERIA_PATH_REL = "config/criteria.json"

def _load_criteria_raw() -> str:
    from pathlib import Path as _P
    p = _P(current_app.root_path) / _CRITERIA_PATH_REL
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return "{}"
    return "{}"

def _save_criteria(data: dict) -> None:
    import tempfile as _tmp
    from pathlib import Path as _P
    p = _P(current_app.root_path) / _CRITERIA_PATH_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    with _tmp.NamedTemporaryFile("w", dir=str(p.parent), delete=False,
                                 suffix=".json", encoding="utf-8") as f:
        import json as _json
        _json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path = f.name
    import os as _os
    _os.replace(tmp_path, str(p))

@bp.route("/criteria", methods=["GET", "POST"])
@login_required
@admin_required
def criteria_config():
    error = None
    raw_text = _load_criteria_raw()
    if request.method == "POST":
        raw_text = request.form.get("config") or ""
        try:
            data = json.loads(raw_text)
            if not isinstance(data.get("criteria"), list):
                raise ValueError('JSON must have a "criteria" array.')
            _save_criteria(data)
            write_audit("config.criteria_updated", actor=current_user, ip=_client_ip())
            db.session.commit()
            flash("Mark scheme saved.", "success")
            return redirect(url_for("admin.criteria_config"))
        except json.JSONDecodeError as e:
            error = f"JSON parse error: {e}"
        except ValueError as e:
            error = str(e)
    return render_template("admin/criteria_config.html", config_text=raw_text, error=error)

@bp.route("/criteria/view")
@login_required
@judge_required
def criteria_view():
    import json as _json
    raw = _load_criteria_raw()
    try:
        data = _json.loads(raw)
    except Exception:
        data = {"title": "Mark Scheme", "sections": []}
    return render_template("admin/criteria_view.html", criteria=data)

_CONTENT_DEFAULTS = {

    "home_about_heading":   "What is prompt-a-thon?",
    "home_about_body":      "prompt-a-thon is a competition that puts AI literacy front and centre. You'll learn how AI systems work, practise giving them clear instructions, and tackle a task that lets you show what you've learned.\n\nYou don't need to be a coder. You don't need prior experience. The Academy will get you up to speed before you start.",
    "home_learn_heading":   "Learn",
    "home_learn_body":      "Work through short modules covering what AI is, how to prompt it well, and how to use it responsibly.",
    "home_compete_heading": "Compete",
    "home_compete_body":    "Pick one of three tasks at your difficulty level — from a community proposal to an ethics deep-dive.",
    "home_submit_heading":  "Submit",
    "home_submit_body":     "Hand in your work as text or a document. Judges review entries and award scores.",

    "academy_title":        "AI Academy",
    "academy_subtitle":     "Self-paced learning on prompt engineering, AI safety, and best practices.",
    "tasks_title":          "Tasks",
    "tasks_subtitle":       "Pick whichever brief interests you most. You'll choose your task on the Submit page when you're ready to upload your work.",
    "submissions_title":    "My submissions",
    "submissions_subtitle": "All work you have submitted, newest first.",
    "submit_title":         "Submit your work",
    "submit_subtitle":      "Choose the task, add your content, and attach any files below.",
}

_FAQ_DEFAULTS = [
    {"q": "Do I need any technical background?",
     "a": "No. The Academy is designed for complete beginners. If you can write a paragraph, you can take part."},
    {"q": "Can I work in a team?",
     "a": "This event is for individual submissions. You're welcome to discuss ideas with friends, but the work you submit must be your own."},
    {"q": "Can I use AI tools to help me?",
     "a": "Absolutely — that's the point. Be honest about what you used and how you used it."},
    {"q": "How does choosing a task work?",
     "a": "You don't pre-register for a task. When you're ready to upload your work, the Submit page lets you pick which brief your submission addresses, and you can submit for different tasks."},
    {"q": "How will my data be handled?",
     "a": "Your data is processed in line with our [privacy policy](/privacy). You can request access, export or deletion at any time via our [data request page](/gdpr/data-request)."},
]

def _get_content():
    import json
    raw = SiteSettings.get("page_content")
    if raw:
        try:
            stored = json.loads(raw)
            merged = dict(_CONTENT_DEFAULTS)
            merged.update(stored)
            return merged
        except Exception:
            pass
    return dict(_CONTENT_DEFAULTS)

def _get_faqs():
    import json
    raw = SiteSettings.get("faqs")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return list(_FAQ_DEFAULTS)

@bp.route("/content", methods=["GET", "POST"])
@login_required
@admin_required
def edit_content():
    import json
    content = _get_content()
    if request.method == "POST":
        updated = {}
        for key in _CONTENT_DEFAULTS:
            val = request.form.get(key, "").strip()
            updated[key] = val if val else _CONTENT_DEFAULTS[key]
        SiteSettings.set("page_content", json.dumps(updated))
        write_audit("content.edit_page_text", actor=current_user,
                    detail="Updated page content", ip=_client_ip())
        db.session.commit()
        flash("Page content saved.", "success")
        return redirect(url_for("admin.edit_content"))
    return render_template("admin/edit_content.html", content=content,
                           defaults=_CONTENT_DEFAULTS)

@bp.route("/faqs", methods=["GET", "POST"])
@login_required
@admin_required
def edit_faqs():
    import json
    faqs = _get_faqs()
    if request.method == "POST":
        questions = request.form.getlist("faq_q")
        answers   = request.form.getlist("faq_a")
        updated = [
            {"q": q.strip(), "a": a.strip()}
            for q, a in zip(questions, answers)
            if q.strip()
        ]
        if not updated:
            updated = list(_FAQ_DEFAULTS)
        SiteSettings.set("faqs", json.dumps(updated))
        write_audit("content.edit_faqs", actor=current_user,
                    detail=f"Updated {len(updated)} FAQs", ip=_client_ip())
        db.session.commit()
        flash("FAQs saved.", "success")
        return redirect(url_for("admin.edit_faqs"))
    return render_template("admin/edit_faqs.html", faqs=faqs)

_ISO_COUNTRIES = {
    "AF":"Afghanistan","AL":"Albania","DZ":"Algeria","AR":"Argentina","AM":"Armenia",
    "AU":"Australia","AT":"Austria","AZ":"Azerbaijan","BH":"Bahrain","BD":"Bangladesh",
    "BY":"Belarus","BE":"Belgium","BZ":"Belize","BR":"Brazil","BG":"Bulgaria",
    "CA":"Canada","CL":"Chile","CN":"China","CO":"Colombia","HR":"Croatia","CU":"Cuba",
    "CY":"Cyprus","CZ":"Czechia","DK":"Denmark","EG":"Egypt","EE":"Estonia","ET":"Ethiopia",
    "FI":"Finland","FR":"France","DE":"Germany","GH":"Ghana","GR":"Greece","GT":"Guatemala",
    "HU":"Hungary","IN":"India","ID":"Indonesia","IR":"Iran","IQ":"Iraq","IE":"Ireland",
    "IL":"Israel","IT":"Italy","JP":"Japan","JO":"Jordan","KZ":"Kazakhstan","KE":"Kenya",
    "KW":"Kuwait","LV":"Latvia","LB":"Lebanon","LY":"Libya","LT":"Lithuania","LU":"Luxembourg",
    "MY":"Malaysia","MT":"Malta","MX":"Mexico","MD":"Moldova","MA":"Morocco","MM":"Myanmar",
    "NP":"Nepal","NL":"Netherlands","NZ":"New Zealand","NG":"Nigeria","NO":"Norway",
    "PK":"Pakistan","PS":"Palestine","PA":"Panama","PE":"Peru","PH":"Philippines",
    "PL":"Poland","PT":"Portugal","QA":"Qatar","RO":"Romania","RU":"Russia",
    "SA":"Saudi Arabia","RS":"Serbia","SG":"Singapore","SK":"Slovakia","SI":"Slovenia",
    "ZA":"South Africa","KR":"South Korea","ES":"Spain","LK":"Sri Lanka","SE":"Sweden",
    "CH":"Switzerland","SY":"Syria","TW":"Taiwan","TH":"Thailand","TN":"Tunisia",
    "TR":"Turkey","UA":"Ukraine","AE":"United Arab Emirates","GB":"United Kingdom",
    "US":"United States","UY":"Uruguay","UZ":"Uzbekistan","VE":"Venezuela","VN":"Vietnam",
    "YE":"Yemen","ZM":"Zambia","ZW":"Zimbabwe",
}

@bp.route("/security")
@login_required
@admin_required
def security():
    from models import ConnectionEvent, SiteSettings
    from datetime import datetime, timedelta, timezone
    import json

    now     = datetime.now(timezone.utc).replace(tzinfo=None)
    since_24h = now - timedelta(hours=24)
    since_1h  = now - timedelta(hours=1)
    since_7d  = now - timedelta(days=7)

    total_24h   = ConnectionEvent.query.filter(ConnectionEvent.created_at >= since_24h).count()
    blocked_24h = ConnectionEvent.query.filter(
        ConnectionEvent.created_at >= since_24h, ConnectionEvent.is_blocked == True).count()
    unique_ips  = db.session.query(db.func.count(db.func.distinct(ConnectionEvent.ip)))        .filter(ConnectionEvent.created_at >= since_24h).scalar() or 0
    active_1h   = db.session.query(db.func.count(db.func.distinct(ConnectionEvent.ip)))        .filter(ConnectionEvent.created_at >= since_1h).scalar() or 0

    logins_ok   = AuditLog.query.filter(
        AuditLog.created_at >= since_24h, AuditLog.action == "auth.login_success").count()
    logins_fail = AuditLog.query.filter(
        AuditLog.created_at >= since_24h, AuditLog.action == "auth.login_failed").count()
    logins_block= AuditLog.query.filter(
        AuditLog.created_at >= since_24h,
        AuditLog.action.in_(["auth.login_denied_domain", "auth.login_blocked_banned"])).count()
    total_auth  = logins_ok + logins_fail + logins_block
    fail_pct    = round(100 * logins_fail / total_auth) if total_auth else 0

    top_fail_ips = db.session.query(
        AuditLog.ip_address, db.func.count(AuditLog.id).label("cnt")
    ).filter(
        AuditLog.created_at >= since_24h,
        AuditLog.action == "auth.login_failed"
    ).group_by(AuditLog.ip_address)     .order_by(db.func.count(AuditLog.id).desc())     .limit(5).all()

    top_countries = db.session.query(
        ConnectionEvent.country_code, db.func.count(ConnectionEvent.id).label("cnt")
    ).filter(
        ConnectionEvent.created_at >= since_24h,
        ConnectionEvent.country_code.isnot(None)
    ).group_by(ConnectionEvent.country_code)     .order_by(db.func.count(ConnectionEvent.id).desc())     .limit(8).all()

    hourly = []
    for h in range(23, -1, -1):
        bucket_start = now - timedelta(hours=h+1)
        bucket_end   = now - timedelta(hours=h)
        n = ConnectionEvent.query.filter(
            ConnectionEvent.created_at >= bucket_start,
            ConnectionEvent.created_at < bucket_end
        ).count()
        hourly.append(n)

    danger_files = AuditLog.query.filter(
        AuditLog.created_at >= since_7d, AuditLog.action == "file.danger_file").count()
    clean_files  = AuditLog.query.filter(
        AuditLog.created_at >= since_7d, AuditLog.action == "file.clean_file").count()

    recent = ConnectionEvent.query.order_by(
        ConnectionEvent.created_at.desc()).limit(100).all()

    restrict_mode    = SiteSettings.get("country_restrict_mode", "off")
    restrict_list_raw= SiteSettings.get("country_restrict_list", "[]")
    try:
        restrict_list = json.loads(restrict_list_raw)
    except Exception:
        restrict_list = []
    blocked_ips_raw  = SiteSettings.get("blocked_ips", "[]")
    try:
        blocked_ips = json.loads(blocked_ips_raw)
    except Exception:
        blocked_ips = []
    lockdown = SiteSettings.lockdown_enabled()
    block_message = SiteSettings.get("country_block_message", "") or ""

    return render_template(
        "admin/security.html",
        total_24h=total_24h, blocked_24h=blocked_24h,
        unique_ips=unique_ips, active_1h=active_1h,
        logins_ok=logins_ok, logins_fail=logins_fail,
        logins_block=logins_block, fail_pct=fail_pct,
        top_fail_ips=top_fail_ips,
        top_countries=top_countries,
        hourly_json=json.dumps(hourly),
        danger_files=danger_files, clean_files=clean_files,
        recent=recent,
        block_message=block_message,
        restrict_mode=restrict_mode, restrict_list=restrict_list,
        blocked_ips=blocked_ips, lockdown=lockdown,
        iso_countries=_ISO_COUNTRIES,
        settings_url=url_for("admin.settings"),
    )

@bp.route("/security/map-data")
@login_required
@admin_required
@limiter.exempt
def security_map_data():

    from models import ConnectionEvent
    from datetime import datetime, timedelta, timezone
    import json
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    rows = db.session.query(
        ConnectionEvent.country_code,
        db.func.count(ConnectionEvent.id).label("cnt")
    ).filter(
        ConnectionEvent.created_at >= since,
        ConnectionEvent.country_code.isnot(None)
    ).group_by(ConnectionEvent.country_code).all()
    return {"countries": {r.country_code: r.cnt for r in rows}}

@bp.route("/security/live-log")
@login_required
@admin_required
@limiter.exempt
def security_live_log():

    from models import ConnectionEvent
    import json
    events = ConnectionEvent.query.order_by(
        ConnectionEvent.created_at.desc()).limit(50).all()

    user_ids = {e.user_id for e in events if e.user_id}
    users = {}
    if user_ids:
        from models import User as _U
        for u in _U.query.filter(_U.id.in_(user_ids)).all():
            users[u.id] = u.email.split("@")[0] if "@" in u.email else u.email

    return {
        "events": [
            {
                "id": e.id,
                "ts": e.created_at.strftime("%H:%M:%S"),
                "ip": e.ip[:6] + "…" if len(e.ip) > 8 else e.ip,
                "cc": e.country_code or "—",
                "country": _ISO_COUNTRIES.get(e.country_code or "", e.country_code or "Unknown"),
                "user": users.get(e.user_id) if e.user_id else None,
                "method": e.method,
                "path": e.path,
                "blocked": e.is_blocked,
            }
            for e in events
        ]
    }

@bp.route("/security/settings", methods=["POST"])
@login_required
@admin_required
def security_settings():

    from models import SiteSettings
    import json

    action = request.form.get("action", "")

    if action == "country_restrict":
        mode = request.form.get("mode", "off")
        if mode not in ("off", "blocklist", "allowlist"):
            mode = "off"
        raw = request.form.get("countries", "")
        codes = [x.strip().upper() for x in raw.replace(",", " ").split() if len(x.strip()) == 2]

        if mode == "allowlist" and not codes:
            mode = "off"
            flash("Allowlist with no countries would block everyone — restriction set to Off.", "warning")
        custom_msg = request.form.get("block_message", "").strip()[:300]
        SiteSettings.set("country_restrict_mode", mode)
        SiteSettings.set("country_restrict_list", json.dumps(codes))
        SiteSettings.set("country_block_message", custom_msg)
        write_audit("security.country_restrict",
                    actor=current_user, ip=_client_ip(),
                    detail=f"mode={mode} countries={codes}")
        db.session.commit()
        flash(f"Country restriction updated: {mode}, {len(codes)} country code(s).", "success")

    elif action == "block_ip":
        ip = request.form.get("ip", "").strip()
        raw = SiteSettings.get("blocked_ips", "[]")
        try:
            ips = json.loads(raw)
        except Exception:
            ips = []
        if ip and ip not in ips:
            ips.append(ip)
            SiteSettings.set("blocked_ips", json.dumps(ips))
            write_audit("security.block_ip", actor=current_user,
                        ip=_client_ip(), detail=f"blocked {ip}")
            db.session.commit()
            flash(f"IP {ip} blocked.", "success")

    elif action == "unblock_ip":
        ip = request.form.get("ip", "").strip()
        raw = SiteSettings.get("blocked_ips", "[]")
        try:
            ips = json.loads(raw)
        except Exception:
            ips = []
        if ip in ips:
            ips.remove(ip)
            SiteSettings.set("blocked_ips", json.dumps(ips))
            write_audit("security.unblock_ip", actor=current_user,
                        ip=_client_ip(), detail=f"unblocked {ip}")
            db.session.commit()
            flash(f"IP {ip} unblocked.", "success")

    elif action == "lockdown":
        enabled = request.form.get("enabled") == "1"
        SiteSettings.set("lockdown_enabled", "1" if enabled else "0")
        write_audit("security.lockdown",
                    actor=current_user, ip=_client_ip(),
                    detail=f"{'enabled' if enabled else 'disabled'}")
        db.session.commit()
        flash(f"Emergency lockdown {'ENABLED' if enabled else 'disabled'}.", "success")

    elif action == "clear_log":
        from models import ConnectionEvent
        deleted = ConnectionEvent.query.delete()
        write_audit("security.clear_log", actor=current_user,
                    ip=_client_ip(), detail=f"deleted {deleted} events")
        db.session.commit()
        flash(f"Cleared {deleted} connection log entries.", "success")

    return redirect(url_for("admin.security"))

def _dir_size(path) -> int:

    from pathlib import Path as _P
    total = 0
    try:
        for f in _P(path).rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total

def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

@bp.route("/storage")
@login_required
@admin_required
def storage():
    from pathlib import Path as _P
    upload_dir = _P(current_app.config["UPLOAD_FOLDER"])
    cache_dir  = upload_dir.parent / "viewer_cache"

    sub_count  = Submission.query.filter(Submission.file_path.isnot(None)).count()
    sub_bytes  = _dir_size(upload_dir)

    from models_gdpr import DataRequest as _DR
    dr_count   = _DR.query.count()
    dr_pending = _DR.query.filter(_DR.status == "pending").count()

    cache_bytes = _dir_size(cache_dir)

    from services.file_pipeline import CONVERSION_DIR as _CONV_DIR
    conv_bytes = _dir_size(_CONV_DIR)

    submission_record_count = Submission.query.count()

    from models import EmailLog
    email_log_count = db.session.query(func.count(EmailLog.id)).scalar() or 0

    return render_template(
        "admin/storage.html",
        sub_count=sub_count,
        sub_bytes=_fmt_bytes(sub_bytes),
        sub_bytes_raw=sub_bytes,
        submission_record_count=submission_record_count,
        dr_count=dr_count,
        dr_pending=dr_pending,
        cache_bytes=_fmt_bytes(cache_bytes),
        cache_bytes_raw=cache_bytes,
        conv_bytes=_fmt_bytes(conv_bytes),
        conv_bytes_raw=conv_bytes,
        email_log_count=email_log_count,
    )

@bp.route("/storage/purge-submissions", methods=["POST"])
@login_required
@admin_required
def storage_purge_submissions():

    from pathlib import Path as _P
    upload_dir = _P(current_app.config["UPLOAD_FOLDER"])

    deleted_files  = 0
    deleted_bytes  = 0
    errors         = []

    subs = Submission.query.filter(Submission.file_path.isnot(None)).all()
    for sub in subs:
        target = _contained_upload(upload_dir, sub.file_path)
        if target is not None and target.is_file():
            try:
                size = target.stat().st_size
                target.unlink()
                deleted_files += 1
                deleted_bytes += size
            except FileNotFoundError:
                pass
            except OSError as e:
                errors.append(str(e))
        sub.file_path = None
        sub.filename  = None
        sub.file_size = None

    from models import SubmissionFile as _SF
    extra_files = _SF.query.all()
    for ef in extra_files:
        target = _contained_upload(upload_dir, ef.file_path)
        if target is not None and target.is_file():
            try:
                size = target.stat().st_size
                target.unlink()
                deleted_files += 1
                deleted_bytes += size
            except FileNotFoundError:
                pass
            except OSError as e:
                errors.append(str(e))
        db.session.delete(ef)

    write_audit(
        "storage.purge_submissions",
        actor=current_user,
        detail=(
            f"deleted {deleted_files} files, "
            f"{_fmt_bytes(deleted_bytes)}; "
            f"errors={len(errors)}"
        ),
        ip=_client_ip(),
    )
    db.session.commit()

    if errors:
        flash(f"Purged {deleted_files} files ({_fmt_bytes(deleted_bytes)}) with {len(errors)} error(s): {errors[0]}", "warning")
    else:
        flash(f"Purged {deleted_files} submission files ({_fmt_bytes(deleted_bytes)}).", "success")
    return redirect(url_for("admin.storage"))

@bp.route("/storage/purge-data-requests", methods=["POST"])
@login_required
@admin_required
def storage_purge_data_requests():

    from models_gdpr import DataRequest as _DR
    resolved = _DR.query.filter(_DR.status == "resolved").all()
    count = len(resolved)
    for dr in resolved:
        db.session.delete(dr)

    write_audit(
        "storage.purge_data_requests",
        actor=current_user,
        detail=f"deleted {count} resolved data request records",
        ip=_client_ip(),
    )
    db.session.commit()
    flash(f"Deleted {count} resolved data request record(s).", "success")
    return redirect(url_for("admin.storage"))

@bp.route("/storage/purge-viewer-cache", methods=["POST"])
@login_required
@admin_required
def storage_purge_viewer_cache():

    from pathlib import Path as _P
    from services.file_pipeline import purge_conversion_dir
    cache_dir = _P(current_app.config["UPLOAD_FOLDER"]).parent / "viewer_cache"
    deleted, total_bytes = 0, 0
    if cache_dir.exists():
        for f in cache_dir.rglob("*"):
            if f.is_file():
                try:
                    total_bytes += f.stat().st_size; f.unlink(); deleted += 1
                except OSError: pass
    conv_del, conv_bytes = purge_conversion_dir()
    deleted += conv_del; total_bytes += conv_bytes
    write_audit("storage.purge_viewer_cache", actor=current_user,
        detail=f"deleted {deleted} cache/temp files ({_fmt_bytes(total_bytes)})", ip=_client_ip())
    db.session.commit()
    flash(f"Cleared cache and conversion temps: {deleted} files ({_fmt_bytes(total_bytes)}).", "success")
    return redirect(url_for("admin.storage"))

@bp.route("/storage/purge-submission-records", methods=["POST"])
@login_required
@admin_required
def storage_purge_submission_records():

    from pathlib import Path as _P
    upload_dir = _P(current_app.config["UPLOAD_FOLDER"])

    count = Submission.query.count()
    deleted_files = 0

    all_subs = Submission.query.all()
    for sub in all_subs:
        for file_path in [sub.file_path] + [ef.file_path for ef in sub.extra_files]:
            target = _contained_upload(upload_dir, file_path)
            if target is not None and target.is_file():
                try:
                    target.unlink()
                    deleted_files += 1
                except OSError:
                    pass

    Submission.query.delete()

    write_audit(
        "storage.purge_submission_records",
        actor=current_user,
        detail=f"deleted {count} submission records and {deleted_files} files",
        ip=_client_ip(),
    )
    db.session.commit()
    flash(f"Deleted {count} submission record(s) and {deleted_files} file(s).", "success")
    return redirect(url_for("admin.storage"))

_TTF_MAGIC = {
    b'\x00\x01\x00\x00',
    b'true',
    b'OTTO',
    b'\x74\x74\x63\x66',
}
_MAX_FONT_BYTES = 8 * 1024 * 1024

def _validate_font(file_storage):

    data = file_storage.read(_MAX_FONT_BYTES + 1)
    if len(data) > _MAX_FONT_BYTES:
        return None, "Font file too large (max 8 MB)."
    if len(data) < 4:
        return None, "File too small to be a font."
    if data[:4] not in _TTF_MAGIC:
        return None, "File does not appear to be a valid TTF/OTF font (magic bytes mismatch)."
    return data, None

@bp.route("/fonts/upload", methods=["POST"])
@login_required
@admin_required
def font_upload():
    from pathlib import Path as _Path
    from werkzeug.utils import secure_filename as _sf
    fonts_dir = _Path(current_app.root_path) / "static" / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)

    f = request.files.get("font")
    if not f or not f.filename:
        flash("No file selected.", "error")
        return redirect(url_for("admin.config_style"))

    original = _sf(f.filename)
    if not original.lower().endswith((".ttf", ".otf")):
        flash("Only .ttf and .otf files are accepted.", "error")
        return redirect(url_for("admin.config_style"))

    data, err = _validate_font(f)
    if err:
        flash(err, "error")
        return redirect(url_for("admin.config_style"))

    dest = fonts_dir / original
    dest.write_bytes(data)
    write_audit("font.uploaded", actor=current_user, detail=original, ip=_client_ip())
    db.session.commit()
    flash(f"Font '{original}' uploaded.", "success")
    return redirect(url_for("admin.config_style"))

@bp.route("/fonts/select", methods=["POST"])
@login_required
@admin_required
def font_select():
    from pathlib import Path as _Path
    fonts_dir = _Path(current_app.root_path) / "static" / "fonts"
    css_path = _Path(current_app.root_path) / "static" / "css" / "main.css"

    from werkzeug.utils import secure_filename as _sf
    filename = request.form.get("filename") or ""
    safe_name = _sf(filename)
    fonts_root = fonts_dir.resolve()
    font_path = (fonts_root / safe_name).resolve() if safe_name else fonts_root
    if (
        not safe_name
        or safe_name != filename
        or not safe_name.lower().endswith(".ttf")
        or not font_path.is_file()
        or not font_path.is_relative_to(fonts_root)
    ):
        flash("Invalid font selection.", "error")
        return redirect(url_for("admin.config_style"))

    mode = SiteSettings.get("theme_mode", "light")
    accent = SiteSettings.get("theme_accent", _DEFAULT_ACCENT)
    animation = SiteSettings.get("theme_animation", "rise")

    ok, err = _rewrite_main_css(css_path, mode, accent, animation, filename)
    if not ok:
        flash(f"Failed to apply font: {err}", "error")
        return redirect(url_for("admin.config_style"))

    SiteSettings.set("theme_font", filename)
    write_audit("font.selected", actor=current_user, detail=filename, ip=_client_ip())
    db.session.commit()
    flash(f"Font changed to '{_font_display_name(filename)}'.", "success")
    return redirect(url_for("admin.config_style"))

import json as _json
import base64 as _base64
import io as _io
from PIL import Image as _Image

_ALLOWED_PIL_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "BMP", "TIFF"}
_MAX_UPLOAD_BYTES = 512 * 1024
_THUMB_SIZE = (256, 256)

def _process_image(file_storage) -> str:

    raw = file_storage.read(_MAX_UPLOAD_BYTES + 1)
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise ValueError("Image too large (max 512 KB).")
    try:
        img = _Image.open(_io.BytesIO(raw))
        img.verify()
    except Exception:
        raise ValueError("File is not a valid image.")

    img = _Image.open(_io.BytesIO(raw))
    if img.format not in _ALLOWED_PIL_FORMATS:
        raise ValueError(f"Image format \'{img.format}\' is not allowed.")

    if img.mode in ("RGBA", "LA", "PA"):
        bg = _Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail(_THUMB_SIZE, _Image.LANCZOS)
    out = _io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True)
    out.seek(0)
    return "data:image/jpeg;base64," + _base64.b64encode(out.read()).decode("ascii")

def _get_team():
    raw = SiteSettings.get("team_members", "[]")
    try:
        return _json.loads(raw)
    except Exception:
        return []

def _save_team(members):
    SiteSettings.set("team_members", _json.dumps(members, ensure_ascii=False))

@bp.route("/fonts/delete", methods=["POST"])
@login_required
@admin_required
def font_delete():
    from pathlib import Path as _Path
    fonts_dir = _Path(current_app.root_path) / "static" / "fonts"

    filename = request.form.get("filename") or ""
    if filename == "RobotoMono.ttf":
        flash("The default font (RobotoMono.ttf) cannot be deleted.", "error")
        return redirect(url_for("admin.config_style"))
    if not filename.endswith(".ttf"):
        flash("Invalid font filename.", "error")
        return redirect(url_for("admin.config_style"))

    font_path = fonts_dir / filename

    try:
        font_path.resolve().relative_to(fonts_dir.resolve())
    except ValueError:
        flash("Invalid font path.", "error")
        return redirect(url_for("admin.config_style"))

    if not font_path.exists():
        flash("Font not found.", "error")
        return redirect(url_for("admin.config_style"))

    active = SiteSettings.get("theme_font", "RobotoMono.ttf")
    if active == filename:
        from pathlib import Path as _P2
        css_path = _P2(current_app.root_path) / "static" / "css" / "main.css"
        mode = SiteSettings.get("theme_mode", "light")
        accent = SiteSettings.get("theme_accent", _DEFAULT_ACCENT)
        animation = SiteSettings.get("theme_animation", "rise")
        _rewrite_main_css(css_path, mode, accent, animation, "RobotoMono.ttf")
        SiteSettings.set("theme_font", "RobotoMono.ttf")

    font_path.unlink()
    write_audit("font.deleted", actor=current_user, detail=filename, ip=_client_ip())
    db.session.commit()
    flash(f"Font '{filename}' deleted.", "success")
    return redirect(url_for("admin.config_style"))

@bp.route("/staff")
@login_required
@admin_required
def staff():
    return render_template("admin/staff.html", members=_get_team())

@bp.route("/staff/add", methods=["POST"])
@login_required
@admin_required
def staff_add():
    name = (request.form.get("name") or "").strip()[:120]
    job = (request.form.get("job") or "").strip()[:200]
    category = request.form.get("category") or "staff"
    if category not in ("made_by", "staff"):
        category = "staff"
    image_b64 = None
    f = request.files.get("image")
    if f and f.filename:
        try:
            image_b64 = _process_image(f)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.staff"))
    members = _get_team()
    import secrets as _secrets
    member_id = _secrets.token_hex(8)
    members.append({"id": member_id, "name": name, "job": job,
                    "category": category, "image": image_b64})
    _save_team(members)
    write_audit("staff.added", actor=current_user, detail=name, ip=_client_ip())
    db.session.commit()
    flash(f"Added {name}.", "success")
    return redirect(url_for("admin.staff"))

@bp.route("/staff/<member_id>/edit", methods=["POST"])
@login_required
@admin_required
def staff_edit(member_id):
    if not member_id or not all(c in "0123456789abcdef" for c in member_id):
        abort(400)
    members = _get_team()
    m = next((x for x in members if x["id"] == member_id), None)
    if not m:
        abort(404)
    m["name"] = (request.form.get("name") or "").strip()[:120]
    m["job"] = (request.form.get("job") or "").strip()[:200]
    cat = request.form.get("category") or "staff"
    m["category"] = cat if cat in ("made_by", "staff") else "staff"
    f = request.files.get("image")
    if f and f.filename:
        try:
            m["image"] = _process_image(f)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("admin.staff"))
    _save_team(members)
    write_audit("staff.edited", actor=current_user, detail=m["name"], ip=_client_ip())
    db.session.commit()
    flash("Updated.", "success")
    return redirect(url_for("admin.staff"))

@bp.route("/staff/<member_id>/delete", methods=["POST"])
@login_required
@admin_required
def staff_delete(member_id):
    if not member_id or not all(c in "0123456789abcdef" for c in member_id):
        abort(400)
    members = [x for x in _get_team() if x["id"] != member_id]
    _save_team(members)
    write_audit("staff.deleted", actor=current_user, detail=member_id, ip=_client_ip())
    db.session.commit()
    flash("Removed.", "success")
    return redirect(url_for("admin.staff"))

@bp.route("/staff/reorder", methods=["POST"])
@login_required
@admin_required
def staff_reorder():
    order = request.form.getlist("order")
    order = [o for o in order if o and all(c in "0123456789abcdef" for c in o)]
    members = _get_team()
    id_map = {m["id"]: m for m in members}
    reordered = [id_map[i] for i in order if i in id_map]
    seen = set(order)
    reordered += [m for m in members if m["id"] not in seen]
    _save_team(reordered)
    db.session.commit()
    return ("", 204)

@bp.route("/email-config", methods=["GET", "POST"])
@login_required
@admin_required
def email_config():
    from models import EmailLog
    if request.method == "POST":
        tracking = "0" if not request.form.get("tracking_enabled") else "1"
        log_body  = "0" if not request.form.get("log_body")          else "1"
        retention = request.form.get("log_retention_days", "").strip()
        SiteSettings.set("email_tracking_enabled", tracking)
        SiteSettings.set("email_log_bodies", log_body)
        if retention.isdigit() and 1 <= int(retention) <= 3650:
            SiteSettings.set("email_log_retention_days", retention)
        elif retention == "0":
            SiteSettings.set("email_log_retention_days", "0")
        write_audit(
            "email.config_updated", actor=current_user,
            detail=f"tracking={tracking} log_body={log_body} retention={retention}",
            ip=_client_ip(),
        )
        db.session.commit()
        flash("Email settings saved.", "success")
        return redirect(url_for("admin.email_config"))

    total_logs = db.session.query(func.count(EmailLog.id)).scalar() or 0
    trackable  = db.session.query(func.count(EmailLog.id)).filter(
        EmailLog.tracking_pixel_id.isnot(None)
    ).scalar() or 0

    return render_template(
        "admin/email_config.html",
        tracking_enabled=SiteSettings.get("email_tracking_enabled", "1") != "0",
        log_body=SiteSettings.get("email_log_bodies", "1") != "0",
        log_retention_days=int(SiteSettings.get("email_log_retention_days", "90") or 90),
        total_logs=total_logs,
        trackable_logs=trackable,
    )

@bp.route("/email-analytics")
@login_required
@admin_required
def email_analytics():
    from models import EmailLog
    page = request.args.get("page", 1, type=int)
    category = request.args.get("category", "").strip()
    q = request.args.get("q", "").strip()

    query = EmailLog.query.order_by(EmailLog.sent_at.desc())
    if category:
        query = query.filter(EmailLog.category == category)
    if q:
        query = query.filter(EmailLog.to_email.ilike(f"%{q}%"))

    pagination = query.paginate(page=page, per_page=50, error_out=False)

    total = EmailLog.query.count()

    trackable_count = db.session.query(func.count(EmailLog.id)).filter(
        EmailLog.tracking_pixel_id.isnot(None)
    ).scalar() or 0
    opened_count = db.session.query(func.count(EmailLog.id)).filter(
        EmailLog.opened_at.isnot(None)
    ).scalar() or 0

    categories = [r[0] for r in db.session.query(EmailLog.category).distinct().all()]

    return render_template(
        "admin/email_analytics.html",
        logs=pagination.items,
        pagination=pagination,
        category=category,
        q=q,
        total=total,
        trackable_count=trackable_count,
        opened_count=opened_count,
        categories=sorted(categories),
    )

@bp.route("/email-analytics/<int:log_id>/detail")
@login_required
@admin_required
def email_analytics_detail(log_id):
    from models import EmailLog
    entry = db.session.get(EmailLog, log_id)
    if entry is None:
        abort(404)
    if entry.category == "otp":
        abort(403)
    return render_template("admin/email_detail.html", entry=entry)

@bp.route("/email-analytics/purge", methods=["POST"])
@login_required
@admin_required
def email_analytics_purge():
    from models import EmailLog
    count = db.session.query(func.count(EmailLog.id)).scalar() or 0
    EmailLog.query.delete(synchronize_session=False)
    write_audit(
        "storage.purge_email_logs",
        actor=current_user,
        detail=f"deleted {count} email log records",
        ip=_client_ip(),
    )
    db.session.commit()
    flash(f"Deleted {count} email log record(s).", "success")
    return redirect(url_for("admin.storage"))

import threading as _threading
import time as _time

def _get_mail_cfg_for_cert():
    from services.conversion_queue import _build_mail_cfg
    return _build_mail_cfg(current_app._get_current_object())

_CERT_RATE_DELAY = 30

def _do_send_certificates(app, cert_jobs, include_sorry_pdf: bool = False):

    import time as _t
    groups_order = ["first", "second", "third", "sorry"]
    with app.app_context():
        from services.email import send_certificate_email
        from services.conversion_queue import _build_mail_cfg
        from models import Certificate
        from extensions import db as _db
        mail_cfg = _build_mail_cfg(app)

        first_email = True
        for group in groups_order:
            group_jobs = [j for j in cert_jobs if j["award_type"] == group]
            for job in group_jobs:
                if not first_email:
                    _t.sleep(_CERT_RATE_DELAY)
                first_email = False

                if group == "sorry":
                    use_pdf = include_sorry_pdf
                else:
                    use_pdf = job.get("include_pdf", True)

                try:
                    ok, err, had_pdf = send_certificate_email(
                        mail_cfg,
                        to_email=job["email"],
                        recipient_name=job["name"],
                        competition_name=job["competition_name"],
                        school_name=job["school_name"],
                        award_type=job["award_type"],
                        sorry_message=job.get("sorry_message", ""),
                        include_pdf=use_pdf,
                    )
                    cert = Certificate(
                        recipient_email=job["email"],
                        recipient_name=job["name"] or None,
                        competition_name=job["competition_name"],
                        school_name=job["school_name"],
                        award_type=job["award_type"],
                        pdf_generated=had_pdf,
                    )
                    _db.session.add(cert)
                    _db.session.commit()
                except Exception as exc:
                    try:
                        _db.session.rollback()
                    except Exception:
                        pass
                    app.logger.error("Certificate send error for %s: %s", job.get("email"), exc)

def _cert_accent() -> str:

    accent = SiteSettings.get("theme_accent", _DEFAULT_ACCENT)
    accent = _LEGACY_ACCENT_HEX.get(accent, accent)
    if not re.match(r'^#[0-9a-fA-F]{6}$', accent):
        accent = _DEFAULT_ACCENT
    return accent

@bp.route("/certificates/preview-pdf")
@login_required
@admin_required
def certificates_preview_pdf():

    from flask import send_file
    from io import BytesIO
    from services.email import generate_certificate_pdf

    award_type = request.args.get("award_type", "first")
    if award_type not in ("first", "second", "third"):
        award_type = "first"

    comp = (request.args.get("competition_name") or
            SiteSettings.get("competition_name", "") or
            current_app.config.get("APP_NAME", "prompt-a-thon")).strip()[:200]
    school = (request.args.get("school_name") or
              SiteSettings.get("school_name", "") or
              current_app.config.get("COLLEGE_NAME", "Your College")).strip()[:200]
    name = (request.args.get("recipient_name") or "Jane Smith").strip()[:200]

    pdf_bytes = generate_certificate_pdf(
        recipient_name=name,
        recipient_email="preview@example.com",
        competition_name=comp,
        school_name=school,
        award_type=award_type,
        accent=_cert_accent(),
    )
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name="certificate_preview.pdf",
    )

@bp.route("/certificates")
@login_required
@admin_required
def certificates():
    from models import Certificate, Submission, User

    rows = (
        db.session.query(Submission, User)
        .join(User, User.id == Submission.user_id)
        .filter(Submission.status != "disqualified")
        .order_by(Submission.submitted_at.desc())
        .all()
    )
    history = Certificate.query.order_by(Certificate.sent_at.desc()).limit(200).all()

    competition_name = (
        SiteSettings.get("competition_name", "") or
        current_app.config.get("APP_NAME", "")
    )
    school_name = (
        SiteSettings.get("school_name", "") or
        current_app.config.get("COLLEGE_NAME", "")
    )
    return render_template(
        "admin/certificates.html",
        submissions=rows,
        history=history,
        competition_name=competition_name,
        school_name=school_name,
    )

@bp.route("/certificates/send", methods=["POST"])
@login_required
@admin_required
def certificates_send():
    from models import Submission, User
    competition_name = (request.form.get("competition_name") or "").strip()[:200]
    school_name = (request.form.get("school_name") or "").strip()[:200]
    include_pdf = request.form.get("include_pdf") == "1"
    include_sorry_pdf = request.form.get("include_sorry_pdf") == "1"
    sorry_message = (request.form.get("sorry_message") or "").strip()[:2000]

    if not competition_name or not school_name:
        flash("Competition name and school name are required.", "error")
        return redirect(url_for("admin.certificates"))

    SiteSettings.set("competition_name", competition_name)
    SiteSettings.set("school_name", school_name)
    db.session.commit()

    cert_jobs = []
    valid_types = {"first", "second", "third", "sorry"}
    for key, award_type in request.form.items():
        if not key.startswith("award_") or award_type not in valid_types:
            continue
        anon_id = key[len("award_"):]
        if not anon_id:
            continue
        sub = Submission.query.filter_by(anonymous_id=anon_id).first()
        if not sub:
            continue
        user = db.session.get(User, sub.user_id)
        if not user:
            continue
        name = (request.form.get(f"name_{anon_id}") or "").strip()[:200]
        cert_jobs.append({
            "email": user.email,
            "name": name,
            "award_type": award_type,
            "competition_name": competition_name,
            "school_name": school_name,
            "sorry_message": sorry_message,
            "include_pdf": include_pdf,
        })

    if not cert_jobs:
        flash("No recipients selected.", "error")
        return redirect(url_for("admin.certificates"))

    counts = {}
    for job in cert_jobs:
        counts[job["award_type"]] = counts.get(job["award_type"], 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in counts.items())

    write_audit(
        "certificates.send",
        actor=current_user,
        detail=f"competition={competition_name!r}; {summary}; rate=120/hr",
        ip=_client_ip(),
    )
    db.session.commit()

    app = current_app._get_current_object()
    t = _threading.Thread(
        target=_do_send_certificates,
        args=(app, cert_jobs),
        kwargs={"include_sorry_pdf": include_sorry_pdf},
        daemon=True,
    )
    t.start()

    total = len(cert_jobs)
    est_mins = max(1, round(total * _CERT_RATE_DELAY / 60))
    flash(
        f"Sending {total} result email(s) in the background ({summary}). "
        f"Estimated completion: ~{est_mins} min at ≤120/hr. "
        f"Check Email Analytics for delivery status.",
        "success",
    )
    return redirect(url_for("admin.certificates"))

@bp.route("/certificates/history/clear", methods=["POST"])
@login_required
@admin_required
def certificates_history_clear():
    from models import Certificate
    count = db.session.query(func.count(Certificate.id)).scalar() or 0
    Certificate.query.delete(synchronize_session=False)
    write_audit(
        "storage.purge_certificate_history",
        actor=current_user,
        detail=f"deleted {count} certificate records",
        ip=_client_ip(),
    )
    db.session.commit()
    flash(f"Cleared {count} certificate record(s) from history.", "success")
    return redirect(url_for("admin.certificates"))
