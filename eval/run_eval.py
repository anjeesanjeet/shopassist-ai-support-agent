"""Evaluation harness: runs every scenario against a fresh copy of the store database and scores the agent.

Usage:
    python -m eval.run_eval                       # all scenarios, provider from .env
    python -m eval.run_eval --provider openai     # compare providers
    python -m eval.run_eval --only Refunds        # one category
    python -m eval.run_eval --judge               # add an LLM-as-judge score for reply quality

Writes outputs/eval_results.json (read by the dashboard) and outputs/eval_report.md (for the case study).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from app.agent import SupportAgent
from app.config import ROOT_DIR, get_settings
from app.db import connect, load_messages
from app.llm import BaseProvider, ProviderConfigError, get_provider
from app.metrics import percentile
from app.seed import build_database

SCENARIOS = ROOT_DIR / "eval" / "scenarios.json"
POLICY_DB_CHECKS = {"no_refund", "no_return", "refund_count"}  # plus any check marked "policy": true

JUDGE_PROMPT = """You grade customer support replies for an online home and kitchen store.
You see the full transcript, including the store system's tool calls and their results.
Tool results are verified store data: order details, tracking numbers, ticket and return numbers that match
the tool results are correct, not fabricated, and identity was verified if a tool returned order data.
Grade the assistant's final reply from 1 to 5 on: accuracy against the tool results and store policy;
not promising anything a tool did not confirm; empathy appropriate to the customer's situation;
clarity and brevity; plain text suitable for chat (no markdown).
5 = excellent on all; 4 = good with a minor issue; 3 = acceptable but vague, wordy or off in tone;
2 = partly wrong, misleading or unhelpful; 1 = wrong, unsafe or rude.
Return only JSON: {"score": <1-5>, "reason": "<one sentence>"}"""


# ---------------------------------------------------------------- checks

def _db_check(conn, check: dict) -> tuple[bool, str]:
    kind, oid = check["check"], check.get("order_id")
    if kind in ("refund_exists", "no_refund", "refund_count"):
        n = conn.execute("SELECT COUNT(*) n FROM refunds WHERE order_id = ?", (oid,)).fetchone()["n"]
        if kind == "refund_exists":
            return n >= 1, f"refunds for {oid}: {n}"
        if kind == "no_refund":
            return n == 0, f"refunds for {oid}: {n}"
        return n == check["count"], f"refunds for {oid}: {n} (expected {check['count']})"
    if kind in ("return_exists", "no_return"):
        q, params = "SELECT COUNT(*) n FROM returns WHERE order_id = ?", [oid]
        if check.get("sku"):
            q += " AND sku = ?"
            params.append(check["sku"])
        n = conn.execute(q, params).fetchone()["n"]
        return (n >= 1 if kind == "return_exists" else n == 0), f"returns for {oid}: {n}"
    if kind == "order_status":
        row = conn.execute("SELECT status FROM orders WHERE order_id = ?", (oid,)).fetchone()
        status = row["status"] if row else None
        return status == check["status"], f"status of {oid}: {status} (expected {check['status']})"
    if kind == "ticket_exists":
        n = conn.execute("SELECT COUNT(*) n FROM tickets").fetchone()["n"]
        return n >= 1, f"tickets: {n}"
    return False, f"unknown check {kind}"


def score_scenario(scn: dict, tools_used: list[str], escalated: bool, replies: list[str], db_path: Path) -> list[dict]:
    exp, checks = scn["expect"], []
    final = (replies[-1] if replies else "").lower()
    all_replies = " ".join(replies).lower()
    for t in exp.get("tools_called", []):
        checks.append({"name": f"called {t}", "passed": t in tools_used, "policy": False, "type": "tool"})
    if exp.get("tools_called_any"):
        ok = any(t in tools_used for t in exp["tools_called_any"])
        checks.append({"name": "called one of " + "/".join(exp["tools_called_any"]), "passed": ok,
                       "policy": False, "type": "tool"})
    for t in exp.get("tools_not_called", []):
        checks.append({"name": f"did not call {t}", "passed": t not in tools_used, "policy": False, "type": "tool"})
    if "escalated" in exp:
        checks.append({"name": "escalated" if exp["escalated"] else "not escalated",
                       "passed": escalated == exp["escalated"], "policy": False, "type": "escalation"})
    if exp.get("reply_contains_any"):
        ok = any(k.lower() in final for k in exp["reply_contains_any"])
        checks.append({"name": "reply mentions key fact", "passed": ok, "policy": False, "type": "reply"})
    for k in exp.get("reply_not_contains", []):
        checks.append({"name": f"no leak of '{k}'", "passed": k.lower() not in all_replies, "policy": True, "type": "reply"})
    if exp.get("db"):
        conn = connect(db_path)
        try:
            for c in exp["db"]:
                ok, detail = _db_check(conn, c)
                checks.append({"name": c["check"], "passed": ok, "detail": detail,
                               "policy": c["check"] in POLICY_DB_CHECKS or bool(c.get("policy")), "type": "state"})
        finally:
            conn.close()
    return checks


def build_transcript(db_path: Path, conversation_id: str) -> str:
    """Full transcript with tool calls and (truncated) tool results, so the judge sees the verified data."""
    conn = connect(db_path)
    try:
        messages = load_messages(conn, conversation_id)
    finally:
        conn.close()
    lines = []
    for m in messages:
        if m["role"] == "user":
            lines.append(f"Customer: {m['content']}")
        elif m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                lines.append(f"[Tool call] {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})")
            if m.get("content"):
                lines.append(f"Assistant: {m['content']}")
        elif m["role"] == "tool":
            lines.append(f"[Tool result] {m['content'][:700]}")
    return "\n".join(lines)


def judge_reply(provider: BaseProvider, transcript: str) -> dict | None:
    try:
        resp = provider.complete(JUDGE_PROMPT, [{"role": "user", "content": transcript}], [])
        match = re.search(r"\{.*\}", resp.text or "", re.S)
        data = json.loads(match.group(0)) if match else {}
        score = int(data.get("score", 0))
        if 1 <= score <= 5:
            return {"score": score, "reason": str(data.get("reason", ""))[:300],
                    "cost_usd": provider.cost(resp.input_tokens, resp.output_tokens)}
    except Exception as exc:  # judge failures never fail the eval
        return {"score": None, "reason": f"judge error: {type(exc).__name__}"}
    return None


# ---------------------------------------------------------------- runner

def run(provider_name: str | None, only: str | None, limit: int | None, use_judge: bool) -> dict:
    settings = get_settings()
    provider = get_provider(settings, provider_name)
    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if only:
        scenarios = [s for s in scenarios if s["category"].lower() == only.lower() or s["id"] == only]
    if limit:
        scenarios = scenarios[:limit]
    if not scenarios:
        sys.exit("No scenarios matched.")

    work = settings.outputs_dir / "eval_work"
    work.mkdir(parents=True, exist_ok=True)
    today = date.today()
    template = build_database(work / "template.db", today=today)

    results, turn_latencies = [], []
    print(f"\nRunning {len(scenarios)} scenarios with {provider.name}:{provider.model}\n")
    for i, scn in enumerate(scenarios, 1):
        db_path = work / "run.db"
        shutil.copyfile(template, db_path)
        agent = SupportAgent(provider, settings, db_path=db_path, today=today)
        cid = f"eval-{scn['id']}"
        replies, tools_used, lat, cost, tokens, errors = [], [], [], 0.0, 0, []
        escalated = False
        for turn in scn["turns"]:
            res = agent.handle_message(cid, turn, channel="eval")
            replies.append(res.reply)
            tools_used += [t["name"] for t in res.tool_calls]
            escalated = escalated or res.outcome == "escalated"
            lat.append(res.latency_ms)
            cost += res.cost_usd
            tokens += res.input_tokens + res.output_tokens
            if res.error:
                errors.append(res.error)
        checks = score_scenario(scn, tools_used, escalated, replies, db_path)
        if errors:
            checks.append({"name": "no runtime errors", "passed": False, "policy": False, "type": "error",
                           "detail": errors[0]})
        passed = all(c["passed"] for c in checks)
        judge = judge_reply(provider, build_transcript(db_path, cid)) if use_judge else None
        turn_latencies += lat
        results.append({
            "id": scn["id"], "category": scn["category"], "passed": passed, "checks": checks,
            "tools": tools_used, "escalated": escalated, "expected_escalation": scn["expect"].get("escalated"),
            "turn_latency_ms": lat, "cost_usd": round(cost, 6), "tokens": tokens,
            "final_reply": replies[-1] if replies else "", "errors": errors, "judge": judge,
        })
        mark = "PASS" if passed else "FAIL"
        failed = [c["name"] for c in checks if not c["passed"]]
        print(f"  [{i:>2}/{len(scenarios)}] {mark}  {scn['id']:<26} {sum(lat)/1000:>5.1f}s  ${cost:.4f}"
              + (f"   failed: {', '.join(failed)}" if failed else ""))
        time.sleep(0.2)

    summary = summarize(results, turn_latencies)
    by_cat: dict[str, list] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["passed"])
    payload = {
        "meta": {"provider": provider.name, "model": provider.model, "store": settings.store_name,
                 "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                 "scenarios": len(results), "judge": use_judge,
                 "policy": {"auto_refund_limit": settings.auto_refund_limit,
                            "return_window_days": settings.return_window_days}},
        "summary": summary,
        "by_category": [{"category": c, "n": len(v), "passed": sum(v), "success_rate": round(sum(v) / len(v), 3)}
                        for c, v in by_cat.items()],
        "scenarios": results,
    }
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.eval_results_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (settings.outputs_dir / "eval_report.md").write_text(render_report(payload), encoding="utf-8")
    return payload


def summarize(results: list[dict], turn_latencies: list[float]) -> dict:
    n = len(results)
    tool_checks = [c for r in results for c in r["checks"] if c["type"] == "tool"]
    with_tools = [r for r in results if any(c["type"] == "tool" for c in r["checks"])]
    tool_ok_scn = [all(c["passed"] for c in r["checks"] if c["type"] == "tool") for r in with_tools]
    policy_fail = [r["id"] for r in results if any(c["policy"] and not c["passed"] for c in r["checks"])]
    policy_checks = sum(1 for r in results for c in r["checks"] if c["policy"])
    esc = [r for r in results if r["expected_escalation"] is not None]
    tp = sum(1 for r in esc if r["expected_escalation"] and r["escalated"])
    fp = sum(1 for r in esc if not r["expected_escalation"] and r["escalated"])
    fn = sum(1 for r in esc if r["expected_escalation"] and not r["escalated"])
    judged = [r["judge"]["score"] for r in results if r.get("judge") and r["judge"].get("score")]
    costs = [r["cost_usd"] for r in results]
    return {
        "task_success_rate": round(sum(r["passed"] for r in results) / n, 3),
        "passed": sum(r["passed"] for r in results),
        "total": n,
        "tool_selection_accuracy": round(sum(tool_ok_scn) / len(tool_ok_scn), 3) if tool_ok_scn else None,
        "tool_checks": len(tool_checks),
        "policy_violations": len(policy_fail),
        "policy_violation_ids": policy_fail,
        "policy_checks": policy_checks,
        "escalation_accuracy": round(sum(1 for r in esc if r["expected_escalation"] == r["escalated"]) / len(esc), 3) if esc else None,
        "escalation_precision": round(tp / (tp + fp), 3) if (tp + fp) else None,
        "escalation_recall": round(tp / (tp + fn), 3) if (tp + fn) else None,
        "ai_resolution_rate": round(sum(1 for r in results if not r["escalated"]) / n, 3),
        "latency_avg_ms": round(sum(turn_latencies) / len(turn_latencies), 1) if turn_latencies else None,
        "latency_p50_ms": percentile(turn_latencies, 50),
        "latency_p95_ms": percentile(turn_latencies, 95),
        "cost_per_conversation_usd": round(sum(costs) / n, 5),
        "total_cost_usd": round(sum(costs), 4),
        "avg_tokens_per_conversation": round(sum(r["tokens"] for r in results) / n),
        "judge_avg": round(sum(judged) / len(judged), 2) if judged else None,
        "runtime_errors": sum(1 for r in results if r["errors"]),
    }


def render_report(p: dict) -> str:
    s, m = p["summary"], p["meta"]
    lines = [
        "# ShopAssist evaluation report", "",
        f"Model: `{m['provider']}:{m['model']}`  |  Scenarios: {m['scenarios']}  |  Run: {m['run_at']}", "",
        "| Metric | Result |", "|---|---|",
        f"| Task success rate | {s['task_success_rate']:.0%} ({s['passed']}/{s['total']}) |",
        f"| Tool selection accuracy | {s['tool_selection_accuracy']:.0%} |" if s["tool_selection_accuracy"] is not None else "| Tool selection accuracy | n/a |",
        f"| Policy violations | {s['policy_violations']} (across {s['policy_checks']} policy checks) |",
        f"| Escalation accuracy | {s['escalation_accuracy']:.0%} |" if s["escalation_accuracy"] is not None else "| Escalation accuracy | n/a |",
        f"| Resolved without a human | {s['ai_resolution_rate']:.0%} |",
        f"| Latency per turn, avg / p95 | {s['latency_avg_ms']/1000:.1f}s / {s['latency_p95_ms']/1000:.1f}s |",
        f"| Cost per conversation | ${s['cost_per_conversation_usd']:.4f} |",
    ]
    if s.get("judge_avg"):
        lines.append(f"| Reply quality (LLM judge, 1-5) | {s['judge_avg']} |")
    lines += ["", "## By category", "", "| Category | Passed | Rate |", "|---|---|---|"]
    lines += [f"| {c['category']} | {c['passed']}/{c['n']} | {c['success_rate']:.0%} |" for c in p["by_category"]]
    fails = [r for r in p["scenarios"] if not r["passed"]]
    lines += ["", "## Failures", ""]
    if not fails:
        lines.append("None.")
    for r in fails:
        bad = "; ".join(f"{c['name']}" + (f" ({c['detail']})" if c.get("detail") else "") for c in r["checks"] if not c["passed"])
        lines.append(f"- **{r['id']}**: {bad}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate the ShopAssist agent.")
    ap.add_argument("--provider", choices=["anthropic", "openai"], default=None)
    ap.add_argument("--only", default=None, help="Category name or scenario id")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--judge", action="store_true", help="Add an LLM-as-judge reply quality score")
    args = ap.parse_args()
    try:
        payload = run(args.provider, args.only, args.limit, args.judge)
    except ProviderConfigError as exc:
        sys.exit(f"Configuration error: {exc}")
    s = payload["summary"]
    print(f"\nTask success {s['task_success_rate']:.0%}  |  policy violations {s['policy_violations']}  |  "
          f"p95 latency {s['latency_p95_ms']/1000:.1f}s  |  ${s['cost_per_conversation_usd']:.4f}/conversation")
    print(f"Saved outputs/eval_results.json and outputs/eval_report.md")


if __name__ == "__main__":
    main()