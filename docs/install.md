# Install

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Production install is for Ubuntu or Debian with systemd. The installer is `setup.py`. It is not a setuptools package.

## What the installer does

When run as root, `setup.py`:

1. Installs apt packages: Python, build tools, WeasyPrint libraries, qpdf, and LibreOffice Writer
2. Creates system users `promptathon` and `promptathon-convert`
3. Copies the app to `/opt/prompt-a-thon` (change with `--install-dir`)
4. Writes `/opt/prompt-a-thon/.env` with a new `SECRET_KEY` and mode `0600`
5. Creates a virtualenv and installs `requirements.txt`
6. Initialises SQLite and promotes the admin email
7. Installs and enables these units:
   - `prompt-a-thon.service` — Gunicorn on `127.0.0.1:8100`
   - `prompt-a-thon-convert.service` — DOCX to PDF daemon
   - `prompt-a-thon-jobs.timer` — maintenance every 15 minutes

It does not obtain a TLS certificate. Put Caddy or nginx in front of port 8100. Examples are in `deploy/`.

## Interactive

```bash
sudo python3 setup.py
```

## Non-interactive

```bash
sudo python3 setup.py --yes \
  --domain competition.example.edu \
  --admin-email organisers@example.edu \
  --app-name "prompt-a-thon" \
  --college-name "Example College" \
  --mail-provider smtp \
  --smtp-host smtp.example.edu \
  --smtp-user noreply@example.edu \
  --smtp-password 'replace-me'
```

`--mail-provider` accepts `smtp`, `brevo_api`, `ses_api`, or `postfix`.

## Local development

`python3 setup.py --dev` creates a virtualenv in the checkout, writes `.env` if missing, and initialises the database. It does not need root and does not install systemd units. On Windows the virtualenv executables are under `venv\Scripts\`.

LibreOffice must be installed separately if you want DOCX conversion. PDF uploads do not need it. The conversion daemon listens on a Unix socket, which is the production path on Linux.

## After install

1. Point DNS at the server.
2. Reverse-proxy `127.0.0.1:8100` and terminate TLS there.
3. Open the site and sign in as the admin email. A code is sent by the mail provider you configured.
4. In Admin, set the timeline, tasks, allowlist, and branding.

Do not publish port 8100. The app trusts the number of proxy hops in `PROXY_FIX_X_FOR`, `PROXY_FIX_X_PROTO`, and `PROXY_FIX_X_HOST` (default 1).
