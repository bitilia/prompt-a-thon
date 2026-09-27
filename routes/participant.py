import re
import uuid
from pathlib import Path

import bleach
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, send_from_directory, url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from decorators import participant_required
from extensions import db, limiter
from models import (
    AcademyProgress, ROLE_JUDGE, ROLE_ADMIN, SiteSettings, Submission, SubmissionFile,
    TaskSelection, write_audit,
)
from services.academy import get_modules, total_item_count
from services.tasks import get_task, get_task_list

bp = Blueprint("participant", __name__, url_prefix="/dashboard")

ALLOWED_TAGS: list = []

def _client_ip() -> str:
    from middleware.security import client_ip
    return client_ip(request)

def _allowed_extension(filename: str) -> bool:
    if not filename:
        return False
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext in current_app.config["ALLOWED_UPLOAD_EXTENSIONS"]

def _sanitize_text(value: str | None, max_len: int) -> str:
    if not value:
        return ""
    cleaned = bleach.clean(value, tags=ALLOWED_TAGS, strip=True)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned[:max_len]

def _user_progress_set() -> set:
    return {
        ap.item_id
        for ap in AcademyProgress.query.filter_by(user_id=current_user.id, completed=True).all()
    }

@bp.route("/")
@login_required
@participant_required
def index():

    return redirect(url_for("participant.academy"))

@bp.route("/academy")
@login_required
@participant_required
def academy():
    modules = get_modules()
    progress = _user_progress_set()
    total = total_item_count()
    completed = len(progress & {
        f"{m['id']}:{i['id']}" for m in modules for i in (m.get("items") or [])
    })
    return render_template(
        "dashboard/academy.html",
        modules=modules,
        progress=progress,
        completed_count=completed,
        total_items=total,
    )

@bp.route("/tasks")
@login_required
@participant_required
def tasks():

    return render_template(
        "dashboard/tasks.html",
        tasks=get_task_list(),
        registration_open=SiteSettings.registration_is_open(),
        submission_open=SiteSettings.submission_is_open(),
    )

@bp.route("/submissions")
@login_required
@participant_required
def submissions():
    rows = (
        Submission.query.filter_by(user_id=current_user.id)
        .order_by(Submission.submitted_at.desc())
        .all()
    )
    task_titles = {t["id"]: t["title"] for t in get_task_list()}
    return render_template(
        "dashboard/submissions.html",
        submissions=rows,
        task_titles=task_titles,
    )

@bp.route("/submissions/<anon_id>/download")
@login_required
@participant_required
def download(anon_id):
    sub = Submission.query.filter_by(anonymous_id=anon_id).first_or_404()
    if sub is None:
        abort(404)

    if sub.user_id != current_user.id and current_user.role not in (ROLE_JUDGE, ROLE_ADMIN):
        abort(403)
    if not sub.file_path:
        abort(404)

    safe = secure_filename(sub.file_path)
    if safe != sub.file_path:
        current_app.logger.warning("Refusing suspicious file_path: %r", sub.file_path)
        abort(404)

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    target = (upload_dir / safe).resolve()
    if not target.is_file() or not target.is_relative_to(upload_dir):
        abort(404)

    download_name = sub.filename or safe
    return send_from_directory(
        str(upload_dir), safe, as_attachment=True, download_name=download_name,
    )

@bp.route("/submit", methods=["GET", "POST"])
@login_required
@participant_required
def submit():
    if not SiteSettings.submission_is_open():
        flash("The submission window is closed.", "warning")
        return redirect(url_for("participant.academy"))

    all_tasks = get_task_list()
    if not all_tasks:
        flash("No tasks are configured yet.", "warning")
        return redirect(url_for("participant.tasks"))

    selection = TaskSelection.query.filter_by(user_id=current_user.id).first()

    default_task_id = (selection.task_id if selection else None) or all_tasks[0]["id"]

    if request.method == "POST":
        task_id = (request.form.get("task_id") or "").strip()
        chosen = get_task(task_id)
        if not chosen:
            return jsonify({"ok": False, "error": "Please choose a valid task."}), 400

        title = _sanitize_text(request.form.get("title"), 200).strip()
        content = _sanitize_text(request.form.get("content"), 50000).strip()

        if not title:
            return jsonify({"ok": False, "error": "Please enter a title."}), 400

        upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
        max_bytes  = current_app.config["MAX_CONTENT_LENGTH"]

        import tempfile as _tf
        tmp_base = Path(current_app.config["UPLOAD_FOLDER"]).parent / "tmp"
        tmp_base.mkdir(exist_ok=True)

        files_to_process = []
        temp_paths = []
        try:
            for pos in range(1, 4):
                fobj = request.files.get(f"file{pos}")
                if not fobj or not fobj.filename:
                    continue
                original = secure_filename(fobj.filename)
                if not original or not _allowed_extension(original):
                    continue
                with _tf.NamedTemporaryFile(
                    dir=str(tmp_base), delete=False,
                    suffix=Path(original).suffix,
                ) as tf:
                    fobj.save(tf.name)
                    temp_path = Path(tf.name)
                    temp_paths.append(temp_path)
                if temp_path.stat().st_size > max_bytes:
                    return jsonify({
                        "ok": False,
                        "error": f"File {pos} exceeds the maximum upload size.",
                    }), 413
                files_to_process.append({
                    "temp_path": str(temp_path),
                    "original":  original,
                    "position":  pos,
                })
        except Exception as exc:
            for p in temp_paths:
                try: p.unlink(missing_ok=True)
                except Exception: pass
            current_app.logger.error("submit: file save error: %s", exc)
            return jsonify({"ok": False, "error": "File upload failed. Please try again."}), 500

        if not content and not files_to_process:
            return jsonify({
                "ok": False,
                "error": "Please provide written content or upload a file.",
            }), 400

        sub = Submission(
            user_id=current_user.id,
            task_id=task_id,
            title=title,
            content=content or None,
            status="processing" if files_to_process else "submitted",
            ip_address=_client_ip(),
        )
        db.session.add(sub)
        db.session.flush()
        sub.ensure_anonymous_id()

        if not files_to_process:

            if selection is None:
                selection = TaskSelection(
                    user_id=current_user.id, task_id=task_id, locked=True,
                )
                db.session.add(selection)
            else:
                selection.task_id = task_id
                selection.locked  = True
            write_audit("submission.created", actor=current_user,
                        target_type="submission", target_id=None, ip=_client_ip())
            db.session.commit()

            try:
                from services.email import send_submission_success_email
                _cfg = current_app.config
                mail_cfg = {
                    "provider":      _cfg.get("MAIL_PROVIDER", "smtp"),
                    "host":          _cfg.get("SMTP_HOST"),
                    "port":          _cfg.get("SMTP_PORT"),
                    "user":          _cfg.get("SMTP_USER"),
                    "password":      _cfg.get("SMTP_PASSWORD"),
                    "use_tls":       _cfg.get("SMTP_USE_TLS", True),
                    "brevo_api_key": _cfg.get("BREVO_API_KEY"),
                    "ses_access_key": _cfg.get("SES_ACCESS_KEY_ID"),
                    "ses_secret_key": _cfg.get("SES_SECRET_ACCESS_KEY"),
                    "ses_region":    _cfg.get("SES_REGION"),
                    "from_name":     _cfg.get("MAIL_FROM_NAME") or _cfg.get("SMTP_FROM_NAME"),
                    "from_address":  _cfg.get("MAIL_FROM_ADDRESS"),
                    "app_name":      _cfg.get("APP_NAME", "prompt-a-thon"),
                }
                app_name = _cfg.get("APP_NAME", "prompt-a-thon")
                ok, e_err = send_submission_success_email(
                    mail_cfg, current_user.email, app_name, sub.title or "",
                )
                if not ok:
                    current_app.logger.warning(
                        "submit: success email failed for sub %s: %s", sub.id, e_err
                    )
            except Exception as exc:
                current_app.logger.warning(
                    "submit: success email exception for sub %s: %s", sub.id, exc
                )

            return jsonify({
                "ok":      True,
                "async":   False,
                "redirect": url_for("participant.submission_success", anon_id=sub.anonymous_id),
            })

        from services.file_pipeline import _check_magic
        for fi in files_to_process:
            _ext = Path(fi["original"]).suffix.lower().lstrip(".")
            if not _check_magic(Path(fi["temp_path"]), _ext):
                for p in temp_paths:
                    try: p.unlink(missing_ok=True)
                    except Exception: pass
                return jsonify({"ok": False,
                                "error": "One or more files appear invalid or corrupted."}), 400

        db.session.commit()

        from services.conversion_queue import get_queue
        get_queue().submit(
            files=files_to_process,
            upload_dir=upload_dir,
            sub_id=sub.id,
            payload={
                "user_id": current_user.id,
                "task_id": task_id,
                "ip":      _client_ip(),
            },
            app=current_app._get_current_object(),
        )

        return jsonify({"ok": True, "async": False,
                        "redirect": url_for("participant.submission_success", anon_id=sub.anonymous_id)})

    return render_template(
        "dashboard/submit.html",
        tasks=all_tasks,
        selected_task_id=default_task_id,
        submission_open=True,
    )

@bp.route("/upload/status/<anon_id>")
@login_required
@participant_required
@limiter.limit("300 per hour; 60 per minute", override_defaults=True)
def upload_status(anon_id):

    sub = Submission.query.filter_by(anonymous_id=anon_id).first()
    if sub is None or sub.user_id != current_user.id:
        return jsonify({"status": "error", "message": "Not found."}), 404

    if sub.status == "submitted":
        return jsonify({
            "status":   "done",
            "redirect": url_for("participant.submission_success", anon_id=sub.anonymous_id),
        })

    if sub.status == "failed":
        from services.conversion_queue import get_queue
        msg = get_queue().get_error(sub.id) or (
            "Your file could not be processed. "
            "Please try again or contact the organisers."
        )
        return jsonify({"status": "error", "message": msg})

    return jsonify({"status": "running"})

@bp.route("/files/<int:file_id>/download")
@login_required
@participant_required
def download_extra_file(file_id):

    sf = db.session.get(SubmissionFile, file_id)
    if sf is None:
        abort(404)
    sub = db.session.get(Submission, sf.submission_id)
    if sub is None:
        abort(404)
    if sub.user_id != current_user.id and current_user.role not in (ROLE_JUDGE, ROLE_ADMIN):
        abort(403)
    safe = secure_filename(sf.file_path)
    if safe != sf.file_path:
        abort(404)
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    target = (upload_dir / safe).resolve()
    if not target.is_file() or not target.is_relative_to(upload_dir):
        abort(404)
    return send_from_directory(str(upload_dir), safe, as_attachment=True, download_name=sf.filename)

@bp.route("/convert-preview", methods=["POST"])
@login_required
def convert_preview():

    import base64, tempfile as _tf
    from services.file_pipeline import SOCKET_PATH, _check_magic, _request_conversion, PipelineError
    from pathlib import Path as _P

    fobj = request.files.get("file")
    if not fobj or not fobj.filename:
        return jsonify({"ok": False, "error": "No file provided."}), 400

    original = secure_filename(fobj.filename)
    if not original.lower().endswith(".docx"):
        return jsonify({"ok": False, "error": "Only DOCX files can be previewed."}), 400

    if not SOCKET_PATH.exists():
        return jsonify({
            "ok": False,
            "error": "The conversion service is not running. Please contact the event organisers."
        }), 503

    with _tf.TemporaryDirectory() as td:
        src = _P(td) / "input.docx"
        fobj.save(str(src))
        if not _check_magic(src, "docx"):
            return jsonify({"ok": False, "error": "File does not appear to be a valid DOCX."}), 400
        docx_bytes = src.read_bytes()

    try:
        pdf_bytes = _request_conversion(docx_bytes, timeout=90)
        pdf_b64 = base64.b64encode(pdf_bytes).decode()
        return jsonify({"ok": True, "pdf_b64": pdf_b64})
    except PipelineError as e:
        current_app.logger.warning("convert-preview: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        current_app.logger.error("convert-preview unexpected: %s", e)
        return jsonify({"ok": False, "error": "Conversion failed unexpectedly. Please try again."}), 500

@bp.route("/submissions/<anon_id>/success")
@login_required
@participant_required
def submission_success(anon_id):
    sub = Submission.query.filter_by(anonymous_id=anon_id).first()
    if sub is None or sub.user_id != current_user.id:
        abort(404)
    raw_md = SiteSettings.submission_success_message()
    return render_template(
        "dashboard/submit_success.html",
        submission=sub,
        message_md=raw_md,
    )

_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

def _academy_item_exists(module_id: str, item_id: str) -> bool:
    for m in get_modules():
        if m.get("id") == module_id:
            for i in (m.get("items") or []):
                if i.get("id") == item_id:
                    return True
            return False
    return False

@bp.route("/academy/<module_id>/<item_id>/complete", methods=["POST"])
@login_required
@participant_required
@limiter.limit("60 per minute")
def academy_complete(module_id, item_id):
    if not (_ID_RE.match(module_id or "") and _ID_RE.match(item_id or "")):
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    if not _academy_item_exists(module_id, item_id):
        return jsonify({"ok": False, "error": "not_found"}), 404

    full_id = f"{module_id}:{item_id}"
    existing = AcademyProgress.query.filter_by(
        user_id=current_user.id, item_id=full_id
    ).first()
    if existing is None:
        ap = AcademyProgress(user_id=current_user.id, item_id=full_id, completed=True)
        db.session.add(ap)
    else:
        existing.completed = True
    db.session.commit()
    return jsonify({"ok": True, "item_id": full_id, "completed_count": _completed_count()})

@bp.route("/academy/<module_id>/<item_id>/uncomplete", methods=["POST"])
@login_required
@participant_required
@limiter.limit("60 per minute")
def academy_uncomplete(module_id, item_id):
    if not (_ID_RE.match(module_id or "") and _ID_RE.match(item_id or "")):
        return jsonify({"ok": False, "error": "invalid_id"}), 400
    if not _academy_item_exists(module_id, item_id):
        return jsonify({"ok": False, "error": "not_found"}), 404

    full_id = f"{module_id}:{item_id}"
    existing = AcademyProgress.query.filter_by(
        user_id=current_user.id, item_id=full_id
    ).first()
    if existing is not None:
        db.session.delete(existing)
        db.session.commit()
    return jsonify({"ok": True, "item_id": full_id, "completed_count": _completed_count()})

def _completed_count() -> int:

    progress = _user_progress_set()
    valid_ids = {
        f"{m['id']}:{i['id']}"
        for m in get_modules()
        for i in (m.get("items") or [])
    }
    return len(progress & valid_ids)
