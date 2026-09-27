# Data requests

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

`/gdpr/data-request` is public and rate-limited (3 per hour and 10 per day). A person can ask for access, erasure, portability, or rectification. The request is stored and mailed to organisers. It is not fulfilled automatically, except where an admin uses the auto-resolve action on a specific request in the admin UI.

Admins work the queue at `/admin/data-requests`. Exporting users as CSV (`/admin/users.csv`) is audited.

This is a workflow, not a claim that the deployment is compliant. You still need a lawful basis, a privacy notice that matches what you actually store, and a process for the requests. The shipped privacy and terms pages are templates. Read them and replace them before a real event.

What is stored includes account email and name in plaintext, encrypted submission text and notes, unencrypted uploaded files, the audit log, the email log (including HTML and open-tracking timestamps), a short-lived connection log (IP, optional country, path), and certificate records. A `consent_records` table exists and is read on the admin data-request preview. This release never inserts a row into it, and there is no cookie-consent banner.

The connection log is not a full access log of every click. It records at most one row per IP per 30 seconds, plus blocked attempts. Old connection rows are deleted after 48 hours, at most once every 10 minutes per worker.
