# Limits and honest gaps

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

- One competition per install. There is no multi-tenant control plane in this repository.
- The database that is installed and tested is SQLite on one machine. Concurrent writes are queued. It is not a horizontally scaled app.
- Production steps assume Debian or Ubuntu, systemd, and a reverse proxy. `setup.py --dev` can prepare a checkout on Windows. The conversion socket and systemd units are the Linux deployment.
- Sign-in needs working email. There is no password and no break-glass login if mail is down, other than Microsoft if you configured it.
- Rate limits are in-memory and split per Gunicorn worker.
- Country restrictions work only with `TRUST_CLOUDFLARE=true`, because the country code is the Cloudflare header. IP blocks use that header only when the same flag is on. Otherwise they use the address after `ProxyFix`.
- The admin “DOCX available” switch is true only when LibreOffice is on `PATH` and `convert.sock` exists. A stopped daemon disables the DOCX option even if LibreOffice is installed.
- Sample tasks, criteria, and academy modules are placeholders, and several academy links are third-party pages.
- Scores are three integers plus a markdown mark scheme. The app does not compute a total or a rank.
- Certificates require WeasyPrint and its system libraries.
- Email open tracking is on for messages that include the pixel. It under-counts and can false-trigger.
- Privacy and terms pages shipped in `templates/static_pages/` are starter text. They mention the ICO as a general UK reference on the privacy page. They are not legal advice and they may not match your institution.
- Participant accounts that never finish sign-in are deleted after 15 minutes. Staff accounts are kept.
- `SECRET_KEY` rotation destroys access to encrypted columns. The HKDF salt is `promptathon-fernet-v1`.
- Source files in this repository do not contain comments. Behaviour that used to live in comments is documented here instead.
- Third-party files under `static/vendor/` and the Roboto Mono font keep their own upstream notices. See `NOTICE`.
