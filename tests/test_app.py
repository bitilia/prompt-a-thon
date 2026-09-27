import io
import os
import tokenize
from datetime import timedelta
from pathlib import Path

from config import Testing, resolve_dir
from models import ROLE_ADMIN, ROLE_JUDGE, ROLE_PARTICIPANT, SiteSettings, User, utcnow
from routes.auth import _is_safe_next
from services.update_checker import check_manifest

from app import purge_unverified_participants
from extensions import db


def test_home_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"prompt-a-thon" in response.data


def test_login_rejects_bad_email(client):
    response = client.post("/auth/login", data={"email": "not-an-email"})
    assert response.status_code == 400


def test_allowlist(app):
    SiteSettings.set_email_allowlist_domains(["example.edu"])
    SiteSettings.set("email_allowlist_mode", "allowlist")
    db.session.commit()
    assert SiteSettings.email_is_allowed("student@example.edu") is True
    assert SiteSettings.email_is_allowed("student@other.edu") is False
    assert SiteSettings.email_is_allowed("student@sub.example.edu") is False


def test_encryption_roundtrip(app):
    user = User(email="person@example.edu", role=ROLE_PARTICIPANT, ban_reason="kept secret")
    db.session.add(user)
    db.session.commit()
    db.session.expire_all()
    loaded = User.query.filter_by(email="person@example.edu").one()
    assert loaded.ban_reason == "kept secret"


def test_safe_next_rejects_offsite_targets():
    assert _is_safe_next("/dashboard/submit") is True
    assert _is_safe_next("https://example.com") is False
    assert _is_safe_next("//example.com") is False


def test_purge_keeps_staff(app):
    old = utcnow() - timedelta(hours=2)
    db.session.add(User(email="admin@example.edu", role=ROLE_ADMIN, created_at=old))
    db.session.add(User(email="judge@example.edu", role=ROLE_JUDGE, created_at=old))
    db.session.add(User(email="person@example.edu", role=ROLE_PARTICIPANT, created_at=old))
    db.session.commit()
    deleted = purge_unverified_participants()
    assert deleted == 1
    assert User.query.filter_by(email="admin@example.edu").one()
    assert User.query.filter_by(email="judge@example.edu").one()
    assert User.query.filter_by(email="person@example.edu").first() is None


def test_country_message_is_escaped(app, client):
    app.config["TRUST_CLOUDFLARE"] = True
    SiteSettings.set("country_restrict_mode", "blocklist")
    SiteSettings.set("country_restrict_list", '["US"]')
    SiteSettings.set("country_block_message", "<script>alert(1)</script>")
    db.session.commit()
    response = client.get("/", headers={"CF-IPCountry": "US"})
    assert response.status_code == 403
    assert b"<script>" not in response.data
    assert b"&lt;script&gt;" in response.data


def test_cloudflare_ip_is_ignored_unless_enabled(app, client):
    SiteSettings.set("blocked_ips", '["1.2.3.4"]')
    db.session.commit()
    app.config["TRUST_CLOUDFLARE"] = False
    open_response = client.get("/", headers={"CF-Connecting-IP": "1.2.3.4"})
    assert open_response.status_code == 200
    app.config["TRUST_CLOUDFLARE"] = True
    blocked = client.get("/", headers={"CF-Connecting-IP": "1.2.3.4"})
    assert blocked.status_code == 403


def test_update_check_does_not_use_network(app):
    status = check_manifest()
    assert status["updates_configured"] is False
    assert status["installed_status"] == "unconfigured"
    assert status["latest"] is None


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_http_manifest_is_refused(monkeypatch):
    import services.update_checker as checker

    calls = {"n": 0}

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        raise RuntimeError("network")

    monkeypatch.setattr(checker, "MANIFEST_URL", "http://127.0.0.1/manifest.json")
    monkeypatch.setattr(checker._ur, "build_opener", boom)
    status = checker.check_manifest()
    assert calls["n"] == 0
    assert status["updates_configured"] is True
    assert status.get("downloaded_path") is None
    assert "127.0.0.1" not in (status.get("last_check_error") or "")


def test_remote_update_download_is_disabled():
    from services.update_checker import download_update

    try:
        download_update({"version": "9.9.9", "download_url": "https://example.com/setup.py"})
    except RuntimeError as exc:
        assert "setup.py" not in str(exc)
        return
    raise AssertionError("download should be refused")


def test_markdown_filter_returns_html(app):
    rendered = app.jinja_env.filters["markdown"]("**bold**")
    assert "<strong>bold</strong>" in str(rendered)


def test_resolve_dir_keeps_absolute_paths():
    absolute = os.path.abspath(os.path.join(os.getcwd(), "uploads-abs"))
    assert resolve_dir(Path.cwd(), absolute, "uploads") == absolute
    relative = resolve_dir(Path.cwd(), "uploads-rel", "uploads")
    assert relative.endswith("uploads-rel")


def test_testing_database_uses_static_pool():
    assert Testing.SQLALCHEMY_ENGINE_OPTIONS["poolclass"].__name__ == "StaticPool"


def test_repository_has_no_old_brand_or_python_comments():
    root = Path(__file__).resolve().parents[1]
    needle = bytes([104, 101, 120, 102, 111, 120])
    skip = {".git", "venv", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skip]
        for name in filenames:
            path = Path(dirpath) / name
            assert needle not in name.lower().encode()
            blob = path.read_bytes()
            assert needle not in blob.lower(), path
            if path.suffix == ".py" and "vendor" not in path.parts:
                text = blob.decode("utf-8", errors="ignore")
                tokens = tokenize.generate_tokens(io.StringIO(text).readline)
                for tok in tokens:
                    if tok.type == tokenize.COMMENT and not tok.string.startswith("#!"):
                        raise AssertionError(f"comment in {path}")
