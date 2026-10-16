"""FastAPI backend + dashboard.   Run: uvicorn app:app --reload"""
import os
import shutil
import tempfile

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import agent
import tools

app = FastAPI(title="AI Business Workflow Automation Agent")
RUNS: dict[str, agent.AgentRun] = {}


def _get_run(run_id: str):
    """In-memory first, then disk (so runs survive a server restart)."""
    run = RUNS.get(run_id)
    if run is None:
        run = agent.AgentRun.load(run_id)
        if run is not None:
            RUNS[run_id] = run
    return run


class RunRequest(BaseModel):
    goal: str


class SourceRequest(BaseModel):
    path: str


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/api/dashboard")
def dashboard():
    summary = tools.summarize_by_department()
    overdue = tools.get_overdue_tasks()
    listing = tools.read_tasks()
    for r in (summary, overdue, listing):
        if "error" in r:
            raise HTTPException(400, r["error"])
    return {"summary": summary["departments"], "overdue": overdue, "tasks": listing["tasks"]}


@app.get("/api/bottlenecks")
def bottlenecks():
    result = tools.analyze_bottlenecks()
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.get("/api/source")
def get_source():
    """Active Excel file + last-modified time; the dashboard polls this to stay live."""
    return tools.file_status()


@app.post("/api/source")
def set_source(req: SourceRequest):
    result = tools.set_tasks_path(req.path)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.delete("/api/source")
def clear_source():
    tools.reset_tasks_path()
    return tools.file_status()


@app.post("/api/upload")
def upload(file: UploadFile):
    suffix = os.path.splitext(file.filename or "")[1].lower()
    if suffix not in (".xlsx", ".csv"):
        raise HTTPException(400, "Upload an .xlsx or .csv file")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
    try:
        df = tools.load_tasks(tmp.name)  # validate before replacing the real tracker
    except Exception as e:
        os.unlink(tmp.name)
        raise HTTPException(400, f"Could not read file: {e}")
    target = tools.tasks_path()
    if suffix == ".csv":
        df["deadline"] = df["deadline"].dt.date
        target = os.path.splitext(target)[0] + ".xlsx"
        df.to_excel(target, index=False)
    else:
        shutil.move(tmp.name, target)
    return {"rows": int(len(df)), "saved_to": target}


@app.post("/api/runs")
def start_run(req: RunRequest):
    if not req.goal.strip():
        raise HTTPException(400, "Goal cannot be empty")
    run = agent.run_agent(req.goal.strip())
    RUNS[run.id] = run
    return run.to_dict()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = _get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run.to_dict()


@app.post("/api/runs/{run_id}/actions/{action_id}/{decision}")
def decide(run_id: str, action_id: str, decision: str):
    run = _get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "Decision must be 'approve' or 'reject'")
    try:
        action = run.approve(action_id) if decision == "approve" else run.reject(action_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"action": action, "run": run.to_dict()}
