"""Automated flowcharts (Mermaid), generated from the live code and data:

  * architecture  - built from the tool registry, so adding a tool updates the diagram automatically
  * decision      - the agent's decision flow, with every tool and its guardrails
  * trace         - a step-by-step flow of one real conversation, read from the database

Usage:  python -m app.flowchart          (writes docs/architecture.md with all diagrams)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import ROOT_DIR, Settings, get_settings
from app.db import connect, init_schema, load_messages
from app.tools import TOOLS

CLASSDEFS = """
    classDef channel fill:#E6EEF4,stroke:#1D3D5C,color:#1D3D5C,stroke-width:1px
    classDef core fill:#1D3D5C,stroke:#1D3D5C,color:#FFFFFF,stroke-width:1px
    classDef tool fill:#FFFFFF,stroke:#1D3D5C,color:#1D3D5C,stroke-width:1px
    classDef store fill:#FDF1DC,stroke:#C98A12,color:#5A3E08,stroke-width:1px
    classDef human fill:#FBE3DD,stroke:#B5452F,color:#6E2213,stroke-width:1px
    classDef ok fill:#E3F1EA,stroke:#2E7D5B,color:#174A33,stroke-width:1px
    classDef blocked fill:#FBE3DD,stroke:#B5452F,color:#6E2213,stroke-width:1px
    classDef user fill:#E6EEF4,stroke:#1D3D5C,color:#1D3D5C,stroke-width:1px"""


def _safe(text: str, limit: int = 70) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = text.replace('"', "'").replace("<", "(").replace(">", ")").replace("#", "no. ").replace("`", "'")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _model_label(settings: Settings) -> str:
    if settings.llm_provider.startswith(("anthropic", "claude")):
        return f"Claude: {settings.anthropic_model}"
    return f"OpenAI: {settings.openai_model}"


def architecture_mermaid(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    lines = ["flowchart LR"]
    lines += [
        '    subgraph CH["Customer channels"]',
        '        WEB["Web chat widget"]',
        '        WA["WhatsApp via Twilio"]',
        "    end",
        '    API["FastAPI service"]',
        '    AGENT{{"Agent loop: plan, call tools, reply"}}',
        f'    LLM["{_safe(_model_label(settings))}"]',
        '    subgraph TL["Tools with guardrails enforced in code"]',
    ]
    limit = f"${settings.auto_refund_limit:.0f}"
    window = f"{settings.return_window_days}-day"
    for t in TOOLS:
        g = _safe(", ".join(t.guardrails), 130).replace("$200", limit).replace("30-day", window)
        lines.append(f'        T_{t.name}["{t.name}<br/><small>{g}</small>"]')
    lines += [
        "    end",
        '    KB[("Policy knowledge base")]',
        '    DB[("Store database: orders, refunds, returns")]',
        '    HQ["Human specialist queue"]',
        '    LOG[("Turn logs: latency, tokens, cost")]',
        '    DASH["Ops dashboard + evaluation"]',
        "    WEB --> API",
        "    WA --> API",
        "    API --> AGENT",
        "    AGENT <--> LLM",
    ]
    for t in TOOLS:
        lines.append(f"    AGENT --> T_{t.name}")
        target = {"knowledge": "KB", "read": "DB", "action": "DB", "handoff": "HQ"}[t.group]
        lines.append(f"    T_{t.name} --> {target}")
    lines += ["    AGENT --> LOG", "    LOG --> DASH", "    HQ --> DASH"]
    lines.append(CLASSDEFS)
    lines += [
        "    class WEB,WA channel",
        "    class API,AGENT,LLM core",
        "    class " + ",".join(f"T_{t.name}" for t in TOOLS) + " tool",
        "    class KB,DB,LOG store",
        "    class HQ human",
        "    class DASH channel",
        "    style CH fill:#F4F6F8,stroke:#D5DEE6,color:#5B6B7B",
        "    style TL fill:#F4F6F8,stroke:#D5DEE6,color:#5B6B7B",
    ]
    return "\n".join(lines)


def decision_mermaid(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    reads = [t for t in TOOLS if t.group == "read"]
    actions = [t for t in TOOLS if t.group == "action"]
    lines = [
        "flowchart TD",
        '    MSG(["Customer message"])',
        '    TYPE{"Policy or general question?"}',
        '    KB["search_knowledge_base"]',
        '    DET{"Order number and email provided?"}',
        '    ASK["Ask for the missing detail"]',
        '    VER{"Identity verified by tool?"}',
        '    NEED{"What does the customer need?"}',
        '    GUARD{"Policy guardrails pass?"}',
        '    SPEC{"Needs a specialist?"}',
        '    EXPLAIN["Explain the reason and offer the next best option"]',
        '    HAND["escalate_to_human: ticket with full summary"]',
        '    REPLY(["Reply to customer"])',
        "    MSG --> TYPE",
        "    TYPE -- yes --> KB --> REPLY",
        "    TYPE -- no --> DET",
        "    DET -- no --> ASK --> REPLY",
        "    DET -- yes --> VER",
        "    VER -- no --> ASK",
        "    VER -- yes --> NEED",
    ]
    for t in reads:
        lines.append(f'    NEED -- info --> R_{t.name}["{t.name}"] --> REPLY')
    limit = f"${settings.auto_refund_limit:.0f}"
    window = f"{settings.return_window_days}-day"
    for t in actions:
        g = _safe(", ".join(t.guardrails), 130).replace("$200", limit).replace("30-day", window)
        lines.append(f'    NEED -- change --> A_{t.name}["{t.name}<br/><small>{g}</small>"] --> GUARD')
    lines += [
        "    GUARD -- yes --> REPLY",
        "    GUARD -- no --> SPEC",
        "    SPEC -- yes --> HAND --> REPLY",
        "    SPEC -- no --> EXPLAIN --> REPLY",
        '    MSG -. "asks for a person or is very upset" .-> HAND',
        CLASSDEFS,
        "    class MSG,REPLY user",
        "    class TYPE,DET,VER,NEED,GUARD,SPEC core",
        "    class KB," + ",".join([f"R_{t.name}" for t in reads] + [f"A_{t.name}" for t in actions]) + " tool",
        "    class HAND human",
        "    class ASK,EXPLAIN channel",
    ]
    return "\n".join(lines)


def trace_mermaid(settings: Settings, conversation_id: str) -> str | None:
    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        messages = load_messages(conn, conversation_id)
    finally:
        conn.close()
    if not messages:
        return None
    lines = ["flowchart TD"]
    prev, n, step = None, 0, 0
    classes: dict[str, list[str]] = {"user": [], "core": [], "ok": [], "blocked": []}
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}

    def add(prefix: str, label: str, shape: str, cls: str):
        nonlocal prev, step
        step += 1
        node_id = f"{prefix}{step}"
        open_, close_ = {"round": ("([", "])"), "box": ('["', '"]'), "hex": ("{{", "}}")}[shape]
        if shape == "box":
            lines.append(f"    {node_id}{open_}{label}{close_}")
        else:
            lines.append(f'    {node_id}{open_}"{label}"{close_}')
        if prev:
            lines.append(f"    {prev} --> {node_id}")
        classes[cls].append(node_id)
        prev = node_id

    for m in messages:
        if m["role"] == "user":
            n += 1
            add("U", f"Customer: {_safe(m['content'], 60)}", "round", "user")
        elif m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                res = results.get(tc["id"], {})
                ok = res.get("ok", False)
                status = "ok" if ok else f"blocked: {res.get('code', 'error')}"
                add("T", f"{tc['name']}<br/><small>{_safe(status, 40)}</small>",
                    "box", "ok" if ok else "blocked")
            if m.get("content") and not m.get("tool_calls"):
                add("A", f"Assistant: {_safe(m['content'], 60)}", "round", "core")
    lines.append(CLASSDEFS)
    for cls, ids in classes.items():
        if ids:
            lines.append(f"    class {','.join(ids)} {cls}")
    return "\n".join(lines)


def latest_conversation_id(settings: Settings) -> str | None:
    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        row = conn.execute("SELECT conversation_id FROM conversations ORDER BY updated_at DESC LIMIT 1").fetchone()
        return row["conversation_id"] if row else None
    finally:
        conn.close()


def write_docs(settings: Settings | None = None, path: Path | None = None) -> Path:
    settings = settings or get_settings()
    path = path or ROOT_DIR / "docs" / "architecture.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "# ShopAssist architecture",
        "",
        "These diagrams are generated from the code by `python -m app.flowchart`. Do not edit by hand.",
        "",
        "## System architecture",
        "",
        "```mermaid",
        architecture_mermaid(settings),
        "```",
        "",
        "## Agent decision flow",
        "",
        "```mermaid",
        decision_mermaid(settings),
        "```",
        "",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


if __name__ == "__main__":
    print(f"Wrote {write_docs()}")
