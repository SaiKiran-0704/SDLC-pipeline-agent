import ast
import json
from dotenv import load_dotenv
from google import genai
from google.genai import types
from jinja2 import Environment, TemplateSyntaxError

load_dotenv()
client = genai.Client()

QA_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_status": {"type": "string", "enum": ["pass", "fail"]},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "severity": {"type": "string", "enum": ["critical", "warning"]},
                    "description": {"type": "string"}
                },
                "required": ["file", "severity", "description"]
            }
        }
    },
    "required": ["overall_status", "issues"]
}

QA_SYSTEM_PROMPT = """You are a senior code reviewer checking a set of
generated file changes BEFORE they are pushed as a pull request.

You will be given the full content of every changed file together. Your job
is specifically to catch cross-file mismatches that a single-file review
would miss — for example:

- A template or route referencing a model field name that doesn't actually
  exist in the model file (e.g. `todo.priority` when the model defines
  `is_high_priority`).
- A route name or URL referenced in one file that doesn't match what's
  defined in another.
- Logic that assumes a field, table, or function exists that was never
  actually defined anywhere in the provided files.

Do NOT flag pure style preferences. Do NOT re-check syntax — that has
already been verified separately. Focus only on real correctness issues
that would cause a bug or crash at runtime because of a mismatch between
files.

A "critical" issue means: this will cause visibly wrong or broken behavior.
A "warning" means: this looks risky or worth a human double-checking, but
might be intentional.

If everything is consistent, return overall_status "pass" with an empty
issues list.
"""


def check_syntax(path: str, content: str) -> dict:
    """Runs a real parser against the file — no LLM involved. Either it
    parses or it doesn't."""
    if path.endswith(".py"):
        try:
            ast.parse(content)
            return {"valid": True, "error": None}
        except SyntaxError as e:
            return {"valid": False, "error": f"Python syntax error: {e}"}

    if path.endswith(".html"):
        try:
            Environment().parse(content)
            return {"valid": True, "error": None}
        except TemplateSyntaxError as e:
            return {"valid": False, "error": f"Jinja2 syntax error: {e}"}

    # Extensions we don't have a real parser for — skip, don't fail
    return {"valid": True, "error": None}


def run_syntax_checks(codegen_output: list[dict]) -> list[dict]:
    """Runs check_syntax on every successfully-generated file. Returns the
    codegen_output list with a 'syntax' key added to each item."""
    results = []
    for file_result in codegen_output:
        if file_result.get("status") != "ok":
            results.append(file_result)
            continue
        syntax = check_syntax(file_result["path"], file_result["updated_content"])
        results.append({**file_result, "syntax": syntax})
    return results


def generate_semantic_review(files_for_review: list[dict]) -> dict:
    """Sends all syntax-valid files together to the LLM for cross-file
    consistency review."""
    if not files_for_review:
        return {"overall_status": "pass", "issues": []}

    files_text = "\n\n".join(
        f"--- {f['path']} ---\n{f['updated_content']}"
        for f in files_for_review
    )
    contents = f"Here are the changed files:\n\n{files_text}"

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=QA_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=QA_SCHEMA,
            http_options=types.HttpOptions(timeout=60_000)
        )
    )
    if not response.text:
        raise ValueError("QA agent returned an empty response")
    return json.loads(response.text)


def run_qa(codegen_output: list[dict]) -> dict:
    """Full QA pass: syntax check every file first (free, deterministic),
    then send only the syntax-valid ones to the LLM for semantic review."""
    checked = run_syntax_checks(codegen_output)

    syntax_issues = [
        {"file": f["path"], "severity": "critical", "description": f["syntax"]["error"]}
        for f in checked
        if f.get("syntax") and not f["syntax"]["valid"]
    ]

    valid_files = [
        f for f in checked
        if f.get("status") == "ok" and f.get("syntax", {}).get("valid", True)
    ]

    semantic_result = generate_semantic_review(valid_files)

    all_issues = syntax_issues + semantic_result.get("issues", [])
    overall_status = "fail" if any(i["severity"] == "critical" for i in all_issues) else "pass"

    return {
        "overall_status": overall_status,
        "issues": all_issues,
        "files_checked": [f["path"] for f in checked if f.get("status") == "ok"]
    }