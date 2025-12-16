import os
import json
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from .tools import fetch_account_balance, fetch_order_status

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()

def has_openai() -> bool:
    return bool(_OPENAI_KEY)

def _client() -> AsyncOpenAI:
    return AsyncOpenAI(api_key=_OPENAI_KEY)

SYSTEM_PROMPT = (
    "You are a helpful assistant in a realtime chat session. "
    "Be concise, ask clarifying questions when needed, and when a tool is available "
    "use it ONLY if it meaningfully improves the answer."
)

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "fetch_account_balance",
            "description": "Get the current account balance for a user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "User identifier"}
                },
                "required": ["user_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_order_status",
            "description": "Get shipping/delivery status for an order.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "Order identifier"}
                },
                "required": ["order_id"]
            }
        }
    }
]

async def stream_assistant_reply(
    messages: List[Dict[str, str]],
    user_id: str = "user-1",
) -> Tuple[str, AsyncGenerator[str, None], Optional[Dict]]:
    """
    Returns (final_text, token_stream, optional_debug_meta).

    If OPENAI_API_KEY is not set, uses a deterministic mock streamer.
    """
    if not has_openai():
        full = _mock_response(messages)
        async def gen():
            for ch in full:
                yield ch
        return full, gen(), {"mode": "mock"}

    client = _client()

    # 1) First call: allow the model to decide if it wants a tool
    resp = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        tools=TOOL_SPECS,
        tool_choice="auto",
    )
    choice = resp.choices[0]
    tool_calls = choice.message.tool_calls

    # If tool calls requested, execute them and send results back
    if tool_calls:
        tool_msgs = []
        debug = {"tool_calls": []}
        for tc in tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            debug["tool_calls"].append({"name": name, "args": args})

            if name == "fetch_account_balance":
                result = await fetch_account_balance(args.get("user_id", user_id))
            elif name == "fetch_order_status":
                result = await fetch_order_status(args.get("order_id", "unknown"))
            else:
                result = {"error": f"Unknown tool: {name}"}

            tool_msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": json.dumps(result, ensure_ascii=False)
            })

        # 2) Second call: stream the final answer using tool outputs
        stream = await client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=([{"role": "system", "content": SYSTEM_PROMPT}] + messages
                      + [{"role": "assistant", "content": choice.message.content or "", "tool_calls": tool_calls}]
                      + tool_msgs),
            stream=True,
        )

        final_parts: List[str] = []

        async def token_gen():
            async for event in stream:
                delta = event.choices[0].delta
                if delta and delta.content:
                    final_parts.append(delta.content)
                    yield delta.content

        # We can't know final text until stream completes; create a wrapper
        async def collect_and_yield():
            async for t in token_gen():
                yield t

        # Consume stream later by server; but we also need final string returned.
        # We'll return placeholder here; server should rebuild from tokens.
        return "", collect_and_yield(), debug

    # No tool: stream response directly
    stream = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        stream=True,
    )

    final_parts: List[str] = []

    async def token_gen():
        async for event in stream:
            delta = event.choices[0].delta
            if delta and delta.content:
                final_parts.append(delta.content)
                yield delta.content

    async def collect_and_yield():
        async for t in token_gen():
            yield t

    return "", collect_and_yield(), {"mode": "openai_stream"}

async def summarize_session(transcript: str) -> str:
    if not has_openai():
        return _mock_summary(transcript)

    client = _client()
    prompt = (
        "Summarize this conversation in 5-7 bullet points. "
        "Include any actions taken (including tool usage) and the user's final intent.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )
    resp = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{"role": "system", "content": "You are an expert session summarizer."},
                  {"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()

def _mock_response(messages: List[Dict[str, str]]) -> str:
    last = ""
    for m in reversed(messages):
        if m["role"] == "user":
            last = m["content"]
            break
    if "balance" in last.lower():
        return "I can help with that. Please share your user_id and I will fetch the balance."
    if "order" in last.lower():
        return "Sure — share your order_id and I will fetch the latest order status."
    return "Got it. Here is a concise response based on your message: " + last[:160]

def _mock_summary(transcript: str) -> str:
    lines = [ln.strip() for ln in transcript.splitlines() if ln.strip()]
    return "\n".join([
        "- Session completed (mock mode).",
        f"- Total transcript lines: {len(lines)}.",
        "- Key topics: realtime chat, streaming, persistence, and post-session summary."
    ])
