# What it is

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

prompt-a-thon is one competition site that you install on your own server. Participants sign in with a one-time email code. Organisers configure tasks, dates, branding, and who may register. Judges score submissions. Admins can issue certificates and handle data requests.

It is a single-instance app. There is no control plane in this repository, and the app does not call [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com) by itself. That site is the public project page. Update checks run only if you set `UPDATE_MANIFEST_URL`.

## What a fresh install contains

- A Flask application served by Gunicorn on `127.0.0.1:8100` in production
- SQLite database at `instance/prompt.db`
- Sample tasks in `config/tasks.json` (Latin, economics, and product design exercises that ask people to use AI)
- A sample mark scheme in `config/criteria.json` (report quality, use of AI, product quality)
- Sample academy modules in `config/academy.json` and `services/academy.py`, including links to third-party pages this project does not operate
- Default public name `prompt-a-thon` and tagline `AI Literacy Competition`, both changeable

The sample tasks are starter content. They are not a finished event, and they are not a single prompt-engineering exam.

## Roles

| Role | What they can do |
| --- | --- |
| Participant | Sign in, read tasks and academy material, submit work |
| Judge | Review submissions and enter the score fields assigned to them |
| Admin | Everything a judge can do, plus users, settings, mail, security, certificates, and data requests |

The address in `ADMIN_EMAIL` is promoted to admin on sign-in and by `flask create-admin`.
