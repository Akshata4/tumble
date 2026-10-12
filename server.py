"""Multi-tenant HTTP frontend for the same agent loop main.py drives
locally. Each session gets its own directory under sessions/<id>/ and its
own SandboxSession, so concurrent visitors never share a workspace, a
sandbox, or a best-metric -- the only thing shared is the process and the
in-memory SESSIONS dict, which is fine at this scale (per DESIGN.md).

Safety caps enforced here: a per-IP daily cap on starting a run, a 5MB
dataset upload cap, a per-session wall-clock cap, a global daily
sandbox-seconds/LLM-token budget ceiling (budget.py, rate_limit.py), and a
Cloudflare Turnstile bot-check (turnstile.py). Two of DESIGN.md's caps
live elsewhere since they're not server-specific: MAX_EXPERIMENTS and the
per-experiment timeout are enforced in tools.py so the CLI gets them too.
"""

import json
import os
import queue
import threading
import uuid

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())
os.environ["LLM_PROVIDER"] = "wandb"  # Tumble is W&B-only by design, regardless of what's in .env

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent_loop import run_agent  # noqa: E402
from budget import DailyBudget  # noqa: E402
from dataset_preview import build_preview  # noqa: E402
from prompts import build_system_prompt, default_prompt  # noqa: E402
from rate_limit import IpRateLimiter  # noqa: E402
from sandbox_exec import SandboxSession  # noqa: E402
from tools import TOOL_SCHEMAS, Workspace  # noqa: E402
from trajectory import Trajectory  # noqa: E402
from turnstile import verify as verify_turnstile  # noqa: E402

SESSIONS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")

MAX_DATASET_BYTES = 5 * 1024 * 1024
MAX_RUNS_PER_IP_PER_DAY = 3
MAX_SESSION_WALL_CLOCK_SECONDS = 5 * 60

# Placeholder defaults, not derived from a specific cost target -- tune
# once real traffic/cost tolerance is known. Override via env vars.
DAILY_SANDBOX_SECONDS_BUDGET = float(os.environ.get("DAILY_SANDBOX_SECONDS_BUDGET", 3600))
DAILY_LLM_TOKEN_BUDGET = int(os.environ.get("DAILY_LLM_TOKEN_BUDGET", 2_000_000))

app = FastAPI(title="Tumble")

# Wide open for now -- there's no auth yet either, so this isn't the thing
# standing between the app and abuse (the caps above are). Tighten this to
# the real frontend origin once one is deployed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

RATE_LIMITER = IpRateLimiter(MAX_RUNS_PER_IP_PER_DAY)
BUDGET = DailyBudget(DAILY_SANDBOX_SECONDS_BUDGET, DAILY_LLM_TOKEN_BUDGET)


class Session:
    """A session directory looks like:
        sessions/<id>/upload.csv       -- the raw upload, never touched again
        sessions/<id>/workspace/       -- the Workspace: train.py, state.json, .git/
    kept separate so Workspace.init()'s wipe-and-recreate of the workspace
    directory can never delete the upload it's about to copy back in."""

    def __init__(self, session_id: str):
        self.id = session_id
        self.root = os.path.join(SESSIONS_ROOT, session_id)
        self.upload_path = os.path.join(self.root, "upload.csv")
        self.workspace_dir = os.path.join(self.root, "workspace")
        self.target_column: str | None = None
        # created -> dataset_uploaded -> running -> completed / failed
        self.status = "created"
        self.error: str | None = None
        self.events: "queue.Queue[dict | None]" = queue.Queue()
        self.trajectory_path: str | None = None

    def push(self, event: dict) -> None:
        self.events.put(event)


SESSIONS: dict[str, Session] = {}


def _get_session(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@app.post("/sessions")
def create_session() -> dict:
    session_id = uuid.uuid4().hex
    session = Session(session_id)
    os.makedirs(session.root, exist_ok=True)
    SESSIONS[session_id] = session
    return {"session_id": session_id}


@app.post("/sessions/{session_id}/dataset")
async def upload_dataset(session_id: str, target: str = Form(...), file: UploadFile = File(...)) -> dict:
    session = _get_session(session_id)
    if session.status != "created":
        raise HTTPException(status_code=409, detail=f"session already has a dataset (status={session.status})")

    size = 0
    with open(session.upload_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_DATASET_BYTES:
                f.close()
                os.remove(session.upload_path)
                raise HTTPException(status_code=413, detail=f"dataset exceeds the {MAX_DATASET_BYTES} byte limit")
            f.write(chunk)

    try:
        preview = build_preview(session.upload_path, target)
    except ValueError as e:
        os.remove(session.upload_path)
        raise HTTPException(status_code=400, detail=str(e))

    session.target_column = target
    session.status = "dataset_uploaded"
    return {"preview": preview}


class StartRequest(BaseModel):
    metric: str = "accuracy"
    minimize: bool = False
    prompt: str | None = None
    turnstile_token: str | None = None


@app.post("/sessions/{session_id}/start")
def start_session(session_id: str, req: StartRequest, request: Request) -> dict:
    session = _get_session(session_id)
    if session.status != "dataset_uploaded":
        raise HTTPException(
            status_code=409, detail=f"session must have a dataset uploaded before starting (status={session.status})"
        )

    client_ip = request.client.host if request.client else "unknown"
    if not verify_turnstile(req.turnstile_token, client_ip):
        raise HTTPException(status_code=403, detail="bot-check failed")
    if not RATE_LIMITER.check_and_increment(client_ip):
        raise HTTPException(status_code=429, detail=f"daily limit of {MAX_RUNS_PER_IP_PER_DAY} runs per IP reached")
    if not BUDGET.has_budget():
        raise HTTPException(status_code=503, detail="the daily service budget is exhausted; try again tomorrow")

    session.status = "running"
    threading.Thread(target=_run_session, args=(session, req), daemon=True).start()
    return {"status": "running"}


def _run_session(session: Session, req: StartRequest) -> None:
    sandbox = SandboxSession(
        dataset_path=session.upload_path,
        on_log=lambda msg: session.push({"type": "log", "content": msg}),
        on_close=BUDGET.add_sandbox_seconds,
    )
    workspace = Workspace(session.workspace_dir, sandbox, session_id=session.id)
    workspace.init(session.upload_path, session.target_column, req.metric, req.minimize)

    preview = build_preview(session.upload_path, session.target_column)
    system_prompt = build_system_prompt(preview, session.target_column, req.metric, req.minimize)
    prompt = req.prompt or default_prompt(req.metric)

    trajectory = Trajectory()
    session.trajectory_path = trajectory.path
    trajectory.subscribe(session.push)

    try:
        run_agent(
            prompt,
            system_prompt,
            TOOL_SCHEMAS,
            workspace.tool_functions(),
            trajectory,
            max_wall_clock_seconds=MAX_SESSION_WALL_CLOCK_SECONDS,
            budget=BUDGET,
        )
        session.status = "completed"
    except Exception as e:
        session.status = "failed"
        session.error = str(e)
        session.push({"type": "notice", "content": f"run failed: {e}"})
    finally:
        sandbox.close()
        session.push(None)  # sentinel: tells /stream to close the connection


@app.get("/sessions/{session_id}/stream")
def stream_session(session_id: str) -> StreamingResponse:
    session = _get_session(session_id)

    # One queue per session, drained by whichever client reads first -- fine
    # for the one-visitor-per-session product this is being built for, but
    # not a general pub/sub (a second concurrent /stream call would only see
    # whatever events the first one hasn't already consumed).
    def event_source():
        while True:
            event = session.events.get()
            if event is None:
                yield "event: done\ndata: {}\n\n"
                return
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@app.get("/sessions/{session_id}/events")
def get_events(session_id: str) -> list[dict]:
    """The full event history from the Trajectory's own JSONL file, not the
    live queue -- unlike /stream (a single-consumer queue drained once),
    this is safe to call any number of times, including for a session that
    finished before anyone was watching /stream. Used to resume viewing a
    session (e.g. a reopened session link) without re-running anything."""
    session = _get_session(session_id)
    if session.trajectory_path is None or not os.path.exists(session.trajectory_path):
        return []
    with open(session.trajectory_path) as f:
        return [json.loads(line) for line in f if line.strip()]


@app.get("/budget")
def get_budget() -> dict:
    return BUDGET.usage()


@app.get("/limits")
def get_limits(request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    return {"runs_remaining_today": RATE_LIMITER.remaining(client_ip), "max_runs_per_day": MAX_RUNS_PER_IP_PER_DAY}


@app.get("/sessions/{session_id}/status")
def session_status(session_id: str) -> dict:
    session = _get_session(session_id)
    return {"status": session.status, "error": session.error}


@app.get("/sessions/{session_id}/report")
def get_report(session_id: str) -> FileResponse:
    session = _get_session(session_id)
    report_path = os.path.join(session.workspace_dir, "REPORT.md")
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="no report yet")
    return FileResponse(report_path, media_type="text/markdown")


@app.get("/sessions/{session_id}/download/script")
def download_script(session_id: str) -> FileResponse:
    session = _get_session(session_id)
    script_path = os.path.join(session.workspace_dir, "train.py")
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail="no script yet")
    return FileResponse(script_path, media_type="text/x-python", filename="train.py")
