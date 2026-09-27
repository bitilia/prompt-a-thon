#!/usr/bin/env python3

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

APP_VERSION = Path(__file__).resolve().parent.joinpath("VERSION").read_text().strip()
SRC_DIR = Path(__file__).resolve().parent
DEFAULT_INSTALL_DIR = Path("/opt/prompt-a-thon")
APP_USER = "promptathon"
APP_GROUP = "promptathon"
CONVERT_USER = "promptathon-convert"
CONVERT_GROUP = "promptathon-convert"
APP_PORT = 8100

APT_PACKAGES = [
    "python3", "python3-venv", "python3-pip", "python3-dev",
    "build-essential", "libffi-dev",
    "curl", "ca-certificates",
    "libpango-1.0-0", "libpangocairo-1.0-0", "libcairo2", "libcairo2-dev",
    "libgdk-pixbuf-2.0-0", "libffi8", "shared-mime-info", "pkg-config",
    "python3-cffi", "python3-brotli", "qpdf",
    "libreoffice-writer",
]

COPY_EXCLUDE = {
    "venv", "instance", "uploads", "logs", ".env", "__pycache__",
    ".git", "dist", "build", "setup.py", "README.md", "VERSION",
    ".gitignore", "deploy", "conversion", "tests", "docs", ".github",
}

def _venv_exe(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name

def info(msg: str) -> None:
    print(f"[*] {msg}", flush=True)

def ok(msg: str) -> None:
    print(f"[+] {msg}", flush=True)

def warn(msg: str) -> None:
    print(f"[!] {msg}", flush=True)

def die(msg: str, code: int = 1) -> None:
    print(f"[X] {msg}", file=sys.stderr, flush=True)
    sys.exit(code)

def run(cmd, check=True, env=None, cwd=None):
    info("$ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=check, env=env, cwd=cwd)

def ensure_root() -> None:
    if os.geteuid() != 0:
        die("Run as root: sudo python3 setup.py")

def ensure_user(name: str) -> None:
    import pwd
    try:
        pwd.getpwnam(name)
        ok(f"user {name} exists")
        return
    except KeyError:
        pass
    run(["useradd", "--system", "--home-dir", f"/home/{name}",
         "--create-home", "--shell", "/usr/sbin/nologin", name])
    ok(f"created user {name}")

def apt_install() -> None:
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run(["apt-get", "update"], env=env)
    run(["apt-get", "install", "-y"] + APT_PACKAGES, env=env)
    if not (shutil.which("libreoffice") or shutil.which("soffice")):
        die("LibreOffice Writer is required for DOCX conversion but was not found after apt install")
    ok("system packages installed")

def copy_app(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in SRC_DIR.iterdir():
        if item.name in COPY_EXCLUDE or item.name.startswith("."):

            if item.name != ".env.example":
                continue
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(
                item, target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "venv"),
            )
        else:
            shutil.copy2(item, target)
    for d in ("instance", "uploads", "logs"):
        (dest / d).mkdir(parents=True, exist_ok=True)

    (dest / "instance" / "version.json").write_text(
        f'{{"version": "{APP_VERSION}", "installed_by": "setup.py"}}\n'
    )
    ok(f"app files → {dest}")

def install_conversion(dest: Path) -> None:
    conv = dest / "conversion"
    conv.mkdir(parents=True, exist_ok=True)
    (conv / "tmp").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC_DIR / "conversion" / "convert_daemon.py", conv / "convert_daemon.py")
    venv = conv / "venv"
    if not _venv_exe(venv, "python").exists():
        run([sys.executable, "-m", "venv", str(venv)])

    ok(f"conversion daemon → {conv}")

def make_venv(dest: Path) -> Path:
    venv = dest / "venv"
    if not _venv_exe(venv, "pip").exists():
        run([sys.executable, "-m", "venv", str(venv)])
    pip = _venv_exe(venv, "pip")
    run([str(pip), "install", "--upgrade", "pip", "wheel"])
    run([str(pip), "install", "-r", str(dest / "requirements.txt")])
    ok("Python dependencies installed")
    return venv

def write_env(dest: Path, args) -> None:
    secret = secrets.token_hex(64)
    base_url = (args.base_url or f"https://{args.domain}").rstrip("/")
    lines = [
        f"APP_NAME={args.app_name}",
        f"TAGLINE={args.tagline}",
        f"COLLEGE_NAME={args.college_name}",
        f"COLLEGE_URL={args.college_url}",
        f"CONTACT_EMAIL={args.admin_email}",
        f"BASE_URL={base_url}",
        f"SECRET_KEY={secret}",
        "FLASK_ENV=production",
        "PROXY_FIX_X_FOR=1",
        "PROXY_FIX_X_PROTO=1",
        "PROXY_FIX_X_HOST=1",
        f"DATABASE_URL=sqlite:///{dest / 'instance' / 'prompt.db'}",
        f"ADMIN_EMAIL={args.admin_email}",
        f"MAIL_PROVIDER={args.mail_provider}",
        f"MAIL_FROM_ADDRESS={args.mail_from or args.admin_email}",
        f"MAIL_FROM_NAME={args.app_name}",
        f"SMTP_HOST={args.smtp_host}",
        f"SMTP_PORT={args.smtp_port}",
        f"SMTP_USER={args.smtp_user}",
        f"SMTP_PASSWORD={args.smtp_password}",
        f"SMTP_FROM_NAME={args.app_name}",
        f"SMTP_USE_TLS={'true' if args.smtp_tls else 'false'}",
        f"BREVO_API_KEY={args.brevo_api_key}",
        f"SES_ACCESS_KEY_ID={args.ses_access_key}",
        f"SES_SECRET_ACCESS_KEY={args.ses_secret_key}",
        f"SES_REGION={args.ses_region}",
        f"UPLOAD_FOLDER={dest / 'uploads'}",
        "MAX_UPLOAD_MB=25",
        "RATELIMIT_STORAGE_URI=memory://",
        "AUDIT_LOG_RETENTION_DAYS=365",
        f"CONVERSION_DIR={dest / 'conversion'}",
        f"APP_DIR={dest}",
        "INSTANCE_SLUG=",
        "TRUST_CLOUDFLARE=false",
        "UPDATE_MANIFEST_URL=",
    ]
    env_path = dest / ".env"
    env_path.write_text("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)
    ok(f"wrote {env_path} (mode 0600)")

def chown_tree(path: Path, user: str) -> None:
    import pwd
    pw = pwd.getpwnam(user)
    for root, dirs, files in os.walk(path):
        os.chown(root, pw.pw_uid, pw.pw_gid)
        for name in dirs + files:
            try:
                os.chown(os.path.join(root, name), pw.pw_uid, pw.pw_gid)
            except OSError:
                pass

def init_db(dest: Path, admin_email: str) -> None:
    env = os.environ.copy()

    for line in (dest / ".env").read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k] = v
    env["FLASK_APP"] = "app:create_app"
    flask = _venv_exe(dest / "venv", "flask")
    run([str(flask), "init-db"], cwd=str(dest), env=env)
    run([str(flask), "create-admin", admin_email], cwd=str(dest), env=env)
    ok("database initialised + admin promoted")

def install_units(dest: Path) -> None:
    units = [
        "prompt-a-thon.service",
        "prompt-a-thon-convert.service",
        "prompt-a-thon-jobs.service",
        "prompt-a-thon-jobs.timer",
    ]
    for name in units:
        src = SRC_DIR / "deploy" / name
        text = src.read_text()
        text = text.replace("/opt/prompt-a-thon", str(dest))
        out = Path("/etc/systemd/system") / name
        out.write_text(text)
        ok(f"installed {out}")

    run(["usermod", "-aG", CONVERT_GROUP, APP_USER], check=False)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "prompt-a-thon-convert.service"])
    run(["systemctl", "enable", "--now", "prompt-a-thon.service"])
    run(["systemctl", "enable", "--now", "prompt-a-thon-jobs.timer"])
    ok("systemd units enabled")

def prompt_if_needed(args) -> None:
    if args.yes:
        missing = []
        if not args.domain and not args.base_url:
            missing.append("--domain or --base-url")
        if not args.admin_email:
            missing.append("--admin-email")
        if missing:
            die("non-interactive mode requires: " + ", ".join(missing))
        return

    print(textwrap.dedent(f"""
    prompt-a-thon competition installer v{APP_VERSION}
    Installs one self-hosted competition on this machine.
    """).strip())
    print()
    if not args.domain:
        args.domain = input("Public hostname (e.g. promptathon.college.edu): ").strip()
    if not args.base_url:
        args.base_url = f"https://{args.domain}"
    if not args.admin_email:
        args.admin_email = input("Admin email (will be promoted to admin): ").strip().lower()
    if not args.app_name:
        args.app_name = input("Competition name [prompt-a-thon]: ").strip() or "prompt-a-thon"
    if not args.college_name:
        args.college_name = input("Organisation / college name: ").strip() or "Your College"
    if not args.mail_provider:
        args.mail_provider = (
            input("Mail provider [smtp|brevo_api|ses_api|postfix] (default smtp): ").strip()
            or "smtp"
        ).lower()
    if args.mail_provider == "smtp":
        if not args.smtp_host:
            args.smtp_host = input("SMTP host: ").strip()
        if not args.smtp_user:
            args.smtp_user = input("SMTP user: ").strip()
        if not args.smtp_password:
            args.smtp_password = getpass.getpass("SMTP password: ")
    elif args.mail_provider == "brevo_api" and not args.brevo_api_key:
        args.brevo_api_key = getpass.getpass("Brevo API key: ")
    elif args.mail_provider == "ses_api":
        if not args.ses_access_key:
            args.ses_access_key = input("SES access key id: ").strip()
        if not args.ses_secret_key:
            args.ses_secret_key = getpass.getpass("SES secret access key: ")

def setup_dev(args) -> None:

    dest = SRC_DIR
    (dest / "instance").mkdir(exist_ok=True)
    (dest / "uploads").mkdir(exist_ok=True)
    (dest / "logs").mkdir(exist_ok=True)
    (dest / "conversion" / "tmp").mkdir(parents=True, exist_ok=True)
    venv = dest / "venv"
    pip = _venv_exe(venv, "pip")
    if not pip.exists():
        run([sys.executable, "-m", "venv", str(venv)])
    run([str(_venv_exe(venv, "pip")), "install", "--upgrade", "pip", "wheel"])
    run([str(_venv_exe(venv, "pip")), "install", "-r", str(dest / "requirements.txt")])
    env_path = dest / ".env"
    if not env_path.exists():
        secret = secrets.token_hex(64)
        admin = args.admin_email or "admin@example.edu"
        env_path.write_text(textwrap.dedent(f"""\
            APP_NAME={args.app_name or 'prompt-a-thon'}
            TAGLINE={args.tagline}
            COLLEGE_NAME={args.college_name or 'Dev College'}
            BASE_URL=http://127.0.0.1:5000
            SECRET_KEY={secret}
            FLASK_ENV=development
            DATABASE_URL=sqlite:///{dest / 'instance' / 'prompt.db'}
            ADMIN_EMAIL={admin}
            MAIL_PROVIDER=smtp
            SMTP_HOST=localhost
            SMTP_PORT=1025
            SMTP_USER=
            SMTP_PASSWORD=
            SMTP_USE_TLS=false
            UPLOAD_FOLDER={dest / 'uploads'}
            CONVERSION_DIR={dest / 'conversion'}
            APP_DIR={dest}
            INSTANCE_SLUG=
            TRUST_CLOUDFLARE=false
            UPDATE_MANIFEST_URL=
        """))
        ok(f"wrote {env_path}")
    env = os.environ.copy()
    for line in env_path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    env["FLASK_APP"] = "app:create_app"
    flask = _venv_exe(venv, "flask")
    py = _venv_exe(venv, "python")
    run([str(flask), "init-db"], cwd=str(dest), env=env)
    run([str(flask), "create-admin", env["ADMIN_EMAIL"]],
        cwd=str(dest), env=env)
    if os.name == "nt":
        print(textwrap.dedent(f"""
        Dev install ready.

          terminal 1 — conversion daemon (needs LibreOffice)
          set CONVERSION_DIR={dest / 'conversion'}
          {py} {dest / 'conversion' / 'convert_daemon.py'}

          terminal 2 — web app
          cd {dest}
          {flask} run --host 127.0.0.1 --port 5000

        Load the variables in .env into the environment before the second command.
        Sign in with OTP at the ADMIN_EMAIL address.
        """).strip())
    else:
        print(textwrap.dedent(f"""
        Dev install ready.

          terminal 1 — conversion daemon (needs LibreOffice)
          CONVERSION_DIR={dest / 'conversion'} {py} \\
              {dest / 'conversion' / 'convert_daemon.py'}

          terminal 2 — web app
          cd {dest}
          set -a; source .env; set +a
          {flask} run --host 127.0.0.1 --port 5000

        Sign in with OTP at the ADMIN_EMAIL address (mail must be reachable,
        or use a local catcher on :1025).
        """).strip())

def main() -> None:
    p = argparse.ArgumentParser(description="Install a standalone prompt-a-thon competition")
    p.add_argument("--dev", action="store_true", help="Local/dev install in this checkout")
    p.add_argument("--yes", action="store_true", help="Non-interactive")
    p.add_argument("--install-dir", type=Path, default=DEFAULT_INSTALL_DIR)
    p.add_argument("--domain", default="")
    p.add_argument("--base-url", default="")
    p.add_argument("--admin-email", default="")
    p.add_argument("--app-name", default="prompt-a-thon")
    p.add_argument("--tagline", default="AI Literacy Competition")
    p.add_argument("--college-name", default="Your College")
    p.add_argument("--college-url", default="https://example.edu")
    p.add_argument("--mail-provider", default="smtp",
                   choices=["smtp", "brevo_api", "ses_api", "postfix"])
    p.add_argument("--mail-from", default="")
    p.add_argument("--smtp-host", default="")
    p.add_argument("--smtp-port", default="587")
    p.add_argument("--smtp-user", default="")
    p.add_argument("--smtp-password", default="")
    p.add_argument("--smtp-tls", action="store_true", default=True)
    p.add_argument("--no-smtp-tls", action="store_false", dest="smtp_tls")
    p.add_argument("--brevo-api-key", default="")
    p.add_argument("--ses-access-key", default="")
    p.add_argument("--ses-secret-key", default="")
    p.add_argument("--ses-region", default="us-east-1")
    p.add_argument("--skip-apt", action="store_true")
    p.add_argument("--skip-systemd", action="store_true")
    args = p.parse_args()

    if args.dev:
        setup_dev(args)
        return

    ensure_root()
    prompt_if_needed(args)
    if not args.domain and args.base_url:

        args.domain = args.base_url.split("//", 1)[-1].split("/", 1)[0]

    dest: Path = args.install_dir
    if not args.skip_apt:
        apt_install()
    ensure_user(APP_USER)
    ensure_user(CONVERT_USER)

    run(["groupadd", "--system", CONVERT_GROUP], check=False)
    run(["usermod", "-aG", CONVERT_GROUP, CONVERT_USER], check=False)
    run(["usermod", "-aG", CONVERT_GROUP, APP_USER], check=False)

    copy_app(dest)
    install_conversion(dest)
    write_env(dest, args)
    make_venv(dest)
    init_db(dest, args.admin_email)

    chown_tree(dest, APP_USER)
    chown_tree(dest / "conversion", CONVERT_USER)
    os.chmod(dest / "conversion", 0o775)
    os.chmod(dest / ".env", 0o600)

    if not args.skip_systemd:
        install_units(dest)

    print()
    ok(f"prompt-a-thon competition v{APP_VERSION} installed at {dest}")
    print(textwrap.dedent(f"""
    Next steps:
      1. Point DNS for {args.domain} at this server.
      2. Put a reverse proxy in front of 127.0.0.1:{APP_PORT}
         (see deploy/Caddyfile.example or deploy/nginx.example.conf).
      3. Open {args.base_url or ('https://' + args.domain)} and sign in as {args.admin_email}.
      4. In Admin → Settings, configure timeline, tasks, branding, and allowlist.

    Useful commands:
      systemctl status prompt-a-thon prompt-a-thon-convert
      journalctl -u prompt-a-thon -f
      cd {dest} && sudo -u {APP_USER} venv/bin/flask test-mail {args.admin_email}
    """).strip())

if __name__ == "__main__":
    main()
