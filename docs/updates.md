# Updates

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

This repository does not ship an automatic updater that contacts [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com).

If `UPDATE_MANIFEST_URL` is empty, `flask check-update-manifest` and the admin Updates page record status `unconfigured` and do not open a network connection.

If you set `UPDATE_MANIFEST_URL` to an HTTPS JSON document, the checker compares `instance/version.json` with `releases[]` in that document. A release object may include `version`, `status` (`latest`, `beta`, `ok`, `critically_outdated`), `notes`, and `date`. `download_url` is ignored and is not stored.

The app does not download release files and does not print a shell command for them. HTTP URLs are refused, redirects are not followed, and the body is capped. Upgrade by installing a new tree yourself from [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com). `POST /admin/download-update` answers `410` and does not fetch anything.

If `INSTANCE_SLUG` is set, the Updates routes return 404 and the banner is hidden. Use that when you roll out code yourself and do not want the panel. Setting the slug does not register the instance with any external service.

The installed version string is whatever `setup.py` wrote from the `VERSION` file at install time. Editing `VERSION` in a git checkout does not change an already installed `instance/version.json`.
