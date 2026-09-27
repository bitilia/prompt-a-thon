from __future__ import annotations

import json
import logging
import os
import pathlib
import urllib.request as _ur
from datetime import datetime, timezone
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MANIFEST_URL = os.environ.get("UPDATE_MANIFEST_URL", "").strip()
_BASE = pathlib.Path(os.environ.get("APP_DIR", "/opt/prompt-a-thon"))
VERSION_FILE = pathlib.Path(
    os.environ.get("UPDATE_VERSION_FILE", str(_BASE / "instance" / "version.json"))
)
STATUS_FILE = pathlib.Path(
    os.environ.get("UPDATE_STATUS_FILE", str(_BASE / "instance" / "update_status.json"))
)
_MAX_MANIFEST_BYTES = 262144
_MAX_RELEASES = 40

def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def get_installed_version() -> str:
    try:
        return json.loads(VERSION_FILE.read_text()).get("version", "unknown")
    except Exception:
        return "unknown"

def get_update_status() -> dict:
    try:
        return json.loads(STATUS_FILE.read_text())
    except Exception:
        return {}

def _write_status(status: dict) -> None:
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATUS_FILE.write_text(json.dumps(status, indent=2) + "\n")
    except Exception as exc:
        logger.error("failed to persist update status: %s", exc)

def _release_view(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    version = str(item.get("version") or "")[:32]
    if not version:
        return None
    view = {
        "version": version,
        "status": str(item.get("status") or "")[:32],
    }
    notes = item.get("notes")
    date = item.get("date")
    if notes:
        view["notes"] = str(notes)[:400]
    if date:
        view["date"] = str(date)[:40]
    return view

class _NoRedirect(_ur.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirects are not followed")

def _fetch_manifest(url: str) -> dict:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or any(ch.isspace() for ch in url):
        raise ValueError("manifest URL must be https")
    opener = _ur.build_opener(_NoRedirect)
    req = _ur.Request(
        url,
        headers={
            "User-Agent": "prompt-a-thon-UpdateChecker/1.0",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    with opener.open(req, timeout=15) as resp:
        final = urlparse(resp.geturl())
        if (final.scheme != "https") or ((final.hostname or "").lower() != host):
            raise ValueError("redirects are not followed")
        raw = resp.read(_MAX_MANIFEST_BYTES + 1)
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise ValueError("manifest is too large")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    return manifest

def check_manifest() -> dict:
    installed = get_installed_version()
    previous = get_update_status()
    if not MANIFEST_URL:
        status = {
            "checked_at": _now().isoformat(),
            "installed_version": installed,
            "installed_status": "unconfigured",
            "latest": None,
            "releases": [],
            "updates_configured": False,
        }
        _write_status(status)
        return status

    try:
        manifest = _fetch_manifest(MANIFEST_URL)
    except Exception as exc:
        logger.warning("update manifest fetch failed")
        status = dict(previous)
        status.pop("downloaded_path", None)
        status.pop("downloaded_paths", None)
        status["last_check_error"] = "Update check failed"
        status["checked_at"] = _now().isoformat()
        status["updates_configured"] = True
        _write_status(status)
        logger.debug("update manifest fetch detail: %s", exc)
        return status

    raw_releases = manifest.get("releases") or []
    if not isinstance(raw_releases, list):
        raw_releases = []
    releases = []
    for item in raw_releases[:_MAX_RELEASES]:
        view = _release_view(item)
        if view:
            releases.append(view)
    installed_release = next((r for r in releases if r.get("version") == installed), None)
    installed_status = installed_release.get("status", "unknown") if installed_release else "unknown"
    latest_release = next((r for r in releases if r.get("status") == "latest"), None)
    status = {
        "checked_at": _now().isoformat(),
        "installed_version": installed,
        "installed_status": installed_status,
        "latest": latest_release,
        "releases": releases,
        "updates_configured": True,
    }
    _write_status(status)
    logger.info(
        "update check: installed=%s (%s), latest=%s",
        installed, installed_status,
        latest_release.get("version") if latest_release else "n/a",
    )
    return status

def download_update(release: dict) -> pathlib.Path:
    version = str((release or {}).get("version") or "")
    raise RuntimeError(f"Refusing to download release files ({version})")
