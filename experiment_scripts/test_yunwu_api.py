"""
Test Yunwu API endpoints (Anthropic + OpenAI protocols)
"""

import asyncio
import anthropic
from openai import AsyncOpenAI


YUNWU_API_KEY = "sk-73SOvht7CVS4W2OM7tIlZdO2p3N5gZcagguW6RSxSj8lK50v"

# ── Test 1: Anthropic protocol via Yunwu ──
async def test_anthropic():
    print("=" * 60)
    print("Test 1: Anthropic protocol (https://yunwu.ai)")
    print("=" * 60)

    client = anthropic.AsyncAnthropic(
        api_key=YUNWU_API_KEY,
        base_url="https://yunwu.ai",
    )

    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6-20250514",
            max_tokens=100,
            messages=[{"role": "user", "content": "Say hello in Chinese, one sentence."}],
        )
        print(f"  Model: claude-sonnet-4-6-20250514")
        print(f"  Response: {response.content[0].text}")
        print(f"  Usage: input={response.usage.input_tokens}, output={response.usage.output_tokens}")
        print("  ✅ SUCCESS")
    except Exception as e:
        print(f"  ❌ FAILED: {e}")

    # Also try the short model name
    print()
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": "Say hello in Chinese, one sentence."}],
        )
        print(f"  Model: claude-sonnet-4-6")
        print(f"  Response: {response.content[0].text}")
        print("  ✅ SUCCESS")
    except Exception as e:
        print(f"  ❌ FAILED with claude-sonnet-4-6: {e}")


# ── Test 2: OpenAI protocol via Yunwu ──
async def test_openai():
    print()
    print("=" * 60)
    print("Test 2: OpenAI protocol (https://yunwu.ai/v1)")
    print("=" * 60)

    client = AsyncOpenAI(
        api_key=YUNWU_API_KEY,
        base_url="https://yunwu.ai/v1",
    )

    try:
        response = await client.chat.completions.create(
            model="gpt-5.1-2025-11-13",
            max_completion_tokens=100,
            messages=[{"role": "user", "content": "Say hello in Chinese, one sentence."}],
        )
        print(f"  Model: gpt-5.1-2025-11-13")
        print(f"  Response: {response.choices[0].message.content}")
        print("  ✅ SUCCESS")
    except Exception as e:
        print(f"  ❌ FAILED: {e}")


# ── Test 3: Embedding via Yunwu ──
async def test_embedding():
    print()
    print("=" * 60)
    print("Test 3: Embedding (https://yunwu.ai/v1)")
    print("=" * 60)

    client = AsyncOpenAI(
        api_key=YUNWU_API_KEY,
        base_url="https://yunwu.ai/v1",
    )

    try:
        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input="Hello world",
        )
        dims = len(response.data[0].embedding)
        print(f"  Model: text-embedding-3-small")
        print(f"  Dimensions: {dims}")
        print("  ✅ SUCCESS")
    except Exception as e:
        print(f"  ❌ FAILED: {e}")


async def main():
    await test_anthropic()
    await test_openai()
    await test_embedding()


if __name__ == "__main__":
    asyncio.run(main())
