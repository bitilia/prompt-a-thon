# Participants, tasks, and submissions

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Signed-in participants use `/dashboard`.

## Tasks

Tasks load from `config/tasks.json`, and admins can replace that file from the tasks screen. Each task has an id, title, description, a `difficulty` label (in the sample data this is a subject, not a level), a format, a word guide, and tags.

There is no separate “pick a task before you may submit” step. The submit form selects the task, and that choice is stored on `TaskSelection` so admins can see it.

Registration and submission windows are dates in `SiteSettings`, with an override that forces them open or closed. If no dates are set, the window is treated as open.

## Submitting

Accepted types are controlled by the admin setting `accepted_files`:

- `pdf` — PDF only
- `pdf_docx` — PDF and DOCX, shown only when LibreOffice is on `PATH` and the conversion socket exists
- `none` — uploads hidden

Uploads are checked for size (`MAX_UPLOAD_MB`, default 25) and for a file signature (`%PDF` or a ZIP local header for DOCX). DOCX bytes are sent to the conversion daemon and the stored artefact is a PDF. See [Files and conversion](files.md).

Submission statuses used in the database are `submitted`, `under_review`, `reviewed`, and `disqualified`. A processing timeout of 15 minutes, run by the maintenance timer, marks stuck rows `failed`.

## Academy progress

Academy completion is optional and stored per user. It is not a gate on submission unless you tell people that in your own event rules. The software does not enforce “finish the academy first”.
