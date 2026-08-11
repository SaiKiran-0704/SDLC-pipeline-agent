from dotenv import load_dotenv
from google import genai
from google.genai import types
import json

load_dotenv()
client = genai.Client()
DEV_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "purpose": {"type": "string"},
                    "implements_component": {"type": "string"},
                    "key_functions_or_classes": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["path", "purpose", "implements_component", "key_functions_or_classes"]
            }
        },
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "implementation_risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["title", "summary", "files", "dependencies", "implementation_risks", "open_questions"]
}
DEV_SYSTEM_PROMPT = """You are a senior software engineer creating an
IMPLEMENTATION PLAN — not code — from a technical design document.

Rules:
1. Do NOT write actual code. List files, their purpose, and what they'd
   contain at a high level (function/class names, not implementations).
2. Every file must reference which design component it implements, using
   the component's exact name from the design (implements_component field).
3. List real dependencies (libraries/packages) implied by the design's
   tech_stack_suggestions — don't invent unrelated ones.
4. implementation_risks are about building this specifically — integration
   complexity, tricky edge cases, things likely to go wrong during
   development. Not business or architecture risks (those live upstream).
5. If the design leaves something genuinely ambiguous for implementation
   (e.g. exact framework conventions, folder structure standards), raise
   it in open_questions rather than guessing.
"""
MAX_RETRIES = 2

def generate_dev_plan_json(design_data: dict, feedback: str | None = None) -> dict:
    """Takes a validated Design dict, returns a validated implementation plan dict."""
    design_as_text = json.dumps(design_data, indent=2)
    contents = f"Here is the technical design:\n\n{design_as_text}"
    if feedback:
        contents += f"\n\nRevision feedback from reviewer: {feedback}\nPlease regenerate the plan, incorporating this feedback."

    last_error = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=DEV_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=DEV_PLAN_SCHEMA,
                    http_options=types.HttpOptions(timeout=60_000)
                )
            )
            if not response.text:
                raise ValueError("Model returned an empty response")
            return json.loads(response.text)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[attempt {attempt}] invalid JSON: {e}")
            last_error = e
            contents += f"\n\nYour previous response was invalid JSON: {e}\nReturn ONLY valid JSON."
        except Exception as e:
            print(f"[attempt {attempt}] request failed: {e}")
            last_error = e

    raise RuntimeError(f"Dev agent failed after {MAX_RETRIES + 1} attempts: {last_error}")
CODE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "is_new_file": {"type": "boolean"},
        "updated_content": {"type": "string"}
    },
    "required": ["path", "is_new_file", "updated_content"]
}

CODE_SYSTEM_PROMPT = """You are a senior software engineer making a real,
minimal, correct code change to an existing file, based on an approved
implementation plan.

Rules:
1. Return the COMPLETE new content of the file — not a diff, not a snippet.
2. Preserve all existing functionality and style — only add/modify what the
   plan specifically calls for in this file.
3. If this is a genuinely new file (is_new_file=true), write it consistent
   with the existing codebase's conventions shown to you.
4. Do not explain your changes in prose — return only the file content.
"""


def generate_code_for_file(file_plan: dict, current_content: str | None, design_summary: str, feedback: str | None = None, skill_content: str | None = None) -> dict:
    """Generates real, complete file content for one file from the Dev plan.
    feedback (e.g. from a failed QA pass) and skill_content (project
    conventions from a matched Skill) are both optional and, when present,
    appended to the prompt."""
    is_new = current_content is None
    contents = (
        f"Design context:\n{design_summary}\n\n"
        f"File to change: {file_plan['path']}\n"
        f"Purpose of this change: {file_plan['purpose']}\n"
        f"Should contain: {', '.join(file_plan['key_functions_or_classes'])}\n\n"
    )
    if is_new:
        contents += "This is a NEW file — write it from scratch, consistent with the codebase's existing style."
    else:
        contents += f"CURRENT file content:\n```\n{current_content}\n```\n\nModify this file per the plan above. Return the complete updated file."

    if skill_content:
        contents += f"\n\nFollow these project conventions:\n{skill_content}"

    if feedback:
        contents += f"\n\nIMPORTANT — fix these issues found by QA:\n{feedback}"

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=CODE_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=CODE_SCHEMA,
            http_options=types.HttpOptions(timeout=60_000)
        )
    )
    if not response.text:
        raise ValueError(f"Empty response generating code for {file_plan['path']}")
    return json.loads(response.text)


def generate_all_code(dev_plan: dict, design_data: dict, codebase_id: str, feedback: str | None = None, skill_content: str | None = None) -> list[dict]:
    """Runs generate_code_for_file for every file in the plan, pulling real
    current content from the DB where it exists. Returns a list of results,
    one per file — a partial failure on one file doesn't stop the others.
    feedback and skill_content, if provided, are passed to every file."""
    from db import get_codebase_file

    design_summary = f"{design_data['title']}: {design_data['architecture_overview']}"
    results = []

    for file_plan in dev_plan["files"]:
        current_content = get_codebase_file(codebase_id, file_plan["path"]) if codebase_id else None
        try:
            code_result = generate_code_for_file(
                file_plan, current_content, design_summary,
                feedback=feedback, skill_content=skill_content
            )
            results.append({"path": file_plan["path"], "status": "ok", **code_result})
        except Exception as e:
            results.append({"path": file_plan["path"], "status": "failed", "error": str(e)})

    return results