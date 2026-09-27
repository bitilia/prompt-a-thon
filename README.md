# prompt-a-thon

## This project used to be distributed under a different name. It is no longer being actively maintained.

Self-hosted competition app: registration, email one-time codes, optional Microsoft sign-in, tasks, PDF and DOCX submissions, judging, certificates, and an admin panel.

The project site is [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com). The documentation in this repository is the [wiki](docs/README.md).

This repository is the installable app. It does not call the project site unless you set `UPDATE_MANIFEST_URL` yourself.

## Requirements

- Ubuntu or Debian for a production install
- Python 3.12 or newer
- A hostname and a reverse proxy (Caddy or nginx)
- Outbound email (SMTP, Brevo, Amazon SES, or local Postfix)
- LibreOffice Writer if participants will upload DOCX (the installer installs it)

## Production install

```bash
sudo python3 setup.py
```

Non-interactive flags are documented in [docs/install.md](docs/install.md). The installer uses `/opt/prompt-a-thon`, the system user `promptathon`, and these units:

| Unit | Role |
| --- | --- |
| `prompt-a-thon.service` | Gunicorn on `127.0.0.1:8100` |
| `prompt-a-thon-convert.service` | DOCX to PDF daemon |
| `prompt-a-thon-jobs.timer` | Cleanup every 15 minutes |

Put a proxy in front of port 8100. Examples: `deploy/Caddyfile.example`, `deploy/nginx.example.conf`.

Sign in as the admin email, then set tasks, dates, the allowlist, and branding under Admin.

## Local development

```bash
python3 setup.py --dev
```

Then run the conversion daemon and `flask run` as the installer prints. Tests:

```bash
python -m venv venv
venv/bin/pip install -r requirements-dev.txt
venv/bin/pytest
```

On Windows, `venv\Scripts\pip` and `venv\Scripts\pytest`.

## Layout

```text
app.py              Flask application and CLI
config.py           Environment settings
models.py           Database models
routes/             Auth, participant, admin, data requests, public pages
services/           Email, files, tasks, academy
conversion/         DOCX to PDF daemon
templates/ static/  UI
config/             Sample tasks, criteria, and academy JSON
deploy/             systemd units and proxy examples
setup.py            Installer
docs/               Wiki
```

## Day-2

```bash
systemctl status prompt-a-thon prompt-a-thon-convert
journalctl -u prompt-a-thon -f
cd /opt/prompt-a-thon
sudo -u promptathon venv/bin/flask test-mail you@example.edu
```

Back up `instance/` and `uploads/`. Keep `.env` secret. `SECRET_KEY` encrypts some columns. Details: [docs/operations.md](docs/operations.md) and [docs/limitations.md](docs/limitations.md).

## Licence

Apache License 2.0. See `LICENSE` and `NOTICE`.
