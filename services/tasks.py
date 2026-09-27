import json
import os
import tempfile
from pathlib import Path

from flask import current_app

DEFAULT_TASKS = {
    "tasks": [
        {
            "id": "task-1",
            "title": "Latin Through AI",
            "description": (
                "Choose a short Latin passage, inscription, or historical text "
                "and produce a translation and analysis of its meaning, grammar, "
                "and cultural context. Compare your interpretation with at "
                "least one academic or classroom source, then evaluate where "
                "your final response was accurate, misleading, or incomplete. "
                "You should demonstrate effective use of AI throughout the task "
                "and briefly reflect on how your interaction with AI influenced "
                "the quality and development of your work."
            ),
            "difficulty": "Latin",
            "format": "Written report or annotated slide deck",
            "max_words": 800,
            "tags": [
                "translation",
                "language-analysis",
                "classics",
                "ai-assisted-learning",
            ],
            "links": [],
        },
        {
            "id": "task-2",
            "title": "AI Market Simulation",
            "description": (
                "Investigate a simple economic scenario such as inflation, "
                "supply and demand changes, taxation, or pricing strategy and "
                "present your findings using data, predictions, explanations, "
                "graphs, or tables where appropriate. Reflect on how your use "
                "of AI influenced the quality, accuracy, and development of "
                "your economic analysis while focusing primarily on producing "
                "a clear and well-reasoned investigation."
            ),
            "difficulty": "Economics",
            "format": "Reflective report with screenshots",
            "max_words": 1000,
            "tags": ["economics", "simulation", "analysis", "ai-tools"],
            "links": [],
        },
        {
            "id": "task-3",
            "title": "AI-Assisted Product Design",
            "description": (
                "Design a simple digital product or prototype using a CAD or "
                "modelling tool such as Tinkercad, Fusion 360, or similar "
                "software. Document your development process from concept to "
                "final outcome and explain how AI influenced your workflow, "
                "decision-making, and problem-solving during the project."
            ),
            "difficulty": "DET",
            "format": "Design portfolio or development log",
            "max_words": 1200,
            "tags": ["3d-design", "cad", "problem-solving", "design-process"],
            "links": [],
        },
    ]
}

def _config_path() -> Path:
    return Path(current_app.root_path) / "config" / "tasks.json"

def load_tasks() -> dict:
    path = _config_path()
    if not path.exists():
        return DEFAULT_TASKS
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            current_app.logger.warning("tasks.json malformed; falling back to defaults")
            return DEFAULT_TASKS
        return data
    except (OSError, json.JSONDecodeError) as exc:
        current_app.logger.warning("tasks.json read failed: %s", exc)
        return DEFAULT_TASKS

def get_task_list() -> list:
    return load_tasks().get("tasks", [])

def get_task(task_id: str) -> dict | None:
    if not task_id:
        return None
    for t in get_task_list():
        if t.get("id") == task_id:
            return t
    return None

def _validate_tasks(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("Top level must be an object")
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError('Top level must contain a "tasks" array')
    seen = set()
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise ValueError(f"Task #{i + 1} must be an object")
        for key in ("id", "title", "description"):
            if not t.get(key) or not isinstance(t.get(key), str):
                raise ValueError(f"Task #{i + 1}: '{key}' is required and must be a string")
        if t["id"] in seen:
            raise ValueError(f"Duplicate task id: {t['id']!r}")
        seen.add(t["id"])
        if "links" in t and t["links"] is not None:
            if not isinstance(t["links"], list):
                raise ValueError(f"Task {t['id']}: 'links' must be a list")
            for j, l in enumerate(t["links"]):
                if not isinstance(l, dict):
                    raise ValueError(f"Task {t['id']} link #{j + 1} must be an object")
                if not l.get("label") or not l.get("url"):
                    raise ValueError(f"Task {t['id']} link #{j + 1}: 'label' and 'url' required")

def save_tasks(data: dict) -> None:

    _validate_tasks(data)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="tasks-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
