# Tecnvi Realtime AI Backend (WebSockets + Supabase)

This project implements the assignment **“Realtime AI Backend (WebSockets + Supabase)”** using **FastAPI + asyncpg**.

It supports:
- **Realtime bi-directional WebSocket chat**: `/ws/session/{session_id}`
- **Token streaming** back to client (low latency)
- **Complex LLM interaction** via **tool/function calling** (account balance / order status)
- **Persistence to Supabase Postgres**: session metadata + detailed event log
- **Post-session automation**: on disconnect, an async job summarizes the session and updates the session record

> If `OPENAI_API_KEY` is not set, the server runs in **mock mode** (still streams tokens, still persists to DB) so you can demo end-to-end without external keys.

---

## 1) Setup

### Prerequisites
- Python 3.10+
- A Supabase project with Postgres enabled
- (Recommended) OpenAI API key for real LLM streaming

### Install
```bash
python -m venv .venv
source .venv/bin/activate   # (Windows) .venv\Scripts\activate
pip install -r requirements.txt
```

### Environment
Copy and edit:
```bash
cp .env.example .env
```

Fill:
- `SUPABASE_DB_URL` (Supabase → Project Settings → Database → Connection string → URI)
- `OPENAI_API_KEY` (optional)

---

## 2) Supabase Database Schema

Run `schema.sql` in **Supabase SQL Editor**:

```sql
-- see schema.sql
```

Tables:
- `sessions(session_id, user_id, start_time, end_time, duration_seconds, summary)`
- `session_events(id, session_id, ts, event_type, role, content, meta)`

---

## 3) Run the Server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:
- API root: `http://localhost:8000/`
- Demo UI: `http://localhost:8000/demo`

---

## 4) How to Test (WebSocket)

### Option A: Built-in HTML demo
1. Go to `http://localhost:8000/demo`
2. Click **Connect**
3. Send messages and watch token streaming

Try:
- “What is my balance? user_id=user-1”
- “Check order status for order_id=ORD-1001”
- Any normal question

### Option B: wscat (CLI)
```bash
npm i -g wscat
wscat -c ws://localhost:8000/ws/session/demo-session?user_id=user-1
```

Type messages; you will receive JSON frames:
- `{type:"start"}`
- `{type:"token"}`
- `{type:"done"}`

---

## 5) Design Notes (Key Choices)

### Streaming
The server sends tokens as JSON frames immediately:
- This is compatible with browser clients and easy to extend (e.g., add partial timestamps, latency).

### “Complex interaction”
We use **LLM tool/function calling**:
- First LLM call decides whether to call a tool.
- Tools are executed (simulated) and passed back to the LLM.
- Final response is streamed to the client.

Tools are implemented in `app/tools.py`.

### Persistence
All events are persisted to Supabase Postgres using `asyncpg`:
- `sessions`: metadata + summary fields updated on disconnect
- `session_events`: chronological log of user/assistant/system events

### Post-session automation
On WebSocket close:
- `asyncio.create_task(post_session_processing(...))` fetches the transcript
- Calls the LLM to create a concise summary
- Updates `sessions` with `end_time`, `duration_seconds`, `summary`

---

## Repo Structure
```
tecnvi_realtime_ai_backend/
  app/
    main.py
    db.py
    llm.py
    tools.py
  frontend/
    index.html
  schema.sql
  requirements.txt
  .env.example
```
