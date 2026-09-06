import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig
from src.agents.worker_agent import WorkerAgent, WORKER_SCHEMA
from src.schemas.models import Task

load_dotenv()

async def test_worker_with_schema():
    print("Testing worker agent with schema...")

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

    # Build the prompt as the worker does
    prompt = (
        f"Task Description:\n{task.description}\n\n"
        f"Context: {task.context}\n\n"
        f"Dependencies to consider: {task.dependencies}\n\n"
        f"Produce the deliverable for this task."
    )

    print("Prompt built.")
    print(f"System prompt: {worker.SYSTEM_PROMPT[:100]}...")
    print(f"User prompt: {prompt[:200]}...")
    print(f"Schema: {WORKER_SCHEMA}")

    try:
        print("Calling provider.generate...")
        response: provider = await provider.generate(
            system_prompt=worker.SYSTEM_PROMPT,
            user_prompt=prompt,
            schema=WORKER_SCHEMA,
        )
        print(f"Provider returned: {response}")
        print(f"Response content: {response.content}")
    except Exception as e:
        print(f"Error in provider.generate: {e}")
        import traceback
        traceback.print_exc()
        return

    # Now try to parse the result as the worker does
    try:
        print("Parsing result...")
        worker_result = worker._parse_result(response.content)
        print(f"Worker result raw_output: {worker_result.raw_output[:100]}...")
        print(f"Worker result artifacts: {len(worker_result.artifacts)}")
        print("SUCCESS: Worker executed without hanging")
    except Exception as e:
        print(f"Error in worker._parse_result: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_worker_with_schema())