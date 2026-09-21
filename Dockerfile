# AI Team Orchestrator — FastAPI backend
# Lightweight slim base; the pinned, fully-resolved dependency set lives in
# requirements.txt (UTF-16 with BOM — pip auto-detects it).
FROM python:3.12-slim

WORKDIR /app

# Install deps first so this layer caches unless requirements change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py api.py ./
COPY src/ ./src/

# Windows cp1252 stdout guard in main.py is irrelevant here, but keep UTF-8
# and unbuffered logs so docker logs stream planner/worker output live.
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    DB_PATH=/app/data/orchestrator.db

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
