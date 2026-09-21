from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

import asyncio
import json

# Load environment variables before anything else so OPENROUTER_* settings
# are available when the LLM client and orchestrator are initialized.
from dotenv import load_dotenv

load_dotenv()

import main
from main import AI_TEAM_ORCHESTRATOR
from src.database import RunModel, TaskModel, get_db

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

        if not goal:
            await safe_send({
                "type": "error",
                "message": "Goal is required"
            })
            return

        # Run the orchestrator with the websocket for streaming while the
        # same connection carries inbound user_feedback frames (Milestone 9).
        # A paused task resumes as soon as its feedback arrives here.
        orchestrator = AI_TEAM_ORCHESTRATOR()
        run_task = asyncio.create_task(orchestrator.run(goal, websocket=websocket))

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
                    data = msg.get("data") or {}
                    accepted = orchestrator.submit_feedback(
                        str(data.get("task_id", "")),
                        str(data.get("feedback", "")),
                    )
                    if not accepted:
                        await safe_send({
                            "type": "feedback_rejected",
                            "data": {
                                "task_id": data.get("task_id"),
                                "reason": "No task is currently waiting for input",
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