# Security

prompt-a-thon is a self-hosted competition app. You operate the server, the database, and the mail account.

## Reporting a vulnerability

Write to the maintainers through [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com). Include the version in `VERSION`, the route, and what you observed. Please do not open a public issue that contains an exploit.

## Deployment expectations

- Gunicorn listens on `127.0.0.1:8100`. TLS belongs on the reverse proxy.
- `PROXY_FIX_X_FOR` must match the proxies you actually run.
- `TRUST_CLOUDFLARE` stays false unless Cloudflare is that proxy.
- `SECRET_KEY` stays in `.env` with mode `0600`.
- `UPDATE_MANIFEST_URL` stays empty unless you want a version comparison against an HTTPS manifest you control. The app does not download or run release files.

More detail is in [docs/security.md](docs/security.md).
