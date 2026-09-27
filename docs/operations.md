# Operations

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

```bash
systemctl status prompt-a-thon prompt-a-thon-convert prompt-a-thon-jobs.timer
journalctl -u prompt-a-thon -f
cd /opt/prompt-a-thon
sudo -u promptathon venv/bin/flask test-mail you@example.edu
sudo -u promptathon venv/bin/flask create-admin other@example.edu
```

`FLASK_APP` is `app:create_app`. The jobs unit sets that. Gunicorn calls `app:create_app()` itself.

## Timer

`prompt-a-thon-jobs.timer` starts 2 minutes after boot and then 15 minutes after each run. Each run executes, in order:

- `purge-expired-otps`
- `fail-stuck-submissions` (processing older than 15 minutes becomes failed, then a best-effort email)
- `purge-unverified-users` (participants with no login, older than 15 minutes)
- `purge-audit-log` (older than `AUDIT_LOG_RETENTION_DAYS`)

It does not check for software updates.

## Backups

Copy `instance/` (the SQLite file and its `-wal` / `-shm` siblings if present) and `uploads/`. Copy `.env` and store it separately. Without `SECRET_KEY`, encrypted columns in the backup cannot be read.

Stop the app or use SQLite's backup API if you need a consistent snapshot under write load. Copying the file while Gunicorn is mid-write can produce a partial snapshot even with WAL.

## Lockdown

Admins can turn on a lockdown message. Static files, `/auth/`, `/admin`, and `/health` stay reachable so an admin can sign in and turn it off, and a probe can still see that the process is up. Everyone else sees the lockdown page with HTTP 503.

## Health

`GET /health` runs `SELECT 1` and returns `{"ok": true}` with HTTP 200, or `{"ok": false}` with HTTP 503 if the database query fails. It does not record a connection-log row. Gunicorn recycles each worker after about 1000 requests so a leak in one worker does not stay up for the life of the host.

## Logs

- `journalctl -u prompt-a-thon` for process output
- `/opt/prompt-a-thon/logs/error.log` and `access.log` from Gunicorn
- `/opt/prompt-a-thon/logs/app.log` from the Flask logger

The admin server page tails the Gunicorn error log when it exists.
