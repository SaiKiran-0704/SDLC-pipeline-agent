# SDLC Pipeline Agent

A multi-agent pipeline that takes a plain-English feature request and carries
it through Requirements → Design → Dev Planning → Code Generation → QA →
Deploy, with a human approval gate at every stage.

Built with Python, FastAPI, LangGraph, SQLite, and the Gemini API. Deploy
integrates with GitHub over the **Model Context Protocol (MCP)**.

## What it actually does

You describe a feature (optionally pointing it at an existing codebase),
and each stage runs as a real agent call, not a mock:

1. **Requirements** — generates a structured BRD (business objectives,
   functional requirements, assumptions, open questions)
2. **Design** — produces a technical design grounded in the real codebase,
   with explicit traceability back to each requirement
3. **Dev** — an implementation plan (files, purpose, what each should
   contain) — not code yet, deliberately
4. **Codegen** — generates real, complete file content for every file in
   the plan, using the actual current contents of the codebase where they
   exist
5. **QA** — a two-layer check: deterministic syntax validation first
   (`ast.parse` / Jinja2 parsing), then an LLM cross-file review that
   catches mismatches a single-file read would miss
6. **Deploy** — pushes the generated changes to a new branch and opens a
   real pull request on GitHub, via a purpose-built MCP server

Every stage pauses for explicit human approval before the next one runs.
Rejecting a stage with feedback sends it back for regeneration.

## Why Deploy only opens a PR, never merges

This is the one non-negotiable design decision in the whole system: the
GitHub MCP server exposes exactly three tools — `create_branch`,
`write_file`, `open_pull_request` — and no `merge` tool exists anywhere in
the code. No matter what the agent generates, or how confident it is, a
human has to click merge. This isn't a config flag that could be toggled
off under pressure — it's a capability that was never built.

## MCP integration

The Deploy stage is an MCP client. It connects to a standalone MCP server
(`github_mcp_server.py`, built on `fastmcp`) over stdio, and calls its three
tools in sequence for each generated file. The server holds the GitHub
token and makes the real API calls — the pipeline itself never touches
GitHub credentials directly.

## Status

Requirements → Design → Dev → Codegen → Deploy are fully working end to
end, including a real, tested GitHub push-and-PR flow. QA is implemented
with syntax + semantic checks. Ongoing work: richer UI feedback for QA and
Deploy results, persistent storage beyond SQLite for multi-user scenarios,
observability, and eval coverage.

## Running it

```bash
pip install -r requirements.txt
uvicorn api:app --reload
```

Open `static/index.html` in a browser, describe a request, optionally
upload a codebase zip, and click Start pipeline.