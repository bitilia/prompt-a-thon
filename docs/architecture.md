# Architecture

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

```text
browser
  → Caddy or nginx (TLS)
    → Gunicorn, 127.0.0.1:8100, user promptathon
        → Flask app (app.py:create_app)
            → SQLite instance/prompt.db
            → uploads/
            → Unix socket → convert daemon, user promptathon-convert
                              → LibreOffice
```

`app.py` builds the Flask app, registers blueprints, CLI commands, security headers, and a few jobs that run on requests (unverified-participant cleanup, at most once a minute per worker).

| Path | Role |
| --- | --- |
| `routes/public.py` | Home, privacy, terms, email tracking image |
| `routes/auth.py` | Email code and Microsoft |
| `routes/participant.py` | Dashboard, submit, academy |
| `routes/admin.py` | Organiser tools |
| `routes/gdpr.py` | Public data request |
| `models.py` | Users, submissions, settings, audit, encryption |
| `models_gdpr.py` | Consent rows and data requests |
| `services/email.py` | All outbound mail and certificate HTML |
| `services/file_pipeline.py` | Upload checks and the socket client |
| `conversion/convert_daemon.py` | The process systemd runs |
| `services/tasks.py`, `services/academy.py` | JSON content |
| `middleware/security.py` | CSP, blocking, connection log |
| `setup.py` | Installer |
| `deploy/` | systemd units and proxy examples |

`services/convert_daemon.py` is not shipped. The daemon that runs is `conversion/convert_daemon.py`.

Gunicorn is started with 2 workers and 4 threads, timeout 120 seconds. Rate-limit storage is memory inside each worker, so a limit is not one global bucket. SQLite WAL is what allows those workers to read while another writes. It is still one file on one machine.

The admin security page can load a world map (`static/world-110m.json`, D3, TopoJSON). Those files are third-party. See `NOTICE`.
