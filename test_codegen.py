import json
from dev_agent import generate_code_for_file
from db import get_codebase_file

current = get_codebase_file("33436a1b", "app.py")
file_plan = {
    "path": "app.py",
    "purpose": "Integrates routing changes to allow creating tasks with high priority, toggling the high priority flag, and filtering listings.",
    "key_functions_or_classes": ["index", "add_todo", "toggle_priority"]
}

result = generate_code_for_file(file_plan, current, "Todo app: adding high-priority marking and filtering")
print(json.dumps(result, indent=2))