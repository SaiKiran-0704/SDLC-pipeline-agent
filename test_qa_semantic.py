"""Second QA test — this time the Jinja2 syntax is fixed, so the file
should pass the syntax layer and actually reach the LLM. Only the
field-name mismatch remains: index.html checks todo.priority, but
models.py defines is_high_priority. This isolates the semantic layer."""

import json
from qa_agent import run_qa

fake_models_py = """from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Todo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    done = db.Column(db.Boolean, default=False)
    is_high_priority = db.Column(db.Boolean, default=False)
"""

# Syntax is now valid Jinja2 — only bug left is the field name mismatch
fake_index_html = """<ul>
{% for todo in todos %}
  <li>
    {{ todo.title }}
    {% if todo.priority %}
      <span class="badge">High Priority</span>
    {% else %}
      <span>Normal</span>
    {% endif %}
  </li>
{% endfor %}
</ul>
"""

codegen_output = [
    {"path": "models.py", "status": "ok", "is_new_file": False, "updated_content": fake_models_py},
    {"path": "templates/index.html", "status": "ok", "is_new_file": False, "updated_content": fake_index_html},
]

result = run_qa(codegen_output)
print(json.dumps(result, indent=2))