"""Standalone QA test — no pipeline run needed. Feeds run_qa() the same two
bugs we found by hand earlier (broken Jinja2 syntax + a field-name mismatch
between models.py and index.html) and prints exactly what QA catches."""

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

# Deliberately broken: wrong field name (todo.priority instead of
# is_high_priority) AND malformed Jinja2 syntax ({% else } / {% endfor })
fake_index_html = """<ul>
{% for todo in todos %}
  <li>
    {{ todo.title }}
    {% if todo.priority %}
      <span class="badge">High Priority</span>
    {% else }
      <span>Normal</span>
    {% endif %}
  </li>
{% endfor }
</ul>
"""

codegen_output = [
    {"path": "models.py", "status": "ok", "is_new_file": False, "updated_content": fake_models_py},
    {"path": "templates/index.html", "status": "ok", "is_new_file": False, "updated_content": fake_index_html},
]

result = run_qa(codegen_output)
print(json.dumps(result, indent=2))