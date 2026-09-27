# HTTP routes

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Prefixes come from the blueprints. Methods below are the ones declared on the route. A missing method list means GET.

## Public

| Methods | Path |
| --- | --- |
| GET | `/` |
| GET | `/health` |
| GET | `/privacy` |
| GET | `/terms` |
| GET | `/email-icon.svg` (serves the PNG logo) |
| GET | `/email-img-logo.png` |
| GET | `/favicon.ico` |
| GET, POST | `/gdpr/data-request` |

## Auth (`/auth`)

| Methods | Path |
| --- | --- |
| GET, POST | `/auth/login` |
| GET, POST | `/auth/verify` |
| GET | `/auth/logout` |
| GET | `/auth/microsoft` |
| GET | `/auth/microsoft/callback` |

## Participant (`/dashboard`)

Requires a signed-in, non-banned user.

| Methods | Path |
| --- | --- |
| GET | `/dashboard/` |
| GET | `/dashboard/academy` |
| GET | `/dashboard/tasks` |
| GET | `/dashboard/submissions` |
| GET | `/dashboard/submissions/<anon_id>/download` |
| GET | `/dashboard/submissions/<anon_id>/success` |
| GET, POST | `/dashboard/submit` |
| GET | `/dashboard/upload/status/<anon_id>` |
| GET | `/dashboard/files/<int:file_id>/download` |
| POST | `/dashboard/convert-preview` |
| POST | `/dashboard/academy/<module_id>/<item_id>/complete` |
| POST | `/dashboard/academy/<module_id>/<item_id>/uncomplete` |

## Admin (`/admin`)

Most routes require an admin. Submission review allows judges. Update routes 404 when `INSTANCE_SLUG` is set.

| Methods | Path |
| --- | --- |
| GET | `/admin/` |
| GET | `/admin/setup` |
| POST | `/admin/setup/step/<int:step>` |
| GET | `/admin/setup/back/<int:step>` |
| POST | `/admin/setup/skip` |
| GET | `/admin/audit-log` |
| GET | `/admin/users` |
| GET | `/admin/users.csv` |
| GET | `/admin/server` |
| POST | `/admin/users/<int:user_id>/toggle-ban` |
| POST | `/admin/users/<int:user_id>/set-role` |
| POST | `/admin/users/<int:user_id>/set-judge-fields` |
| GET | `/admin/submissions` |
| GET, POST | `/admin/submissions/<anon_id>/review` |
| POST | `/admin/submissions/<anon_id>/terminate` |
| GET | `/admin/update` |
| POST | `/admin/download-update` (always 410; does not fetch a file) |
| POST | `/admin/recheck-updates` |
| GET, POST | `/admin/settings` |
| POST | `/admin/allowlist/add` |
| POST | `/admin/allowlist/remove` |
| GET, POST | `/admin/config/tasks` |
| GET, POST | `/admin/config/academy` |
| GET, POST | `/admin/config/style` |
| POST | `/admin/config/style/reset` |
| GET, POST | `/admin/criteria` |
| GET | `/admin/criteria/view` |
| GET, POST | `/admin/content` |
| GET, POST | `/admin/faqs` |
| GET | `/admin/security` |
| GET | `/admin/security/map-data` |
| GET | `/admin/security/live-log` |
| POST | `/admin/security/settings` |
| GET | `/admin/storage` |
| POST | `/admin/storage/purge-submissions` |
| POST | `/admin/storage/purge-data-requests` |
| POST | `/admin/storage/purge-viewer-cache` |
| POST | `/admin/storage/purge-submission-records` |
| POST | `/admin/fonts/upload` |
| POST | `/admin/fonts/select` |
| POST | `/admin/fonts/delete` |
| GET | `/admin/staff` |
| POST | `/admin/staff/add` |
| POST | `/admin/staff/<member_id>/edit` |
| POST | `/admin/staff/<member_id>/delete` |
| POST | `/admin/staff/reorder` |
| GET, POST | `/admin/email-config` |
| GET | `/admin/email-analytics` |
| GET | `/admin/email-analytics/<int:log_id>/detail` |
| POST | `/admin/email-analytics/purge` |
| GET | `/admin/certificates` |
| GET | `/admin/certificates/preview-pdf` |
| POST | `/admin/certificates/send` |
| POST | `/admin/certificates/history/clear` |
| GET | `/admin/data-requests` |
| POST | `/admin/data-requests/<int:req_id>/resolve` |
| GET | `/admin/data-requests/<int:req_id>/preview` |
| POST | `/admin/data-requests/<int:req_id>/auto-resolve` |
| GET | `/admin/submissions/<anon_id>/view-file` |
| GET | `/admin/files/<int:file_id>/view` |

File-view responses skip the usual content security policy so the browser can render the PDF.
