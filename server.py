import asyncio
import json
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import store
from agent import agent_events

DB_PATH = os.environ.get("DB_PATH", str(Path(__file__).parent / "support.db"))
EFFORT = os.environ.get("AGENT_EFFORT", "low")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.open_db(DB_PATH)
    yield


app = FastAPI(title="Order support agent", lifespan=lifespan)
locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def check_owner(session_id: str, user_id: str) -> None:
    if store.session_user(session_id) != user_id:
        raise HTTPException(404, "Сессия не найдена")


@app.post("/sessions")
async def create_session(x_user_id: str = Header()):
    return {"session_id": store.create_session(x_user_id)}


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, x_user_id: str = Header()):
    check_owner(session_id, x_user_id)
    return {"items": store.load_history(session_id), "cost_usd": store.session_cost(session_id)}


@app.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn, x_user_id: str = Header()):
    check_owner(session_id, x_user_id)

    async def events():
        async with locks[session_id]:
            store.append_messages(session_id, [{"role": "user", "content": body.text}])
            steps, cost = 0, 0.0
            try:
                async for kind, data in agent_events(store.load_history(session_id), effort=EFFORT):
                    if kind == "text":
                        yield sse("delta", {"text": data})
                    elif kind == "tool":
                        yield sse("tool", data)
                    elif kind == "usage":
                        steps += 1
                        cost += data["cost_usd"]
                        store.log_usage(session_id, data)
                    elif kind == "step":
                        store.save_step(session_id, data)
                yield sse("done", {"steps": steps, "cost_usd": round(cost, 6)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"сессия {session_id[:8]}: {type(exc).__name__}: {exc}")
                yield sse("error", {"message": "Не удалось получить ответ, повторите сообщение"})

    return StreamingResponse(events(), media_type="text/event-stream")
