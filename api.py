from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import main

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