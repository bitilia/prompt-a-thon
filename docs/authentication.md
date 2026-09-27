# Authentication

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

There are no passwords. Sign-in is a 6-digit code emailed to the address, or Microsoft when `MICROSOFT_CLIENT_ID` is set.

## Email code

1. `POST /auth/login` with an email address.
2. If the person is new, registration must be open, unless the address is `ADMIN_EMAIL`. The domain must be on the allowlist, unless the mode is `allow_all` or the address is the admin.
3. A code is stored hashed and emailed. Default life is 10 minutes (`OTP_EXPIRY_MINUTES`). Default attempts are 5 (`MAX_OTP_ATTEMPTS`).
4. `POST /auth/verify` signs the person in and sends them to a safe relative `next` path, the admin setup wizard, the admin home, or the participant dashboard.

A banned account is not told that it is banned at the email step. The attempt is audited.

New participant rows are created before the email is sent. If the person never completes sign-in, a participant account with no login and older than 15 minutes is deleted. Admins, judges, and `ADMIN_EMAIL` are not deleted by that job. A failed OTP email can therefore leave a participant row for up to 15 minutes.

## Allowlist

Default mode is `allowlist`. The domain after `@` must match exactly. `student@cs.example.edu` does not match an allowlist entry of `example.edu`. The admin domain is seeded on first database init so the admin can sign in. Change this under Admin settings.

## Microsoft

Optional. The app uses MSAL against `login.microsoftonline.com` and `MICROSOFT_TENANT_ID` (default `common`). The callback is under `/auth/microsoft/callback`. `BASE_URL` must match the redirect URI registered in Microsoft. The same registration and allowlist rules apply, and `ADMIN_EMAIL` is still promoted to admin.

## Sessions

Sessions last 8 hours, are HTTP-only, and use `SameSite=Lax`. The secure cookie flag is on only when `FLASK_ENV=production`. Flask-Login session protection is `strong`. CSRF is on except in the test configuration.
