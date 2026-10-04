"""Builds the dashboard payload: evaluation results (offline) + live operations metrics (from turn logs)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.config import Settings
from app.db import connect, init_schema


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    k = (len(vals) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return round(vals[lo] + (vals[hi] - vals[lo]) * (k - lo), 1)


def load_eval_results(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def live_metrics(settings: Settings) -> dict:
    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        convs = conn.execute("SELECT outcome, channel FROM conversations").fetchall()
        turns = conn.execute("SELECT latency_ms, cost_usd, tool_calls, error FROM turn_logs").fetchall()
        tickets = conn.execute(
            "SELECT id, order_id, reason, priority, summary, status, created_at FROM tickets "
            "ORDER BY id DESC LIMIT 8").fetchall()
        recent = conn.execute(
            "SELECT conversation_id, channel, outcome, updated_at FROM conversations "
            "ORDER BY updated_at DESC LIMIT 6").fetchall()
        refunds = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM refunds "
                               "WHERE conversation_id IS NOT NULL").fetchone()
    finally:
        conn.close()

    tool_counter: Counter = Counter()
    for t in turns:
        for call in json.loads(t["tool_calls"] or "[]"):
            tool_counter[call["name"]] += 1
    latencies = [t["latency_ms"] for t in turns]
    total_cost = sum(t["cost_usd"] for t in turns)
    n_conv = len(convs)
    escalated = sum(1 for c in convs if c["outcome"] == "escalated")
    return {
        "conversations": n_conv,
        "turns": len(turns),
        "errors": sum(1 for t in turns if t["error"]),
        "escalation_rate": round(escalated / n_conv, 3) if n_conv else None,
        "handled_by_ai": n_conv - escalated,
        "latency_avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "latency_p95_ms": percentile(latencies, 95),
        "total_cost_usd": round(total_cost, 4),
        "cost_per_conversation_usd": round(total_cost / n_conv, 5) if n_conv else None,
        "channels": dict(Counter(c["channel"] for c in convs)),
        "tool_usage": dict(tool_counter.most_common()),
        "refunds_issued": refunds["n"],
        "refund_value_usd": round(refunds["s"], 2),
        "tickets": [dict(t) | {"ticket_id": f"TCK-{t['id']:05d}"} for t in tickets],
        "recent_conversations": [dict(r) for r in recent],
    }


def guardrail_list() -> list[str]:
    from app.tools import TOOLS
    seen: list[str] = []
    for t in TOOLS:
        if t.group in ("read", "action"):
            for g in t.guardrails:
                if g not in seen:
                    seen.append(g)
    return seen


def dashboard_payload(settings: Settings) -> dict:
    model = settings.anthropic_model if settings.llm_provider.startswith(("anthropic", "claude")) else settings.openai_model
    return {
        "store": settings.store_name,
        "provider": settings.llm_provider,
        "model": model,
        "policy": {"auto_refund_limit": settings.auto_refund_limit, "return_window_days": settings.return_window_days},
        "tool_count": len(__import__("app.tools", fromlist=["TOOLS"]).TOOLS),
        "guardrails": [g.replace("$200", f"${settings.auto_refund_limit:.0f}")
                       .replace("30-day", f"{settings.return_window_days}-day") for g in guardrail_list()],
        "eval": load_eval_results(settings.eval_results_path),
        "live": live_metrics(settings),
    }
