# Configuration

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Settings come from environment variables, read when the process starts. `setup.py` writes them to `.env`. See `.env.example` for the full list. Admin screens then store event settings in SQLite (`SiteSettings`), which override the display timeline and content without a restart.

## Required in production

`FLASK_ENV=production` checks these at startup:

- `SECRET_KEY`
- `ADMIN_EMAIL`
- Mail credentials for the selected provider. `postfix` uses local port 25 and does not need a password. `smtp` needs `SMTP_USER` and `SMTP_PASSWORD`. `brevo_api` and `ses_api` need their keys and `MAIL_FROM_ADDRESS`.

If `SECRET_KEY` is missing outside production, the process generates a new one in memory. Sessions and encrypted columns will not survive a restart. Set a key.

## Values that matter

| Variable | Role |
| --- | --- |
| `APP_NAME`, `TAGLINE`, `COLLEGE_NAME`, `COLLEGE_URL`, `CONTACT_EMAIL` | Public branding |
| `BASE_URL` | Links in email and the Microsoft redirect. No trailing slash |
| `DATABASE_URL` | SQLAlchemy URL. The installer sets SQLite |
| `UPLOAD_FOLDER` | Absolute paths are used as given. Relative paths are under the app directory |
| `MAX_UPLOAD_MB` | Also sets Flask's max request size. Default 25 |
| `APP_DIR` | Where version and update status files are read |
| `CONVERSION_DIR` | Directory that contains `convert.sock` |
| `INSTANCE_SLUG` | If non-empty, the in-app updater is hidden. Nothing else phones home |
| `TRUST_CLOUDFLARE` | When `true`, `CF-Connecting-IP` and `CF-IPCountry` are trusted. Default `false` |
| `UPDATE_MANIFEST_URL` | Empty disables update checks |
| `RATELIMIT_STORAGE_URI` | Default `memory://`, which is per process |
| `AUDIT_LOG_RETENTION_DAYS` | Used by `flask purge-audit-log`. Default in code is 90 if unset; `.env.example` sets 365 |

## Encryption

Judge notes, ban reasons, and some other free-text columns are encrypted with Fernet. The key is HKDF-SHA256 of `SECRET_KEY` with salt `promptathon-fernet-v1` and info `column-encryption-key`. Email and name stay in plaintext so admins can search them.

Rotating `SECRET_KEY` makes old ciphertext undecryptable. Failed decrypts are stored as empty values in the UI and an error is logged. This salt is specific to this release. A database written with a different salt cannot be read.

## SQLite

For SQLite the engine uses `NullPool`, WAL, a 60 second busy timeout, and foreign keys. The test configuration uses an in-memory database and `StaticPool` so one connection is shared. Other database URLs get a normal connection pool and do not receive SQLite `connect_args`. This release is developed and tested against SQLite. Do not treat another database as supported just because the URL can be changed.
