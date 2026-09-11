from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import main
from sqlalchemy.orm import Session
from src.database import RunModel, TaskModel, get_db

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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