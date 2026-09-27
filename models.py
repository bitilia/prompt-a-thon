import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app
from flask_login import UserMixin
from sqlalchemy import Index, UniqueConstraint, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import validates
from sqlalchemy.types import Text, TypeDecorator

from extensions import db, login_manager

@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
    except Exception:

        pass

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

_FERNET_CACHE: dict = {}

def _get_fernet() -> Fernet:

    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    else:
        secret_bytes = bytes(secret)

    cached = _FERNET_CACHE.get(secret_bytes)
    if cached is not None:
        return cached

    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"promptathon-fernet-v1",
        info=b"column-encryption-key",
    ).derive(secret_bytes)
    key = base64.urlsafe_b64encode(derived)
    f = Fernet(key)
    _FERNET_CACHE[secret_bytes] = f
    return f

class EncryptedText(TypeDecorator):

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        token = _get_fernet().encrypt(value.encode("utf-8"))
        return token.decode("ascii")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            plaintext = _get_fernet().decrypt(value.encode("ascii"))
            return plaintext.decode("utf-8")
        except InvalidToken:

            current_app.logger.error("EncryptedText: failed to decrypt column value")
            return None

ROLE_PARTICIPANT = "participant"
ROLE_JUDGE = "judge"
ROLE_ADMIN = "admin"
VALID_ROLES = {ROLE_PARTICIPANT, ROLE_JUDGE, ROLE_ADMIN}

AUTH_EMAIL = "email"
AUTH_MICROSOFT = "microsoft"

SUBMISSION_STATUSES = {
    "submitted": "Submitted",
    "under_review": "Under review",
    "reviewed": "Reviewed",
    "disqualified": "Disqualified",
}

SCORE_FIELDS = ["score1", "score2", "score3"]

def _generate_anonymous_id() -> str:

    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))

class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=True)
    role = db.Column(db.String(20), nullable=False, default=ROLE_PARTICIPANT)

    judge_score_fields = db.Column(db.String(50), nullable=True)
    auth_provider = db.Column(db.String(20), nullable=False, default=AUTH_EMAIL)
    ms_object_id = db.Column(db.String(64), nullable=True, index=True)

    is_active_flag = db.Column("is_active", db.Boolean, nullable=False, default=True)
    is_banned = db.Column(db.Boolean, nullable=False, default=False)
    ban_reason = db.Column(EncryptedText, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(45), nullable=True)

    otp_tokens = db.relationship(
        "OTPToken", backref="user", cascade="all, delete-orphan", lazy="dynamic"
    )
    submissions = db.relationship(
        "Submission",
        backref="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
        foreign_keys="Submission.user_id",
    )
    task_selection = db.relationship(
        "TaskSelection", backref="user", cascade="all, delete-orphan", uselist=False
    )
    academy_progress = db.relationship(
        "AcademyProgress", backref="user", cascade="all, delete-orphan", lazy="dynamic"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @validates("email")
    def _normalise_email(self, key, value):

        if value is None:
            return None
        return value.strip().lower()

    @property
    def is_judge(self) -> bool:
        return self.role == ROLE_JUDGE

    @property
    def judge_score_fields_list(self) -> list:

        if not self.judge_score_fields:
            return []
        return [f.strip() for f in self.judge_score_fields.split(",") if f.strip() in SCORE_FIELDS]

    @property
    def display_name(self) -> str:
        if self.name and self.name.strip():
            return self.name.strip()
        return self.email.split("@")[0]

    @property
    def is_active(self) -> bool:
        return bool(self.is_active_flag) and not self.is_banned

    def update_login(self, ip: str) -> None:
        self.last_login_at = utcnow()
        self.last_login_ip = (ip or "")[:45]

    def __repr__(self) -> str:
        return f"<User {self.email}>"

@login_manager.user_loader
def _load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None

class SubmissionFile(db.Model):

    __tablename__ = "submission_files"

    id            = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.Integer,
        db.ForeignKey("submissions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    position  = db.Column(db.Integer, nullable=False)
    filename  = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer, nullable=True)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class ConnectionEvent(db.Model):

    __tablename__ = "connection_events"

    id           = db.Column(db.Integer, primary_key=True)
    created_at   = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    ip           = db.Column(db.String(45), nullable=False, index=True)
    country_code = db.Column(db.String(2), nullable=True, index=True)
    path         = db.Column(db.String(500), nullable=True)
    method       = db.Column(db.String(10), nullable=True)
    user_id      = db.Column(db.Integer, nullable=True)
    is_blocked   = db.Column(db.Boolean, default=False, nullable=False)

class OTPToken(db.Model):
    __tablename__ = "otp_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    used = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    ip_address = db.Column(db.String(45), nullable=True)

    @staticmethod
    def _hash(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @classmethod
    def generate(cls, user: User, expiry_minutes: int, ip: str | None):

        cls.query.filter_by(user_id=user.id, used=False).update({"used": True})

        code = f"{secrets.randbelow(1_000_000):06d}"
        token = cls(
            user_id=user.id,
            token_hash=cls._hash(code),
            expires_at=utcnow() + timedelta(minutes=expiry_minutes),
            attempts=0,
            used=False,
            ip_address=(ip or "")[:45] or None,
        )
        db.session.add(token)
        db.session.flush()
        return token, code

    def verify(self, code: str, max_attempts: int) -> bool:
        if self.used:
            return False
        if utcnow() > self.expires_at:
            self.used = True
            return False

        self.attempts = (self.attempts or 0) + 1
        if self.attempts > max_attempts:
            self.used = True
            return False
        candidate = self._hash((code or "").strip())
        if hmac.compare_digest(candidate, self.token_hash):
            self.used = True
            return True
        if self.attempts >= max_attempts:
            self.used = True
        return False

class TaskSelection(db.Model):
    __tablename__ = "task_selections"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    task_id = db.Column(db.String(64), nullable=False)
    locked = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

class Submission(db.Model):
    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    anonymous_id = db.Column(db.String(8), nullable=True, unique=True, index=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id = db.Column(db.String(64), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(EncryptedText, nullable=True)
    filename = db.Column(db.String(255), nullable=True)
    file_path = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="submitted", index=True)
    score = db.Column(db.Integer, nullable=True)
    score1 = db.Column(db.Integer, nullable=True)
    score2 = db.Column(db.Integer, nullable=True)
    score3 = db.Column(db.Integer, nullable=True)
    judge_notes = db.Column(EncryptedText, nullable=True)
    reviewed_by = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    ip_address = db.Column(db.String(45), nullable=True)

    reviewer = db.relationship("User", foreign_keys=[reviewed_by])
    extra_files = db.relationship(
        "SubmissionFile", backref="submission", cascade="all, delete-orphan",
        order_by="SubmissionFile.position",
    )

    @property
    def status_label(self) -> str:
        return SUBMISSION_STATUSES.get(self.status, self.status.replace("_", " ").title())

    def ensure_anonymous_id(self) -> None:

        if self.anonymous_id:
            return
        from sqlalchemy.exc import IntegrityError
        for _ in range(20):
            candidate = _generate_anonymous_id()
            if not Submission.query.filter_by(anonymous_id=candidate).first():
                self.anonymous_id = candidate
                return

        self.anonymous_id = _generate_anonymous_id()

    @property
    def total_score(self) -> int | None:

        if self.score1 is None and self.score2 is None and self.score3 is None:

            return self.score
        return (self.score1 or 0) + (self.score2 or 0) + (self.score3 or 0)

class AcademyProgress(db.Model):
    __tablename__ = "academy_progress"
    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_user_item"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id = db.Column(db.String(120), nullable=False)
    completed = db.Column(db.Boolean, nullable=False, default=True)
    completed_at = db.Column(db.DateTime, nullable=False, default=utcnow)

class SiteSettings(db.Model):
    __tablename__ = "site_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @classmethod
    def get(cls, key: str, default=None):
        row = db.session.get(cls, key)
        if row is None:
            return default
        return row.value

    @classmethod
    def set(cls, key: str, value):
        if value is None:
            value_str = None
        else:
            value_str = str(value)
        row = db.session.get(cls, key)
        if row is None:
            row = cls(key=key, value=value_str)
            db.session.add(row)
        else:
            row.value = value_str
            row.updated_at = utcnow()
        return row

    @classmethod
    def _date_window_open(cls, open_key: str, close_key: str) -> bool:
        from datetime import date, timezone

        today = date.today()
        open_str = cls.get(open_key)
        close_str = cls.get(close_key)

        def _parse(s):
            if not s:
                return None
            try:
                return date.fromisoformat(s.strip())
            except (ValueError, AttributeError):
                return None

        open_d = _parse(open_str)
        close_d = _parse(close_str)
        if open_d and today < open_d:
            return False
        if close_d and today > close_d:
            return False
        return True

    @classmethod
    def registration_is_open(cls) -> bool:
        override = cls.get("registration_override")
        if override == "1":
            return True
        if override == "0":
            return False
        return cls._date_window_open("registration_open_date", "registration_close_date")

    @classmethod
    def submission_is_open(cls) -> bool:
        override = cls.get("submission_override")
        if override == "1":
            return True
        if override == "0":
            return False
        return cls._date_window_open("submission_open_date", "submission_deadline_date")

    @classmethod
    def email_allowlist_mode(cls) -> str:

        return cls.get("email_allowlist_mode", "allowlist") or "allowlist"

    @classmethod
    def email_allowlist_domains(cls) -> list[str]:

        raw = cls.get("email_allowlist_domains", "") or ""
        domains = []
        for d in raw.replace(",", "\n").splitlines():
            d = d.strip().lower().lstrip("@")
            if d and d not in domains:
                domains.append(d)
        return domains

    @classmethod
    def set_email_allowlist_domains(cls, domains) -> None:
        cleaned = []
        for d in domains or []:
            d = (d or "").strip().lower().lstrip("@")
            if d and d not in cleaned:
                cleaned.append(d)
        cls.set("email_allowlist_domains", "\n".join(cleaned))

    @classmethod
    def email_is_allowed(cls, email: str) -> bool:

        if not email or "@" not in email:
            return False
        if cls.email_allowlist_mode() == "allow_all":
            return True
        domain = email.rsplit("@", 1)[-1].strip().lower()
        return domain in cls.email_allowlist_domains()

    @classmethod
    def announcement_enabled(cls) -> bool:
        return (cls.get("announcement_enabled") or "0") == "1"

    @classmethod
    def announcement_text(cls) -> str:
        return cls.get("announcement_text", "") or ""

    @classmethod
    def lockdown_enabled(cls) -> bool:
        return (cls.get("lockdown_enabled") or "0") == "1"

    @classmethod
    def lockdown_message(cls) -> str:
        default = ("We are working to get this up and running. "
                   "Please check back later.")
        v = cls.get("lockdown_message")
        return v if v else default

    @classmethod
    def submission_success_message(cls) -> str:

        default = (
            "Your work has been submitted successfully.\n\n"
            "We'll be in touch once judging is complete. "
            "You can review or download what you submitted from the "
            "**Submissions** tab."
        )
        val = cls.get("submission_success_message")
        return val if val else default

class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email = db.Column(db.String(254), nullable=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(40), nullable=True)
    target_id = db.Column(db.String(64), nullable=True)
    detail = db.Column(EncryptedText, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

def write_audit(action: str, actor=None, target_type=None, target_id=None,
                detail=None, ip=None) -> None:

    entry = AuditLog(
        actor_id=getattr(actor, "id", None),
        actor_email=getattr(actor, "email", None),
        action=action[:80],
        target_type=(target_type or None),
        target_id=(str(target_id)[:64] if target_id is not None else None),
        detail=detail,
        ip_address=(ip or "")[:45] or None,
    )
    db.session.add(entry)

Index("ix_audit_action_created", AuditLog.action, AuditLog.created_at)

class EmailLog(db.Model):

    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)
    to_email = db.Column(db.String(254), nullable=False, index=True)
    subject = db.Column(db.String(500), nullable=False)
    category = db.Column(db.String(40), nullable=False, index=True)
    sent_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    tracking_pixel_id = db.Column(db.String(64), nullable=True, unique=True, index=True)
    opened_at = db.Column(db.DateTime, nullable=True)
    body_html = db.Column(EncryptedText, nullable=True)

class Certificate(db.Model):

    __tablename__ = "certificates"

    id = db.Column(db.Integer, primary_key=True)
    recipient_email = db.Column(db.String(254), nullable=False, index=True)
    recipient_name = db.Column(db.String(200), nullable=True)
    competition_name = db.Column(db.String(200), nullable=False)
    school_name = db.Column(db.String(200), nullable=False)
    award_type = db.Column(db.String(20), nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    pdf_generated = db.Column(db.Boolean, nullable=False, default=False)
    email_log_id = db.Column(
        db.Integer,
        db.ForeignKey("email_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
