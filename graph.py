from typing import TypedDict, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
import json
import uuid
import asyncio
import os
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from agent import generate_brd_json
from design_agent import generate_design_json
from dev_agent import generate_dev_plan_json, generate_all_code


class PipelineState(TypedDict):
    user_request: str
    codebase_context: Optional[dict]
    codebase_id: Optional[str]
    brd: Optional[dict]
    design: Optional[dict]
    dev_output: Optional[dict]
    codegen_output: Optional[list]
    qa_output: Optional[dict]
    qa_retry_count: Optional[int]
    qa_feedback: Optional[str]
    deploy_output: Optional[dict]
    approved: Optional[bool]
    feedback: Optional[str]
    user_id: Optional[int]
    github_repo: Optional[str]


# ---------- Real agent nodes ----------

def requirements_node(state: PipelineState) -> PipelineState:
    request_text = state["user_request"]
    if state.get("feedback"):
        request_text = (
            f"{request_text}\n\n"
            f"Revision feedback from reviewer: {state['feedback']}\n"
            f"Please regenerate the BRD, incorporating this feedback."
        )
    brd = generate_brd_json(request_text, codebase_context=state.get("codebase_context"))
    return {"brd": brd}


def design_node(state: PipelineState) -> PipelineState:
    design = generate_design_json(
        state["brd"],
        feedback=state.get("feedback"),
        codebase_context=state.get("codebase_context")
    )
    return {"design": design}


def dev_node(state: PipelineState) -> PipelineState:
    plan = generate_dev_plan_json(state["design"], feedback=state.get("feedback"))

    real_component_names = {c["name"] for c in state["design"]["components"]}
    referenced_names = {f["implements_component"] for f in plan["files"]}

    missing = real_component_names - referenced_names
    unknown = referenced_names - real_component_names

    if missing:
        print(f"⚠️  Design components with NO file implementing them: {sorted(missing)}")
    if unknown:
        print(f"⚠️  Dev plan references components that don't exist in the design: {sorted(unknown)}")
    if not missing and not unknown:
        print("✅ Dev traceability check passed — every design component is implemented, no invented components.")

    return {"dev_output": plan}


def codegen_node(state: PipelineState) -> PipelineState:
    from skills import detect_relevant_skills, load_skill_content
    matched = detect_relevant_skills(state.get("codebase_context"))
    skill_content = load_skill_content(matched) if matched else None

    results = generate_all_code(
        state["dev_output"], state["design"], state.get("codebase_id"),
        feedback=state.get("qa_feedback"),
        skill_content=skill_content
    )
    return {"codegen_output": results}


# ---------- Stub nodes (not built yet) ----------

MAX_QA_RETRIES = 5

def qa_node(state: PipelineState) -> PipelineState:
    from qa_agent import run_qa
    qa_result = run_qa(state["codegen_output"])
    retry_count = state.get("qa_retry_count") or 0

    should_retry = qa_result["overall_status"] == "fail" and retry_count < MAX_QA_RETRIES
    qa_result["retries_used"] = retry_count  # true count of auto-fix attempts already made before this result
    updates = {"qa_output": qa_result, "qa_auto_retry": should_retry}

    if should_retry:
        issues_text = "\n".join(f"- {i['file']}: {i['description']}" for i in qa_result["issues"])
        updates["qa_feedback"] = f"QA found issues that must be fixed:\n{issues_text}"
        updates["qa_retry_count"] = retry_count + 1

    return updates


MCP_SERVER_PATH = os.getenv("MCP_SERVER_PATH", "/Users/saik/Desktop/mcp_server/github_mcp_server.py")

async def _run_deploy(codegen_output: list, github_token: str, github_repo: str) -> dict:
    # Per-user credentials, not the shared env vars — each deploy authenticates
    # as whichever user is actually running the pipeline, against the repo
    # they chose, so the PR lands in THEIR GitHub, not a shared test repo.
    env = {"GITHUB_PAT": github_token, "GITHUB_REPO": github_repo}
    transport = StdioTransport(command="python", args=[MCP_SERVER_PATH], env=env)
    client = Client(transport)

    branch_name = f"pipeline-{uuid.uuid4().hex[:8]}"
    written_files = []
    failed_files = []

    async with client:
        await client.call_tool("create_branch", {"branch_name": branch_name})

        for file_result in codegen_output:
            if file_result.get("status") != "ok":
                continue
            try:
                await client.call_tool("write_file", {
                    "branch_name": branch_name,
                    "file_path": file_result["path"],
                    "content": file_result["updated_content"],
                    "commit_message": f"Update {file_result['path']} via pipeline"
                })
                written_files.append(file_result["path"])
            except Exception as e:
                failed_files.append({"path": file_result["path"], "error": str(e)})

        if not written_files:
            return {
                "status": "failed",
                "branch": branch_name,
                "note": "No files were successfully written — branch exists but is empty. No PR opened.",
                "failed_files": failed_files
            }

        pr_body = f"Automated changes to: {', '.join(written_files)}"
        if failed_files:
            pr_body += f"\n\n⚠️ The following files FAILED to write and are missing from this PR: {', '.join(f['path'] for f in failed_files)}"

        pr_result = await client.call_tool("open_pull_request", {
            "branch_name": branch_name,
            "title": f"Pipeline changes: {branch_name}" + (" (partial)" if failed_files else ""),
            "body": pr_body
        })

    return {
        "status": "partial" if failed_files else "success",
        "branch": branch_name,
        "files_written": written_files,
        "files_failed": failed_files,
        "pull_request": pr_result.data
    }


def deploy_node(state: PipelineState) -> PipelineState:
    codegen_output = state.get("codegen_output") or []
    ok_files = [f for f in codegen_output if f.get("status") == "ok"]

    if not ok_files:
        return {"deploy_output": {"status": "failed", "note": "No successfully generated files to deploy"}}

    user_id = state.get("user_id")
    github_repo = state.get("github_repo")
    if not user_id or not github_repo:
        return {"deploy_output": {
            "status": "failed",
            "note": "No GitHub account or repo selected — log in with GitHub and pick a repo before running this."
        }}

    from auth import get_user_by_id
    user = get_user_by_id(user_id)
    if not user:
        return {"deploy_output": {"status": "failed", "note": "Logged-in user not found — please log in again."}}

    try:
        result = asyncio.run(_run_deploy(codegen_output, user["access_token"], github_repo))
        return {"deploy_output": result}
    except Exception as e:
        return {"deploy_output": {"status": "failed", "error": str(e)}}


# ---------- Generic approval gate factory ----------

def make_approval_node(stage_label: str, data_key: str):
    def node(state: PipelineState) -> PipelineState:
        decision = interrupt({
            "question": f"Approve {stage_label}?",
            "stage": stage_label,
            "data": state[data_key]
        })
        return {"approved": decision["approved"], "feedback": decision.get("feedback")}
    return node


def make_router(retry_node: str, next_node: str):
    def router(state: PipelineState) -> str:
        return next_node if state["approved"] else retry_node
    return router


def qa_approval_node(state: PipelineState) -> PipelineState:
    decision = interrupt({
        "question": "Approve QA (pre-deploy)?",
        "stage": "QA (pre-deploy)",
        "data": state["qa_output"]
    })
    updates = {"approved": decision["approved"], "feedback": decision.get("feedback")}
    # codegen_node reads qa_feedback, not feedback — without this, a human's
    # "Fix with AI" / "Request Changes" click at this stage was silently
    # ignored, and codegen regenerated using stale feedback left over from
    # the last automatic retry instead of the issues actually shown here.
    if not decision["approved"] and decision.get("feedback"):
        updates["qa_feedback"] = decision["feedback"]
    return updates


# ---------- Build the graph ----------

graph_builder = StateGraph(PipelineState)

graph_builder.add_node("requirements", requirements_node)
graph_builder.add_node("approval_requirements", make_approval_node("Requirements (BRD)", "brd"))
graph_builder.add_node("design", design_node)
graph_builder.add_node("approval_design", make_approval_node("Design", "design"))
graph_builder.add_node("dev", dev_node)
graph_builder.add_node("approval_dev", make_approval_node("Development", "dev_output"))
graph_builder.add_node("codegen", codegen_node)
graph_builder.add_node("approval_codegen", make_approval_node("Generated Code", "codegen_output"))
graph_builder.add_node("qa", qa_node)
graph_builder.add_node("approval_qa", qa_approval_node)
graph_builder.add_node("deploy", deploy_node)

graph_builder.add_edge(START, "requirements")
graph_builder.add_edge("requirements", "approval_requirements")
graph_builder.add_conditional_edges("approval_requirements",
    make_router("requirements", "design"),
    {"requirements": "requirements", "design": "design"})

graph_builder.add_edge("design", "approval_design")
graph_builder.add_conditional_edges("approval_design",
    make_router("design", "dev"),
    {"design": "design", "dev": "dev"})

graph_builder.add_edge("dev", "approval_dev")
graph_builder.add_conditional_edges("approval_dev",
    make_router("dev", "codegen"),
    {"dev": "dev", "codegen": "codegen"})

graph_builder.add_edge("codegen", "approval_codegen")
graph_builder.add_conditional_edges("approval_codegen",
    make_router("codegen", "qa"),
    {"codegen": "codegen", "qa": "qa"})

def qa_result_router(state: PipelineState) -> str:
    return "codegen" if state.get("qa_auto_retry") else "approval_qa"

graph_builder.add_conditional_edges("qa", qa_result_router,
    {"codegen": "codegen", "approval_qa": "approval_qa"})

graph_builder.add_conditional_edges("approval_qa",
    make_router("codegen", "deploy"),
    {"codegen": "codegen", "deploy": "deploy"})

graph_builder.add_edge("deploy", END)

conn = sqlite3.connect("checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(conn)
graph = graph_builder.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    user_request = input("Describe your business requirement: ").strip()
    if not user_request:
        print("No request entered. Exiting.")
        exit(1)

    print("\n[graph] running pipeline...\n")
    result = graph.invoke(
        {"user_request": user_request, "codebase_context": None, "codebase_id": None,
         "brd": None, "design": None, "dev_output": None, "codegen_output": None,
         "qa_output": None, "deploy_output": None,
         "approved": None, "feedback": None,
         "user_id": None, "github_repo": None},
        config=config
    )

    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        print(f"\n[APPROVAL NEEDED — {payload['stage']}]\n")
        print(json.dumps(payload["data"], indent=2))

        answer = input(f"\nApprove {payload['stage']}? (yes/no): ").strip().lower()
        if answer == "yes":
            resume_value = {"approved": True, "feedback": None}
        else:
            feedback_text = input("What should change? ").strip()
            resume_value = {"approved": False, "feedback": feedback_text}

        print("\n[graph] resuming...\n")
        result = graph.invoke(Command(resume=resume_value), config=config)

    print("\n[graph] pipeline complete.\n")
    print(json.dumps(result, indent=2))
