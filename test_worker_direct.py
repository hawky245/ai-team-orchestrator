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

async def test_worker_direct():
    print("Testing worker agent with direct provider call...")

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
    print(f"System prompt length: {len(worker.SYSTEM_PROMPT)}")
    print(f"User prompt length: {len(prompt)}")

    try:
        print("Calling provider.generate with timeout...")
        # Wrap in asyncio.wait_for to add a timeout
        response = await asyncio.wait_for(
            provider.generate(
                system_prompt=worker.SYSTEM_PROMPT,
                user_prompt=prompt,
                schema=None,  # No schema for now to test basic functionality
                max_tokens=200,  # Reasonable limit
                temperature=temp,
            ),
            timeout=30.0  # 30 second timeout
        )
        print(f"Provider returned: {response}")
        print(f"Response content: {response.content}")
        print(f"Response content length: {len(response.content)}")
    except asyncio.TimeoutError:
        print("Provider.generate timed out after 30 seconds")
        return
    except Exception as e:
        print(f"Error in provider.generate: {e}")
        import traceback
        traceback.print_exc()
        return

    # Now try to parse the result as the worker does
    try:
        print("Parsing result with _extract_and_parse_json...")
        from src.utils.json_parser import _extract_and_parse_json
        parsed_data = _extract_and_parse_json(response.content)
        print(f"Parsed data: {parsed_data}")

        # Now create WorkerResult manually
        from src.agents.worker_agent import WorkerResult
        worker_result = WorkerResult(
            raw_output=parsed_data.get("raw_output", ""),
            artifacts=parsed_data.get("artifacts", [])
        )
        print(f"Worker result raw_output: {worker_result.raw_output[:100]}...")
        print(f"Worker result artifacts: {len(worker_result.artifacts)}")
        print("SUCCESS: Worker executed without hanging")
    except Exception as e:
        print(f"Error in parsing or creating WorkerResult: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_worker_direct())