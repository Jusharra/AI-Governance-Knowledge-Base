# scripts/mcp_pinecone_demo.py
import os
import asyncio
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_anthropic import ChatAnthropic

load_dotenv()

# Load env vars
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ASSISTANT_HOST = os.getenv("PINECONE_ASSISTANT_HOST")
PINECONE_ASSISTANT_NAME = os.getenv("PINECONE_ASSISTANT_NAME")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Guard
required = {
    "PINECONE_API_KEY": PINECONE_API_KEY,
    "PINECONE_ASSISTANT_HOST": PINECONE_ASSISTANT_HOST,
    "PINECONE_ASSISTANT_NAME": PINECONE_ASSISTANT_NAME,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
}

missing = [k for k, v in required.items() if not v]
if missing:
    raise RuntimeError(f"❌ Missing env vars: {missing}")

# Build the correct MCP URL
mcp_url = f"https://{PINECONE_ASSISTANT_HOST}/mcp/assistants/{PINECONE_ASSISTANT_NAME}"

async def main():
    # --- ENV + validation ---
    load_dotenv(".env")
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
    PINECONE_ASSISTANT_HOST = os.getenv("PINECONE_ASSISTANT_HOST")
    PINECONE_ASSISTANT_NAME = os.getenv("PINECONE_ASSISTANT_NAME")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

    if not all([PINECONE_API_KEY, PINECONE_ASSISTANT_HOST, PINECONE_ASSISTANT_NAME, ANTHROPIC_API_KEY]):
        raise RuntimeError("Missing required environment variables")

    mcp_url = f"https://{PINECONE_ASSISTANT_HOST}/mcp/assistants/{PINECONE_ASSISTANT_NAME}"
    print(f"[MCP] Connecting to Pinecone Assistant at: {mcp_url}")

    # --- Build MCP client ---
    client = MultiServerMCPClient(
        {
            "pinecone_assistant": {
                "url": mcp_url,
                "transport": "streamable_http",
                "headers": {
                    "Authorization": f"Bearer {PINECONE_API_KEY}"
                }
            }
        }
    )

    # --- Load tools ---
    tools = await client.get_tools()
    print(f"[MCP] Loaded {len(tools)} tools from Pinecone Assistant.")

    # --- LLM ---
    model = ChatAnthropic(
        model="claude-3-7-sonnet-latest",
        api_key=ANTHROPIC_API_KEY,
    )

    # ====================================================
    # 🔥 INSERT THE CORRECT QUERY RIGHT HERE
    # ====================================================

    response = await model.ainvoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Use the pinecone_assistant.context tool to retrieve information about MFA control requirements."
                }
            ],
            "tool_choice": {
                "type": "tool",
                "name": "pinecone_assistant.context"
            }
        },
        tools=tools
    )

    # --- Print result ---
    print("\n=== ASSISTANT RESPONSE ===")
    print(response["messages"][-1].content)

if __name__ == "__main__":
    asyncio.run(main())
