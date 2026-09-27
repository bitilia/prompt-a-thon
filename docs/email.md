# Email

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Mail is required for email sign-in. Providers:

| `MAIL_PROVIDER` | How it sends |
| --- | --- |
| `smtp` | SMTP, STARTTLS when `SMTP_USE_TLS` is true. Port 465 should use TLS off (implicit SSL) |
| `brevo_api` | HTTPS to Brevo. `MAIL_FROM_ADDRESS` must be a verified sender |
| `ses_api` | HTTPS to Amazon SES. New accounts are often still in the sandbox and can only mail verified recipients |
| `postfix` | Local SMTP on `127.0.0.1:25` |

`flask test-mail you@example.edu` prints the non-secret config, checks DNS and ports or the provider API, then sends a code. `--probe-only` skips the send. `flask test-smtp` is an alias that forces SMTP.

Every outgoing message can include a tracking image. The public route `/email-img-logo.png?pixel=<id>` records `opened_at` on the email log when a client fetches it. That is open tracking. It is not a guarantee someone read the message: many clients block images, and some prefetch them. Admins can see this under email analytics. The image itself is `static/email-img-logo.png`.

Email bodies for logged messages are stored so an admin can open the detail page. Treat that table as personal data.

The maintenance timer does not send mail except when `fail-stuck-submissions` tries to notify people whose processing timed out.
