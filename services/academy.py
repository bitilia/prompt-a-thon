import json
import os
import tempfile
from pathlib import Path

from flask import current_app

DEFAULT_ACADEMY = {
    "modules": [
        {
            "id": "module-1",
            "title": "What is Artificial Intelligence?",
            "description": "A beginner-friendly tour of what AI actually is, what it can do, and where it falls short.",
            "items": [
                {
                    "id": "item-1",
                    "type": "video",
                    "title": "AI in 5 Minutes",
                    "url": "https://www.youtube-nocookie.com/embed/2ePf9rue1Ao",
                    "duration": 5,
                    "description": "A short, clear primer on what AI is.",
                },
                {
                    "id": "item-2",
                    "type": "text",
                    "title": "A Brief History of Artificial Intelligence",
                    "url": "https://www.ibm.com/topics/artificial-intelligence",
                    "read_minutes": 8,
                    "description": "How the field of AI emerged and evolved.",
                },
                {
                    "id": "item-3",
                    "type": "video",
                    "title": "How Machine Learning Works",
                    "url": "https://www.youtube-nocookie.com/embed/PeMlggyqz0Y",
                    "duration": 7,
                    "description": "An accessible explanation of training models from data.",
                },
            ],
        },
        {
            "id": "module-2",
            "title": "Talking to AI: The Art of Prompting",
            "description": "Practical techniques for getting useful, accurate output from large language models.",
            "items": [
                {
                    "id": "item-1",
                    "type": "text",
                    "title": "Prompt Engineering 101",
                    "url": "https://learnprompting.org/docs/intro",
                    "read_minutes": 10,
                    "description": "An introduction to crafting effective prompts.",
                },
                {
                    "id": "item-2",
                    "type": "video",
                    "title": "Advanced Prompting Techniques",
                    "url": "https://www.youtube-nocookie.com/embed/dOxUroR57xs",
                    "duration": 12,
                    "description": "Chain-of-thought, role prompting and structured output.",
                },
                {
                    "id": "item-3",
                    "type": "text",
                    "title": "Common Prompting Mistakes",
                    "url": "https://learnprompting.org/docs/basics/pitfalls",
                    "read_minutes": 6,
                    "description": "Pitfalls to avoid when working with AI assistants.",
                },
            ],
        },
        {
            "id": "module-3",
            "title": "AI Ethics & Responsible Use",
            "description": "Bias, accountability and the social impact of AI systems.",
            "items": [
                {
                    "id": "item-1",
                    "type": "video",
                    "title": "The Problem of AI Bias",
                    "url": "https://www.youtube-nocookie.com/embed/59bMh59JQDo",
                    "duration": 9,
                    "description": "How bias enters AI systems and why it matters.",
                },
                {
                    "id": "item-2",
                    "type": "text",
                    "title": "Ethics of Artificial Intelligence",
                    "url": "https://plato.stanford.edu/entries/ethics-ai/",
                    "read_minutes": 15,
                    "description": "A thorough academic overview of the ethical landscape.",
                },
                {
                    "id": "item-3",
                    "type": "text",
                    "title": "Reflection Prompt",
                    "content": (
                        "Think of one ethical issue around AI you have personally "
                        "encountered or read about. Write down: (1) what happened, "
                        "(2) who was affected, (3) what you would do differently, "
                        "and (4) what principle should guide that decision."
                    ),
                    "read_minutes": 5,
                    "description": "A short reflective exercise.",
                },
            ],
        },
    ]
}

def _config_path() -> Path:
    return Path(current_app.root_path) / "config" / "academy.json"

def get_modules() -> list:
    path = _config_path()
    if not path.exists():
        return DEFAULT_ACADEMY["modules"]
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
            current_app.logger.warning("academy.json malformed; using defaults")
            return DEFAULT_ACADEMY["modules"]
        return data["modules"]
    except (OSError, json.JSONDecodeError) as exc:
        current_app.logger.warning("academy.json read failed: %s", exc)
        return DEFAULT_ACADEMY["modules"]

def total_item_count() -> int:
    n = 0
    for module in get_modules():
        items = module.get("items") or []
        n += len(items)
    return n

def _validate_academy(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON must be an object")
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise ValueError("'modules' must be a list")
    seen_mods = set()
    for i, m in enumerate(modules):
        if not isinstance(m, dict):
            raise ValueError(f"Module #{i} must be an object")
        for key in ("id", "title"):
            if not isinstance(m.get(key), str) or not m.get(key).strip():
                raise ValueError(f"Module #{i} missing required string field: {key}")
        mid = m["id"].strip()
        if mid in seen_mods:
            raise ValueError(f"Duplicate module id: {mid}")
        seen_mods.add(mid)
        items = m.get("items")
        if not isinstance(items, list):
            raise ValueError(f"Module {mid}: 'items' must be a list")
        seen_items = set()
        for j, it in enumerate(items):
            if not isinstance(it, dict):
                raise ValueError(f"Module {mid} item #{j} must be an object")
            for key in ("id", "type", "title"):
                if not isinstance(it.get(key), str) or not it.get(key).strip():
                    raise ValueError(
                        f"Module {mid} item #{j} missing required string field: {key}"
                    )
            iid = it["id"].strip()
            if iid in seen_items:
                raise ValueError(f"Module {mid}: duplicate item id: {iid}")
            seen_items.add(iid)
            if it["type"] not in {"video", "text"}:
                raise ValueError(
                    f"Module {mid} item {iid}: type must be 'video' or 'text'"
                )

def save_academy(data: dict) -> None:
    _validate_academy(data)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".academy-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
