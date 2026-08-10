import logging
import time
import uuid
import os
import zipfile
from db import save_codebase_files, get_codebase_file, init_db
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent import generate_brd_json, render_docx
from design_agent import generate_design_json, render_design_docx
from graph import graph
from langgraph.types import Command
from codebase_context import extract_and_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("requirements_agent")

app = FastAPI()
init_db()
brd_store = {}
codebase_store = {}


class RequirementRequest(BaseModel):
    request_text: str

class StartPipelineRequest(BaseModel):
    request_text: str
    codebase_id: str | None = None

class ResumeRequest(BaseModel):
    approved: bool
    feedback: str | None = None


@app.post("/brd")
def create_brd(payload: RequirementRequest):
    start = time.time()
    logger.info(f"REQUEST received | text=\"{payload.request_text[:80]}...\"")
    try:
        data = generate_brd_json(payload.request_text)
    except RuntimeError as e:
        logger.error(f"REQUEST failed | reason={e}")
        raise HTTPException(status_code=502, detail=str(e))

    doc_id = str(uuid.uuid4())[:8]
    output_path = f"outputs/{doc_id}.docx"
    os.makedirs("outputs", exist_ok=True)
    render_docx(data, output_path)
    brd_store[doc_id] = data

    logger.info(f"REQUEST completed | doc_id={doc_id} | took={round(time.time()-start,2)}s")
    return {"doc_id": doc_id, "brd_json": data, "download_url": f"/brd/{doc_id}/download"}


@app.get("/brd/{doc_id}/download")
def download_brd(doc_id: str):
    return FileResponse(f"outputs/{doc_id}.docx", filename=f"{doc_id}.docx")


@app.post("/brd/{doc_id}/design")
def create_design(doc_id: str):
    if doc_id not in brd_store:
        raise HTTPException(status_code=404, detail="No BRD found for that doc_id")
    logger.info(f"DESIGN request received | doc_id={doc_id}")
    start = time.time()
    try:
        design_data = generate_design_json(brd_store[doc_id])
    except RuntimeError as e:
        logger.error(f"DESIGN failed | doc_id={doc_id} | reason={e}")
        raise HTTPException(status_code=502, detail=str(e))

    render_design_docx(design_data, f"outputs/{doc_id}_design.docx")
    logger.info(f"DESIGN completed | doc_id={doc_id} | took={round(time.time()-start,2)}s")
    return {"doc_id": doc_id, "design_json": design_data, "download_url": f"/brd/{doc_id}/design/download"}


@app.get("/brd/{doc_id}/design/download")
def download_design(doc_id: str):
    return FileResponse(f"outputs/{doc_id}_design.docx", filename=f"{doc_id}_design.docx")


def format_interrupt_response(thread_id, result):
    payload = result["__interrupt__"][0].value
    return {"status": "paused", "thread_id": thread_id, "stage": payload["stage"], "data": payload["data"]}


@app.post("/pipeline/start")
def start_pipeline(payload: StartPipelineRequest):
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    logger.info(f"PIPELINE start | thread_id={thread_id} | codebase_id={payload.codebase_id}")

    codebase_context = None
    if payload.codebase_id:
        if payload.codebase_id not in codebase_store:
            raise HTTPException(status_code=404, detail="No codebase found for that codebase_id")
        codebase_context = codebase_store[payload.codebase_id]

    try:
        result = graph.invoke(
                {"user_request": payload.request_text, "codebase_context": codebase_context,
                 "codebase_id": payload.codebase_id,
                 "brd": None, "design": None,
                 "dev_output": None, "codegen_output": None,
                 "qa_output": None, "deploy_output": None,
                 "approved": None, "feedback": None},
                config=config
        )
    except RuntimeError as e:
        logger.error(f"PIPELINE start failed | thread_id={thread_id} | reason={e}")
        raise HTTPException(status_code=502, detail=str(e))

    if "__interrupt__" in result:
        return format_interrupt_response(thread_id, result)
    return {"status": "completed", "thread_id": thread_id, "result": result}


@app.post("/pipeline/{thread_id}/resume")
def resume_pipeline(thread_id: str, payload: ResumeRequest):
    config = {"configurable": {"thread_id": thread_id}}
    logger.info(f"PIPELINE resume | thread_id={thread_id} | approved={payload.approved}")
    resume_value = {"approved": payload.approved, "feedback": payload.feedback}
    try:
        result = graph.invoke(Command(resume=resume_value), config=config)
    except RuntimeError as e:
        logger.error(f"PIPELINE resume failed | thread_id={thread_id} | reason={e}")
        raise HTTPException(status_code=502, detail=str(e))

    if "__interrupt__" in result:
        return format_interrupt_response(thread_id, result)

    doc_id = thread_id[:8]
    os.makedirs("outputs", exist_ok=True)
    render_design_docx(result["design"], f"outputs/{doc_id}_design.docx")
    return {"status": "completed", "thread_id": thread_id, "result": result, "download_url": f"/pipeline/{thread_id}/download"}


@app.get("/pipeline/{thread_id}/download")
def download_pipeline_design(thread_id: str):
    doc_id = thread_id[:8]
    return FileResponse(f"outputs/{doc_id}_design.docx", filename=f"{doc_id}_design.docx")


@app.post("/codebase/upload")
async def upload_codebase(file: UploadFile = File(...)):
    if not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")
    zip_bytes = await file.read()
    if len(zip_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Zip file too large (max 20MB)")
    try:
        context = extract_and_scan(zip_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Unsafe zip contents: {e}")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="File is not a valid zip archive")

    codebase_id = str(uuid.uuid4())[:8]
    codebase_store[codebase_id] = context
    save_codebase_files(codebase_id, context["file_full_contents"])

    logger.info(f"CODEBASE uploaded | codebase_id={codebase_id} | files={len(context['file_tree'])}")
    return {"codebase_id": codebase_id, "file_count": len(context["file_tree"]), "file_tree": context["file_tree"][:50]}


@app.get("/")
def serve_ui():
    return FileResponse("static/index.html")