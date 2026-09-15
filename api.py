from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

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
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
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

        # Run the orchestrator with the websocket for streaming
        orchestrator = AI_TEAM_ORCHESTRATOR()
        result = await orchestrator.run(goal, websocket=websocket)

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