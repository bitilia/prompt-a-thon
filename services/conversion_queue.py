from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_WORKERS        = 4
TTL_SECONDS        = 600
POLL_TIMEOUT       = 180
FILE_PROC_TIMEOUT  = 240

_PENDING = "pending"
_RUNNING = "running"
_DONE    = "done"
_ERROR   = "error"

def _get_accent() -> str:
    try:
        from models import SiteSettings
        return SiteSettings.get("theme_accent", "#f27d00") or "#f27d00"
    except Exception:
        return "#f27d00"

def _build_mail_cfg(app) -> dict:
    cfg = app.config
    return {
        "provider":      cfg.get("MAIL_PROVIDER", "smtp"),
        "host":          cfg.get("SMTP_HOST"),
        "port":          cfg.get("SMTP_PORT"),
        "user":          cfg.get("SMTP_USER"),
        "password":      cfg.get("SMTP_PASSWORD"),
        "use_tls":       cfg.get("SMTP_USE_TLS", True),
        "brevo_api_key": cfg.get("BREVO_API_KEY"),
        "ses_access_key": cfg.get("SES_ACCESS_KEY_ID"),
        "ses_secret_key": cfg.get("SES_SECRET_ACCESS_KEY"),
        "ses_region":    cfg.get("SES_REGION"),
        "from_name":     cfg.get("MAIL_FROM_NAME") or cfg.get("SMTP_FROM_NAME"),
        "from_address":  cfg.get("MAIL_FROM_ADDRESS"),
        "app_name":      cfg.get("APP_NAME", "prompt-a-thon"),
        "accent":        _get_accent(),
        "base_url":      (cfg.get("BASE_URL") or "").rstrip("/"),
    }

@dataclass
class ConversionJob:
    job_id:      str
    status:      str             = _PENDING
    result_path: Optional[Path]  = None
    error:       Optional[str]   = None
    created_at:  float           = field(default_factory=time.monotonic)
    finished_at: float           = 0.0
    sub_id:      Optional[int]   = None

class ConversionQueue:
    def __init__(self, max_workers: int = MAX_WORKERS):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="convert",
        )
        self._jobs:       dict[str, ConversionJob] = {}
        self._sub_errors: dict[int, str]           = {}
        self._lock = threading.Lock()

    def submit(
        self,
        files: list,
        upload_dir: Path,
        sub_id: int,
        payload: dict,
        app,
    ) -> str:

        job_id = uuid.uuid4().hex
        job = ConversionJob(job_id=job_id, sub_id=sub_id)
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job, files, upload_dir, sub_id, payload, app)
        logger.info("queue: submitted job %s for sub %s (%d file(s))",
                    job_id, sub_id, len(files))
        return job_id

    def _run(self, job, files, upload_dir, sub_id, payload, app):
        from services.file_pipeline import process_upload, PipelineError
        job.status = _RUNNING

        processed   = []
        first_error = None

        for file_info in files:
            temp_path = Path(file_info["temp_path"])
            original  = file_info["original"]
            position  = file_info["position"]
            try:

                _file_ex = ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="file_proc")
                _fut = _file_ex.submit(
                    process_upload,
                    temp_path, original, upload_dir,
                    lambda action, detail="": None,
                )
                try:
                    stored_name, display_name, fsize = _fut.result(
                        timeout=FILE_PROC_TIMEOUT
                    )
                    processed.append((stored_name, display_name, fsize, position))
                    logger.info("queue: job %s file[%d] done → %s",
                                job.job_id, position, stored_name)
                except _FutureTimeout:
                    if first_error is None:
                        first_error = (
                            "File processing timed out. "
                            "Please try a smaller file or export as PDF from Word."
                        )
                    logger.error("queue: job %s file[%d] timed out after %ds",
                                 job.job_id, position, FILE_PROC_TIMEOUT)
                except PipelineError as exc:
                    if first_error is None:
                        first_error = str(exc)
                    logger.warning("queue: job %s file[%d] pipeline error: %s",
                                   job.job_id, position, exc)
                except Exception as exc:
                    if first_error is None:
                        first_error = ("Your file could not be processed. "
                                       "Please try again or contact the organisers.")
                    logger.error("queue: job %s file[%d] unexpected error: %s",
                                 job.job_id, position, exc)
                finally:

                    _file_ex.shutdown(wait=False)

            finally:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

        job.finished_at = time.monotonic()

        with app.app_context():
            try:
                from extensions import db
                from models import (
                    Submission, SubmissionFile, TaskSelection, User, write_audit,
                )

                sub = db.session.get(Submission, sub_id)
                if sub is None:
                    logger.error("queue: sub %s not found for job %s", sub_id, job.job_id)
                    job.status = _ERROR
                    job.error  = "Submission record missing."
                    return

                user_id = payload.get("user_id")
                task_id = payload.get("task_id", "")
                ip      = payload.get("ip", "")
                user    = db.session.get(User, user_id) if user_id else None

                _notify_type  = None
                _notify_error = None

                if not processed:
                    err = first_error or "File processing failed."
                    sub.status = "failed"
                    with self._lock:
                        self._sub_errors[sub_id] = err
                    job.status    = _ERROR
                    job.error     = err
                    _notify_type  = "failed"
                    _notify_error = err
                else:
                    if sub.status == "failed":

                        logger.info(
                            "queue: job %s sub %s terminated by admin; discarding %d file(s)",
                            job.job_id, sub_id, len(processed),
                        )
                        for (stored_name, _, _, _) in processed:
                            try: (upload_dir / stored_name).unlink(missing_ok=True)
                            except Exception: pass
                        job.status    = _ERROR
                        _notify_type  = "failed"
                        _notify_error = "Your submission was terminated by an administrator."
                    else:
                        for (stored_name, display_name, fsize, position) in processed:
                            if position == 1:
                                sub.file_path = stored_name
                                sub.filename  = display_name
                                sub.file_size = fsize
                            else:
                                db.session.add(SubmissionFile(
                                    submission_id=sub.id,
                                    position=position,
                                    filename=display_name,
                                    file_path=stored_name,
                                    file_size=fsize,
                                ))

                        sel = TaskSelection.query.filter_by(user_id=user_id).first()
                        if sel is None:
                            db.session.add(TaskSelection(
                                user_id=user_id, task_id=task_id, locked=True,
                            ))
                        else:
                            sel.task_id = task_id
                            sel.locked  = True

                        sub.status = "submitted"
                        write_audit(
                            "submission.created", actor=user,
                            target_type="submission", target_id=None, ip=ip,
                        )
                        job.status      = _DONE
                        job.result_path = Path(sub.file_path) if sub.file_path else None
                        _notify_type    = "success"

                sub_title  = sub.title or ""
                _file_names = (
                    ([sub.filename] if sub.filename else []) +
                    [ef.filename for ef in sub.extra_files]
                ) if sub.status == "submitted" else None
                db.session.commit()

                if _notify_type and user and user.email:
                    try:
                        from services.email import (
                            send_submission_success_email,
                            send_submission_failed_email,
                        )
                        mail_cfg  = _build_mail_cfg(app)
                        app_name  = app.config.get("APP_NAME", "prompt-a-thon")
                        if _notify_type == "success":
                            ok, e_err = send_submission_success_email(
                                mail_cfg, user.email, app_name, sub_title,
                                file_names=_file_names,
                            )
                        else:
                            ok, e_err = send_submission_failed_email(
                                mail_cfg, user.email, app_name, sub_title,
                                _notify_error or "File processing failed.",
                            )
                        if not ok:
                            logger.warning(
                                "queue: status email (%s) failed for sub %s: %s",
                                _notify_type, sub_id, e_err,
                            )
                    except Exception as exc:
                        logger.warning(
                            "queue: status email (%s) exception for sub %s: %s",
                            _notify_type, sub_id, exc,
                        )

            except Exception as exc:
                logger.error("queue: DB update failed for sub %s: %s", sub_id, exc)
                try:
                    db.session.rollback()
                except Exception:
                    pass
                err = "A database error occurred. Please try again."
                with self._lock:
                    self._sub_errors[sub_id] = err
                job.status = _ERROR
                job.error  = err

        self._purge_old()

    def poll(self, job_id: str) -> Optional[ConversionJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_error(self, sub_id: int) -> Optional[str]:

        with self._lock:
            return self._sub_errors.get(sub_id)

    def mark_terminated(self, sub_id: int) -> None:

        with self._lock:
            self._sub_errors[sub_id] = "Terminated by an administrator."

    def _purge_old(self):
        now = time.monotonic()
        with self._lock:
            expired = [
                jid for jid, j in self._jobs.items()
                if j.status in (_DONE, _ERROR) and (now - j.finished_at) > TTL_SECONDS
            ]
            for jid in expired:
                j = self._jobs.pop(jid)
                if j.sub_id is not None:
                    self._sub_errors.pop(j.sub_id, None)
        if expired:
            logger.debug("queue: purged %d expired jobs", len(expired))

    def shutdown(self):
        self._executor.shutdown(wait=False)

_queue: Optional[ConversionQueue] = None
_queue_lock = threading.Lock()

def get_queue() -> ConversionQueue:
    global _queue
    if _queue is None:
        with _queue_lock:
            if _queue is None:
                _queue = ConversionQueue(max_workers=MAX_WORKERS)
                logger.info("conversion queue started (max_workers=%d)", MAX_WORKERS)
    return _queue
