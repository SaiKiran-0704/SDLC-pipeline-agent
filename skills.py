import os

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

SKILL_DETECTORS = {
    "flask-conventions": lambda content: "from flask import" in content or "Flask(__name__)" in content,
    # Add more skills here later, e.g.:
    # "react-conventions": lambda content: "from react" in content or "import React" in content,
}


def detect_relevant_skills(codebase_context: dict) -> list[str]:
    """Returns the names of every skill whose detector matches the
    current codebase. Deterministic, no LLM call involved."""
    if not codebase_context:
        return []
    all_content = " ".join(codebase_context.get("file_full_contents", {}).values())
    return [name for name, detector in SKILL_DETECTORS.items() if detector(all_content)]


def load_skill_content(skill_names: list[str]) -> str:
    """Reads and concatenates the full content of each matched skill file."""
    parts = []
    for name in skill_names:
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                parts.append(f.read())
    return "\n\n".join(parts)