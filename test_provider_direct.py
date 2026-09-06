import asyncio
import os
import sys
from dotenv import load_dotenv

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.providers.base_provider import NvidiaNimProvider, ProviderConfig

load_dotenv()

async def test_provider():
    print("Testing provider directly...")

    model_id = os.getenv("NVIDIA_MODEL_ID", "meta/llama3-70b-instruct")
    temp = float(os.getenv("NVIDIA_TEMPERATURE", "0.7"))
    max_tok = int(os.getenv("NVIDIA_MAX_TOKENS", "1024"))
    print(f"Model ID: {model_id}")
    print(f"Temperature: {temp}")
    print(f"Max tokens (from env): {max_tok}")

    config = ProviderConfig(model_id=model_id, temperature=temp, max_tokens=max_tok)
    provider = NvidiaNimProvider(config)

    # Test with a low max_tokens to see if it responds
    try:
        print("Calling provider.generate with max_tokens=50...")
        response = await provider.generate(
            system_prompt="You are a helpful assistant.",
            user_prompt="Say hello in one word.",
            schema=None,
            max_tokens=50,  # Override to a low value
            temperature=0.7,
        )
        print(f"Provider returned: {response}")
        print(f"Response content: {response.content}")
    except Exception as e:
        print(f"Error in provider.generate: {e}")
        import traceback
        traceback.print_exc()
        return

if __name__ == "__main__":
    asyncio.run(test_provider())