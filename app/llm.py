import os
import json
import asyncio
from typing import AsyncGenerator, Dict, List, Optional, Tuple

from openai import AsyncOpenAI

from .tools import fetch_account_balance, fetch_order_status

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
_OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()

MOCK_STREAM_DELAY = float(os.getenv("MOCK_STREAM_DELAY", "0.02"))
MOCK_CHUNK_SIZE = int(os.getenv("MOCK_CHUNK_SIZE", "3"))


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
                "required": ["user_id"],
            },
        },
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
                "required": ["order_id"],
            },
        },
    },
]


async def _mock_stream_text(text: str) -> AsyncGenerator[str, None]:
    """
    Visible streaming in mock mode:
    - yields small chunks
    - sleeps briefly so UI shows incremental updates
    """
    if not text:
        return
    chunk_size = max(1, MOCK_CHUNK_SIZE)
    delay = max(0.0, MOCK_STREAM_DELAY)

    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
        if delay > 0:
            await asyncio.sleep(delay)


async def stream_assistant_reply(
    messages: List[Dict[str, str]],
    user_id: str = "user-1",
) -> Tuple[str, AsyncGenerator[str, None], Optional[Dict]]:
    """
    Returns (final_text, token_stream, optional_debug_meta).

    NOTE: Many WebSocket handlers build the final assistant message by
    concatenating the streamed tokens, so we return final_text="" and stream.
    (Mock mode still *visibly* streams.)
    """
    # -----------------------
    # MOCK MODE (no OpenAI key)
    # -----------------------
    if not has_openai():
        full = _mock_response(messages)

        async def gen():
            async for chunk in _mock_stream_text(full):
                yield chunk

        return "", gen(), {"mode": "mock", "mock_delay": MOCK_STREAM_DELAY, "mock_chunk": MOCK_CHUNK_SIZE}

    # -----------------------
    # REAL OPENAI MODE
    # -----------------------
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
        debug = {"mode": "openai_tools", "tool_calls": []}

        # Convert tool calls to JSON-safe payload
        tool_calls_payload = []
        for tc in tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")

            debug["tool_calls"].append({"name": name, "args": args})

            tool_calls_payload.append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
            )

            if name == "fetch_account_balance":
                result = await fetch_account_balance(args.get("user_id", user_id))
            elif name == "fetch_order_status":
                result = await fetch_order_status(args.get("order_id", "unknown"))
            else:
                result = {"error": f"Unknown tool: {name}"}

            tool_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        # 2) Second call: stream the final answer using tool outputs
        stream = await client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=(
                [{"role": "system", "content": SYSTEM_PROMPT}]
                + messages
                + [
                    {
                        "role": "assistant",
                        "content": choice.message.content or "",
                        "tool_calls": tool_calls_payload,
                    }
                ]
                + tool_msgs
            ),
            stream=True,
        )

        async def token_gen():
            async for event in stream:
                delta = event.choices[0].delta
                if delta and delta.content:
                    yield delta.content

        return "", token_gen(), debug

    # No tool call: stream directly
    stream = await client.chat.completions.create(
        model=DEFAULT_MODEL,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        stream=True,
    )

    async def token_gen():
        async for event in stream:
            delta = event.choices[0].delta
            if delta and delta.content:
                yield delta.content

    return "", token_gen(), {"mode": "openai_stream"}


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
        messages=[
            {"role": "system", "content": "You are an expert session summarizer."},
            {"role": "user", "content": prompt},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _mock_response(messages: List[Dict[str, str]]) -> str:
    last_user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user = m.get("content", "")
            break

    low = last_user.lower()

    # A slightly more "assistant-like" mock so demos look realistic
    if "balance" in low:
        return (
            "Sure — I can help check that.\n"
            "Please share your user_id (e.g., user-1) and I’ll fetch the current balance."
        )
    if "order" in low and "status" in low:
        return (
            "Absolutely.\n"
            "Please share your order_id (e.g., ORD-1001) and I’ll fetch the latest shipping status."
        )
    if "websocket" in low and "fastapi" in low:
        return (
            "Here’s a step-by-step overview of how WebSockets work in FastAPI:\n"
            "1) Client requests an HTTP upgrade to WebSocket.\n"
            "2) Server accepts the upgrade and keeps the connection open.\n"
            "3) Messages can flow both ways (full-duplex).\n"
            "4) Your handler awaits incoming frames (receive_text/receive_json).\n"
            "5) You send frames back (send_text/send_json) anytime.\n"
            "6) For streaming, you send many small frames incrementally.\n"
            "7) Maintain per-session state in memory or DB.\n"
            "8) Handle disconnects and cleanup.\n"
            "9) Use async I/O to scale many sessions.\n"
            "10) Add auth + rate limits for production.\n\n"
            "Example:\n"
            "async for token in generator: await ws.send_json({'type':'token','token': token})"
        )

    return (
        "Got it. I received your message and I’m responding in mock streaming mode.\n\n"
        f"You said: {last_user[:220]}"
    )


def _mock_summary(transcript: str) -> str:
    lines = [ln.strip() for ln in transcript.splitlines() if ln.strip()]
    return "\n".join(
        [
            "- Session completed (mock mode).",
            f"- Transcript lines recorded: {len(lines)}.",
            "- The session demonstrated realtime WebSocket streaming and persistence.",
        ]
    )
