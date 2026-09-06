import asyncio
import os
from dotenv import load_dotenv
from src.providers.base_provider import NvidiaNimProvider, ProviderConfig

load_dotenv()
model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
config = ProviderConfig(model_id=model_id, temperature=temp, max_tokens=max_tok)
provider = NvidiaNimProvider(config)

async def test():
    print("Testing provider...")
    try:
        response = await provider.generate(
            system_prompt="You are a helpful assistant.",
            user_prompt="Say hello in one word.",
            schema=None,  # No schema for now
        )
        print(f"Response: {response.content}")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())