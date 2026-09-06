import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig
from src.agents.reviewer_agent import ReviewerAgent, REVIEWER_SCHEMA
from src.schemas.models import Task, WorkerResult, ReviewResult

load_dotenv()

async def test_reviewer_with_schema_call():
    print("Testing reviewer agent's provider.generate call with schema...")

    model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
    temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
    max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
    config = ProviderConfig(model_id=model_id, temperature=temp, max_tokens=max_tok)
    provider = NvidiaNimProvider(config)
    reviewer = ReviewerAgent(provider)

    # Create a simple task
    task = Task(
        task_id="t1",
        description="Say 'Hello World' in a single line of text.",
        dependencies=[],
        context={}
    )

    # Create a simple worker result
    worker_result = WorkerResult(
        raw_output="Hello World",
        artifacts=[]
    )

    # Build the prompt as the reviewer does
    prompt = f"""Task Description:
{task.description}

Worker Output:
{worker_result.raw_output}

Context: {task.context}

Dependencies considered: {task.dependencies}

Evaluate the worker output. Is it correct, complete, and properly formatted? Provide feedback, any issues found, suggestions for improvement, whether a retry is allowed, and a score from 0.0 to 1.0."""

    print("Prompt built.")
    print(f"System prompt length: {len(reviewer.SYSTEM_PROMPT)}")
    print(f"User prompt length: {len(prompt)}")
    print(f"Using schema: {REVIEWER_SCHEMA}")

    try:
        print("Calling provider.generate with schema and timeout...")
        # Wrap in asyncio.wait_for to add a timeout
        response = await asyncio.wait_for(
            provider.generate(
                system_prompt=reviewer.SYSTEM_PROMPT,
                user_prompt=prompt,
                schema=REVIEWER_SCHEMA,
                max_tokens=max(4096, config.max_tokens),  # Ensure at least 4096
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

    # Now try to parse the result as the reviewer does
    try:
        print("Parsing result with _extract_and_parse_json...")
        from src.utils.json_parser import _extract_and_parse_json
        parsed_data = _extract_and_parse_json(response.content)
        print(f"Parsed data: {parsed_data}")

        # Create ReviewResult
        review_result = ReviewResult(
            is_valid=parsed_data.get("approved", False),
            feedback=parsed_data.get("feedback", ""),
            issues=parsed_data.get("issues", []),
            suggestions=parsed_data.get("suggestions", []),
            retry_allowed=parsed_data.get("retry_allowed", True),
            score=float(parsed_data.get("score", 0.0))
        )
        print(f"Review result is_valid: {review_result.is_valid}")
        print(f"Review result feedback: {review_result.feedback[:100]}...")
        print(f"Review result score: {review_result.score}")
        print("SUCCESS: Reviewer executed without hanging")
    except Exception as e:
        print(f"Error in parsing or creating ReviewResult: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_reviewer_with_schema_call())