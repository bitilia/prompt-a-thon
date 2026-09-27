# Files and conversion

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Participant files land in `UPLOAD_FOLDER`. The web app and the converter do not share a directory for the conversion itself. The web process sends DOCX bytes over a Unix socket. The daemon, running as `promptathon-convert`, writes only inside its own temp directory and returns PDF bytes.

Protocol:

```text
CONVERT <job_id> <byte_count>\n
<docx bytes>
```

Reply is `OK <job_id> <byte_count>` plus PDF bytes, or `ERROR <job_id> <message>`.

The daemon runs LibreOffice Writer headless (`--convert-to pdf:writer_pdf_Export`) with a 90 second timeout and a small concurrency limit. `pandoc` is not part of this path. WeasyPrint is not part of this path. WeasyPrint is used for certificate PDFs.

After conversion, or for a PDF uploaded directly, the app:

- rejects tiny or oversized files and files that do not start with `%PDF`
- rewrites the PDF with pypdf when it can, because some generators trip qpdf
- opens it with pikepdf, strips actions that can launch code, and clears document metadata

Preview helpers in `viewer_workers/` can read CSV, DOCX, and XLSX for admin viewing. They are separate from the submission store.

The admin server page reports whether `prompt-a-thon-convert` is active and whether the socket accepts a connection. Gunicorn writes `logs/error.log` and `logs/access.log`. The Flask logger also writes `logs/app.log`. The server page shows `error.log` when that file exists, otherwise `app.log`.
