# Flask Conventions

Use snake_case for all variable, function, and route names.

Route handlers should be small — delegate real logic to model methods
where possible, not inline in the route function.

Database queries belong in the route or a service layer, never inside
Jinja2 templates.

Always validate/sanitize request.form and request.args before using
them — don't trust user input directly in queries.

New routes should follow the existing RESTful-ish naming pattern already
used in the codebase (e.g. /toggle_priority/<id>, not a query-string
based alternative).