import os
import asyncio
from typing import Dict, List, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

from . import db
from .llm import stream_assistant_reply, summarize_session

load_dotenv()

app = FastAPI(title="Realtime AI Backend (WebSockets + Supabase)")

@app.on_event("startup")
async def _startup():
    # Warm up connection pool early so failures are obvious
    await db.get_pool()

@app.on_event("shutdown")
async def _shutdown():
    await db.close_pool()

@app.get("/")
async def root():
    # Basic landing page
    return {"ok": True, "ws": "/ws/session/{session_id}?user_id=... (optional)"}

@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    html = open(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"), "r", encoding="utf-8").read()
    return HTMLResponse(html)

@app.websocket("/ws/session/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str):
    await websocket.accept()

    user_id = websocket.query_params.get("user_id", "user-1")
    await db.upsert_session(session_id=session_id, user_id=user_id)
    await db.log_event(session_id, "system", "system", f"WebSocket connected for user_id={user_id}")

    messages: List[Dict[str, str]] = []

    try:
        while True:
            user_text = await websocket.receive_text()
            await db.log_event(session_id, "user_message", "user", user_text)

            messages.append({"role": "user", "content": user_text})

            await websocket.send_json({"type": "start", "role": "assistant"})
            full_text_parts: List[str] = []

            final_text, token_stream, meta = await stream_assistant_reply(messages, user_id=user_id)

            async for tok in token_stream:
                full_text_parts.append(tok)
                await websocket.send_json({"type": "token", "token": tok})

            assistant_text = final_text or "".join(full_text_parts).strip()

            await db.log_event(session_id, "assistant_message", "assistant", assistant_text, meta=meta or {})
            messages.append({"role": "assistant", "content": assistant_text})

            await websocket.send_json({"type": "done", "text": assistant_text})

    except WebSocketDisconnect:
        await db.log_event(session_id, "system", "system", "WebSocket disconnected")
        asyncio.create_task(post_session_processing(session_id))
    except Exception as e:
        await db.log_event(session_id, "system", "system", f"Server error: {e.__class__.__name__}: {e}")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
        asyncio.create_task(post_session_processing(session_id))

async def post_session_processing(session_id: str) -> None:
    """
    On disconnect: read event log, ask LLM to summarize, and finalize session record.
    """
    try:
        transcript_rows = await db.get_transcript(session_id)
        transcript_lines: List[str] = []
        for r in transcript_rows:
            role = r["role"]
            et = r["event_type"]
            content = r["content"]
            if et in ("user_message", "assistant_message", "tool_call", "tool_result", "system"):
                transcript_lines.append(f"[{role}] {content}")
        transcript = "\n".join(transcript_lines)

        summary = await summarize_session(transcript)
        await db.finalize_session(session_id, summary)

        await db.log_event(session_id, "system", "system", "Session finalized with summary.")
    except Exception as e:
        try:
            await db.log_event(session_id, "system", "system", f"Post-session processing failed: {e}")
        except Exception:
            pass
