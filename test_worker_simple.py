import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig
from src.agents.worker_agent import WorkerAgent
from src.schemas.models import Task

load_dotenv()

async def test_worker():
    print("Testing worker agent with simple task...")

    model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
    temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
    max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
    config = ProviderConfig(model_id=model_id, temperature=temp, max_tokens=max_tok)
    provider = NvidiaNimProvider(config)
    worker = WorkerAgent(provider)

    # Create a simple task
    task = Task(
        task_id="t1",
        description="Say 'Hello World' in a single line of text.",
        dependencies=[],
        context={}
    )

    try:
        print("Executing task...")
        result = await worker.execute(task)
        print(f"Worker result raw_output: {result.raw_output[:100]}...")
        print(f"Worker result artifacts: {len(result.artifacts)}")
        print("SUCCESS: Worker executed without hanging")
    except Exception as e:
        print(f"Error in worker: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_worker())