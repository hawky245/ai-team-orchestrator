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
    print("Testing planner...")
    try:
        result = await planner.execute("Design a social media database schema")
        print(f"Got result: {result}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())