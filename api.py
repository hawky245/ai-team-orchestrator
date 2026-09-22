from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

import asyncio
import json
import os

from dotenv import load_dotenv

load_dotenv()

import main
from main import AI_TEAM_ORCHESTRATOR
from src.database import RunModel, TaskModel, get_db
from src.providers.base_provider import (
    NvidiaNimProvider,
    ProviderConfig,
    ProviderError,
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

class ExecutionRequest(BaseModel):
    goal: str

@app.get("/")
def health_check():
    return {"status": "ok", "message": "AI Team Orchestrator API is running"}

@app.post("/api/execute")
def execute(request: ExecutionRequest):
    try:
        result = main.run_orchestrator(request.goal)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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

@app.get("/api/models")
async def list_available_models(api_key: str | None = None):
    """Proxy the provider's model catalogue for the dashboard's model picker.

    With `api_key` the list reflects that key's access; without it the
    server's .env key is used. Invalid keys return a clean 400 — the key
    itself is never echoed back. (Caveat: query-string keys can land in
    access logs; the dashboard also accepts keys via the run payload.)
    """
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
    }

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

        # Send final result
        await safe_send({
            "type": "execution_complete",
            "data": result
        })

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