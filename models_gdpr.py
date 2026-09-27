from extensions import db
from models import EncryptedText, utcnow

class ConsentRecord(db.Model):

    __tablename__ = "consent_records"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    session_id = db.Column(db.String(64), nullable=True, index=True)
    consent_version = db.Column(db.String(20), nullable=False, default="1.0")
    necessary = db.Column(db.Boolean, nullable=False, default=True)
    analytics = db.Column(db.Boolean, nullable=False, default=False)
    preferences = db.Column(db.Boolean, nullable=False, default=False)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    granted_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    withdrawn_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="banner")

REQUEST_TYPES = {
    "access": "Access (copy of my data)",
    "erasure": "Erasure (delete my data)",
    "portability": "Portability (export my data)",
    "rectification": "Rectification (correct my data)",
}

REQUEST_STATUSES = ("pending", "in_progress", "completed", "rejected")

class DataRequest(db.Model):

    __tablename__ = "data_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    email = db.Column(db.String(254), nullable=False, index=True)
    request_type = db.Column(db.String(20), nullable=False)
    detail = db.Column(EncryptedText, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    @property
    def request_type_label(self) -> str:
        return REQUEST_TYPES.get(self.request_type, self.request_type)
