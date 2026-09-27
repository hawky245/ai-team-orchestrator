from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

from main import AI_TEAM_ORCHESTRATOR
from src import telemetry
from src.database import RunModel, TaskModel, get_db
from src.providers.base_provider import (
    NvidiaNimProvider,
    ProviderConfig,
    ProviderError,
    detect_provider_label,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        # Docker: frontend served by nginx on :80 (direct 8000 calls too)
        "http://localhost", "http://127.0.0.1",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {"status": "ok", "message": "AI Team Orchestrator API is running"}

@app.get("/api/runs")
def list_runs(db: Session = Depends(get_db)):
    runs = db.query(RunModel).order_by(RunModel.created_at.desc()).all()
    return [run.to_dict() for run in runs]

@app.get("/api/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(RunModel).filter(RunModel.run_id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    tasks = db.query(TaskModel).filter(TaskModel.run_id == run_id).order_by(TaskModel.created_at).all()
    return {
        "run": run.to_dict(),
        "tasks": [task.to_dict() for task in tasks]
    }

class ModelsRequest(BaseModel):
    api_key: str | None = None

async def _models_payload(api_key: str | None) -> dict:
    """List models for a key (or the server .env key when None). The key is
    never echoed back; failures surface as a clean 400 without key material."""
    config = ProviderConfig(
        model_id=os.getenv("LLM_MODEL_ID", "unused-for-listing"),
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
    )
    try:
        provider = NvidiaNimProvider(config, api_key=api_key)
        models = await provider.list_models()
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "models": models,
        "source": "custom_key" if api_key else "server_env",
        # Display-only label derived from the key prefix / base URL host.
        # No key material is ever returned.
        "provider": detect_provider_label(api_key),
        # The model the planner runs on by default; the dashboard warns when
        # a fetched catalogue cannot serve it (planning would 404).
        "default_model": os.getenv("LLM_MODEL_ID", ""),
    }

@app.get("/api/models")
async def list_available_models():
    """Server-key catalogue only. Keys must travel in the POST body —
    query-string keys land verbatim in every proxy/access log."""
    return await _models_payload(None)

@app.get("/api/telemetry")
async def get_telemetry():
    """Real server metrics for the console's bottom bar (stdlib sampler,
    no key material involved)."""
    return telemetry.sample()

@app.post("/api/models")
async def list_available_models_for_key(payload: ModelsRequest):
    """Proxy the provider's model catalogue for a user-supplied key.

    The key is read from the JSON body so it never appears in a URL, and
    is therefore never written to access logs. Invalid keys return a clean
    400 that does not echo the key.
    """
    key = (payload.api_key or "").strip() or None
    return await _models_payload(key)

@app.websocket("/ws/execute")
async def websocket_execute(websocket: WebSocket):
    await websocket.accept()
    connection_closed = False

    async def safe_send(payload: dict) -> None:
        """Send only if the connection is still open; never raise on a closed socket."""
        nonlocal connection_closed
        if connection_closed:
            return
        try:
            await websocket.send_text(json.dumps(payload))
        except Exception:
            connection_closed = True

    try:
        # Receive the goal from the client
        data = await websocket.receive_text()
        request_data = json.loads(data)
        goal = request_data.get("goal")
        # Dynamic user-provided key: initial payload wins, query string is the
        # fallback. Never log either.
        api_key = request_data.get("api_key") or websocket.query_params.get("api_key")
        # Dashboard model picker; "Auto" (or absent) leaves planner routing alone
        model_selection = request_data.get("model_selection")
        # Plan-review gate: pause after planning so each task's model can be
        # assigned from the dashboard before workers start.
        review_plan = bool(request_data.get("review_plan"))
        # Pre-selected per-task models (task_id -> model), applied after
        # planning without pausing. Malformed payloads degrade to "none".
        raw_plan_models = request_data.get("plan_models")
        plan_models = (
            {
                str(k): str(v)
                for k, v in raw_plan_models.items()
                if isinstance(k, str) and isinstance(v, str)
            }
            if isinstance(raw_plan_models, dict) else None
        )
        # model -> api_key map so each task's calls use the key that served it
        raw_model_keys = request_data.get("model_keys")
        model_keys = (
            {
                str(k): str(v)
                for k, v in raw_model_keys.items()
                if isinstance(k, str) and isinstance(v, str)
            }
            if isinstance(raw_model_keys, dict) else None
        )
        # The dashboard's fetched catalogue: the planner is constrained to it
        # (prompt whitelist) and every model assignment is validated against
        # it before any wave runs. Malformed entries are dropped.
        raw_avail = request_data.get("available_models")
        available_models = (
            [s for m in raw_avail if isinstance(m, str) and (s := m.strip())]
            if isinstance(raw_avail, list) else None
        )

        if not goal:
            await safe_send({
                "type": "error",
                "message": "Goal is required"
            })
            return

        # Run the orchestrator with the websocket for streaming while the
        # same connection carries inbound user_feedback frames (Milestone 9).
        # A paused task resumes as soon as its feedback arrives here.
        orchestrator = AI_TEAM_ORCHESTRATOR(api_key=api_key)
        run_task = asyncio.create_task(
            orchestrator.run(
                goal,
                websocket=websocket,
                model_selection=model_selection,
                review_plan=review_plan,
                plan_models=plan_models,
                model_keys=model_keys,
                available_models=available_models,
            )
        )

        async def feedback_listener() -> None:
            while True:
                inbound = await websocket.receive_text()
                try:
                    msg = json.loads(inbound)
                except json.JSONDecodeError:
                    await safe_send({
                        "type": "error",
                        "data": {"message": "Malformed JSON from client"}
                    })
                    continue
                if msg.get("type") == "user_feedback":
                    fb_data = msg.get("data") or {}
                    accepted = orchestrator.submit_feedback(
                        str(fb_data.get("task_id", "")),
                        str(fb_data.get("feedback", "")),
                    )
                    if not accepted:
                        await safe_send({
                            "type": "feedback_rejected",
                            "data": {
                                "task_id": fb_data.get("task_id"),
                                "reason": "No task is currently waiting for input",
                            },
                        })
                elif msg.get("type") == "stop_run":
                    # Dashboard STOP: abandon at the next task/wave boundary.
                    orchestrator.request_stop()
                    await safe_send({"type": "stop_requested", "data": {}})
                elif msg.get("type") == "plan_model_assignment":
                    pa_data = msg.get("data") or {}
                    assignments = pa_data.get("assignments")
                    if not isinstance(assignments, dict):
                        assignments = {}
                    accepted = orchestrator.submit_plan_assignments(assignments)
                    if not accepted:
                        await safe_send({
                            "type": "feedback_rejected",
                            "data": {
                                "reason": "No plan review is currently pending",
                            },
                        })

        listen_task = asyncio.create_task(feedback_listener())
        done, _pending = await asyncio.wait(
            {run_task, listen_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if listen_task in done:
            # Client disconnected (or the listener crashed) mid-run:
            # stop the pipeline instead of orphaning it.
            listen_exc = listen_task.exception()
            if not isinstance(listen_exc, WebSocketDisconnect):
                raise listen_exc if listen_exc else RuntimeError("listener died")
            run_task.cancel()
            await listen_task  # re-raises WebSocketDisconnect for the handler
            return

        # Pipeline finished normally; no more feedback can arrive.
        listen_task.cancel()
        result = await run_task
        # The orchestrator already streamed execution_completed + run_completed
        # frames with the summary and final output; nothing else to send.

    except WebSocketDisconnect:
        # Client disconnected — nothing to send or close
        connection_closed = True
    except Exception as e:
        await safe_send({
            "type": "error",
            "message": str(e)
        })
    finally:
        if not connection_closed:
            try:
                await websocket.close()
            except Exception:
                pass