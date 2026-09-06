import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig
from src.agents.planner_agent import PlannerAgent, PLANNER_SCHEMA
from src.schemas.models import Goal

load_dotenv()

async def test_planner_with_schema_call():
    print("Testing planner agent's provider.generate call with schema...")

    model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
    temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
    max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
    config = ProviderConfig(model_id=model_id, temperature=temp, max_tokens=max_tok)
    provider = NvidiaNimProvider(config)
    planner = PlannerAgent(provider)

    goal = "Say hello"

    # Build the prompt as the planner does
    prompt = f"User Goal:\n{goal}\n\nReturn the task plan as JSON."

    print("Prompt built.")
    print(f"System prompt length: {len(planner.SYSTEM_PROMPT)}")
    print(f"User prompt length: {len(prompt)}")
    print(f"Using schema: {PLANNER_SCHEMA}")

    try:
        print("Calling provider.generate with schema and timeout...")
        # Wrap in asyncio.wait_for to add a timeout
        response = await asyncio.wait_for(
            provider.generate(
                system_prompt=planner.SYSTEM_PROMPT,
                user_prompt=prompt,
                schema=PLANNER_SCHEMA,
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

    # Now try to parse the result as the planner does
    try:
        print("Parsing result with _extract_and_parse_json...")
        from src.utils.json_parser import _extract_and_parse_json
        parsed_data = _extract_and_parse_json(response.content)
        print(f"Parsed data: {parsed_data}")

        # Now create PlannerOutput manually (we don't need the full object, just check it works)
        from src.agents.planner_agent import TaskPlan
        # The planner's _parse_plan returns a TaskPlan
        # We can try to create a TaskPlan from the parsed data to see if it's valid
        # But for now, just check that we have the expected keys
        if "tasks" in parsed_data and isinstance(parsed_data["tasks"], list):
            print(f"SUCCESS: Planner returned {len(parsed_data['tasks'])} tasks")
            for i, task in enumerate(parsed_data["tasks"]):
                print(f"  Task {i+1}: {task.get('task_id', '?')} - {task.get('description', '??')[:50]}...")
        else:
            print("WARNING: Parsed data does not contain a 'tasks' list")
    except Exception as e:
        print(f"Error in parsing or creating TaskPlan: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_planner_with_schema_call())