import asyncio
import os
from dotenv import load_dotenv
from src.providers.base_provider import NvidiaNimProvider, ProviderConfig
from src.agents.planner_agent import PlannerAgent

load_dotenv()
model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
config = ProviderConfig(model_id=model_id, temperature=temp, max_tokens=max_tok)
provider = NvidiaNimProvider(config)
planner = PlannerAgent(provider)

async def test():
    print("Testing planner with goal 'Say hello'...")
    try:
        # Wait for the planner for 10 seconds
        result = await asyncio.wait_for(planner.execute("Say hello"), timeout=10.0)
        print("Planner returned successfully")
        # Instead of printing the result, let's just check the number of tasks
        print(f"Number of tasks: {len(result.tasks)}")
        for i, task in enumerate(result.tasks):
            print(f"Task {i+1}: {task.task_id} - {task.description[:50]}...")
    except asyncio.TimeoutError:
        print("Planner timed out after 10 seconds")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())