# Contributing

The wiki is [docs/README.md](docs/README.md). The project site is [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com).

## Tests

From a virtualenv with `requirements-dev.txt` installed:

```bash
pytest
```

The tests use an in-memory SQLite database. They do not send email or start LibreOffice.

## Source style in this repository

Python, HTML, CSS, and the project's own JavaScript do not contain comments. Describe behaviour in `docs/` instead. Do not add comments when you change code.

Do not commit `.env`, `instance/`, `uploads/`, or `venv/`.

Third-party files in `static/vendor/` keep their upstream licence headers. Do not strip those.
