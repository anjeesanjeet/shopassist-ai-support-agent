"""FastAPI service: web chat API, WhatsApp webhook, dashboard and flowchart APIs, static pages.

Run:  python -m app.main     then open http://127.0.0.1:8000
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from html import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent import SupportAgent
from app.config import ROOT_DIR, get_settings
from app.db import connect, init_schema, load_messages
from app.flowchart import architecture_mermaid, decision_mermaid, latest_conversation_id, trace_mermaid
from app.llm import ProviderConfigError, get_provider
from app.metrics import dashboard_payload
from app.seed import build_database

STATIC = ROOT_DIR / "static"
settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.db_path.exists():
        build_database(settings.db_path)
    yield


app = FastAPI(title="ShopAssist: AI customer support agent", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@lru_cache(maxsize=1)
def get_agent() -> SupportAgent:
    return SupportAgent(get_provider(settings), settings)


# ------------------------------------------------------------------ pages

@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/chat")


@app.get("/chat", include_in_schema=False)
def chat_page():
    return FileResponse(STATIC / "chat.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(STATIC / "dashboard.html")


@app.get("/flowchart", include_in_schema=False)
def flowchart_page():
    return FileResponse(STATIC / "flowchart.html")


# ------------------------------------------------------------------ API

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    channel: str = "web"


@app.post("/api/chat")
def chat(body: ChatIn):
    try:
        agent = get_agent()
    except ProviderConfigError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    conversation_id = body.conversation_id or f"web-{uuid.uuid4().hex[:12]}"
    result = agent.handle_message(conversation_id, body.message, channel=body.channel)
    return result.to_dict()


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str):
    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        messages = load_messages(conn, conversation_id)
    finally:
        conn.close()
    if not messages:
        raise HTTPException(404, "Conversation not found")
    return {"conversation_id": conversation_id, "messages": messages}


@app.get("/api/dashboard")
def dashboard():
    return dashboard_payload(settings)


@app.get("/api/flowchart")
def flowchart(view: str = "architecture", conversation_id: str | None = None):
    if view == "architecture":
        return {"view": view, "mermaid": architecture_mermaid(settings)}
    if view == "decision":
        return {"view": view, "mermaid": decision_mermaid(settings)}
    if view == "trace":
        cid = conversation_id or latest_conversation_id(settings)
        diagram = trace_mermaid(settings, cid) if cid else None
        if not diagram:
            return {"view": view, "conversation_id": cid, "mermaid": None}
        return {"view": view, "conversation_id": cid, "mermaid": diagram}
    raise HTTPException(400, "view must be architecture, decision or trace")


@app.get("/api/health")
def health():
    return {"status": "ok", "provider": settings.llm_provider}


# ------------------------------------------------------------------ WhatsApp (Twilio)

def _valid_twilio_signature(request: Request, form: dict) -> bool:
    """Validates X-Twilio-Signature when TWILIO_AUTH_TOKEN and PUBLIC_BASE_URL are configured."""
    if not settings.twilio_auth_token or not settings.public_base_url:
        return True  # local development: validation disabled
    url = settings.public_base_url + request.url.path
    data = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    digest = hmac.new(settings.twilio_auth_token.encode(), data.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", ""))


@app.post("/webhooks/whatsapp", include_in_schema=False)
async def whatsapp(request: Request):
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not _valid_twilio_signature(request, form):
        raise HTTPException(403, "Invalid Twilio signature")
    sender, text = form.get("From", "unknown"), (form.get("Body") or "").strip()
    if not text:
        reply = "Hi! Send me your question about an order, delivery, return or refund."
    else:
        try:
            result = get_agent().handle_message(f"wa-{sender}", text, channel="whatsapp")
            reply = result.reply
        except ProviderConfigError:
            reply = "Our assistant is not configured yet. Please try again later."
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(reply[:1500])}</Message></Response>'
    return Response(content=twiml, media_type="application/xml")


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()
