import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="promptathon-test-"))
os.environ["FLASK_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_EMAIL"] = "admin@example.edu"
os.environ["APP_NAME"] = "prompt-a-thon"
os.environ["UPLOAD_FOLDER"] = str(_TMP / "uploads")
os.environ["APP_DIR"] = str(_TMP)
os.environ["UPDATE_MANIFEST_URL"] = ""
os.environ["UPDATE_STATUS_FILE"] = str(_TMP / "update_status.json")
os.environ["UPDATE_VERSION_FILE"] = str(_TMP / "version.json")
os.environ["TRUST_CLOUDFLARE"] = "false"
os.environ["MAIL_PROVIDER"] = "smtp"

import pytest

from app import create_app, purge_unverified_participants
from extensions import db


@pytest.fixture
def app():
    application = create_app()
    application.config["TRUST_CLOUDFLARE"] = False
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
