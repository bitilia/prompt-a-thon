# Judging and criteria

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Judges and admins review submissions at `/admin/submissions`. A judge who opens an admin-only page is sent to the submissions list. A participant receives 403.

Each submission has three integer fields: `score1`, `score2`, and `score3`. An admin can limit a judge to a subset of those fields (`judge_score_fields`). The sample mark scheme in `config/criteria.json` is three criteria (report quality out of 5, use of AI out of 10, result quality out of 5). The scheme is markdown shown to judges. It is not automatically added up into the three score columns. Organisers decide how the columns map to the scheme.

Notes stored on a review are encrypted at rest. See [Configuration](configuration.md).

Admins can disqualify a submission. That state is `disqualified`.

The public site does not publish scores. Results, if you announce them, are a manual step (email, certificates, or your own page text).
