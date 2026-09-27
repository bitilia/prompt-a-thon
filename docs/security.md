# Security

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

## What the app sets

- CSRF on state-changing forms, except the automated test config
- A content security policy with a per-request script nonce. Inline styles are allowed. `object-src` is `none`
- `X-Frame-Options: SAMEORIGIN`, `nosniff`, a referrer policy, and a permissions policy
- HSTS when the session cookie is marked secure (production)
- Login and verify routes are rate-limited, as are data requests. `POST /admin/download-update` is rate-limited and always refuses.
- Upload type checks and PDF sanitising described in [Files](files.md)
- Open redirects after login are rejected unless the target is a same-site path starting with a single `/`

## What you must do

- Terminate TLS at the proxy. Leave Gunicorn on loopback.
- Keep `PROXY_FIX_*` equal to the number of proxies you actually run. A public Gunicorn plus `PROXY_FIX_X_FOR=1` lets clients spoof the address used for logs and limits.
- Leave `TRUST_CLOUDFLARE` false unless Cloudflare is the proxy in front of you. Those headers are not trustworthy from the open internet. Country blocking does nothing useful until the flag is on, because the country comes only from `CF-IPCountry`.
- The country block message is escaped. It is plain text, not HTML.
- Keep `.env` mode `0600`. `SECRET_KEY` decrypts columns.
- Leave `UPDATE_MANIFEST_URL` empty unless you want a version comparison. The URL must be HTTPS. Redirects are not followed and the body is size-capped. The app never saves or runs a remote update script.
- `GET /health` checks the database and returns JSON. It does not write a connection-log row and it stays available during lockdown so a probe can tell the process is up.
- Uploaded file paths are resolved and must stay inside the upload directory. Font selection must stay inside `static/fonts`.
- Dependency upgrades in `requirements.txt` are ranges. Pin a lockfile before a high-stakes event if you need a byte-for-byte reinstall.

## Reporting

If you find a vulnerability, contact the maintainers through [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com) rather than filing a public exploit. Give the version from `VERSION` and the route involved.
