"""Agent tools.

Every business rule (identity verification, return window, refund limit, final-sale items,
duplicate refunds) is enforced HERE in code. The LLM decides *which* tool to call, but it can never
approve something the policy does not allow, even under prompt injection.
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from app.config import Settings
from app.db import now_iso, set_outcome
from app.knowledge import get_knowledge_base


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    settings: Settings
    conversation_id: str
    today: date = field(default_factory=date.today)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Callable[[ToolContext, dict], dict]
    group: str                       # used for flowcharts: knowledge | read | action | handoff
    guardrails: list[str] = field(default_factory=list)


VERIFY_FAIL = {
    "ok": False,
    "code": "verification_failed",
    "message": "No order matches that order number and email combination. Ask the customer to double-check "
               "both. Do not reveal whether the order number exists.",
}


def _norm_order(order_id: str) -> str:
    oid = (order_id or "").strip().upper().replace(" ", "")
    if oid and not oid.startswith("ORD-") and oid.replace("ORD", "").isdigit():
        oid = "ORD-" + oid.replace("ORD", "")
    return oid


def _verify(ctx: ToolContext, args: dict) -> tuple[sqlite3.Row | None, dict | None]:
    order_id, email = _norm_order(args.get("order_id", "")), (args.get("email") or "").strip().lower()
    if not order_id or not email:
        return None, {"ok": False, "code": "missing_details",
                      "message": "Both the order number and the checkout email are required."}
    row = ctx.conn.execute(
        """SELECT o.*, c.name AS customer_name, c.email AS customer_email
           FROM orders o JOIN customers c ON c.customer_id = o.customer_id
           WHERE o.order_id = ? AND lower(c.email) = ?""",
        (order_id, email),
    ).fetchone()
    return (row, None) if row else (None, VERIFY_FAIL)


def _items(ctx: ToolContext, order_id: str) -> list[dict]:
    rows = ctx.conn.execute(
        """SELECT i.sku, p.name, p.category, i.qty, i.unit_price, p.final_sale
           FROM order_items i JOIN products p ON p.sku = i.sku WHERE i.order_id = ?""", (order_id,)
    ).fetchall()
    return [{"sku": r["sku"], "name": r["name"], "qty": r["qty"], "unit_price": r["unit_price"],
             "final_sale": bool(r["final_sale"])} for r in rows]


def _days_since(day_iso: str | None, today: date) -> int | None:
    return None if not day_iso else (today - date.fromisoformat(day_iso)).days


def _refunded_amount(ctx: ToolContext, order_id: str, sku: str | None = None) -> float:
    q = "SELECT COALESCE(SUM(amount),0) AS s FROM refunds WHERE order_id = ?"
    params: list[Any] = [order_id]
    if sku:
        q += " AND (sku = ? OR sku IS NULL)"
        params.append(sku)
    return float(ctx.conn.execute(q, params).fetchone()["s"])


# ---------------------------------------------------------------- handlers

def search_knowledge_base(ctx: ToolContext, args: dict) -> dict:
    kb = get_knowledge_base(str(ctx.settings.knowledge_dir))
    hits = kb.search(args.get("query", ""), k=3)
    if not hits:
        return {"ok": True, "results": [], "message": "No policy text matched. Do not guess; offer a specialist."}
    return {"ok": True, "results": hits}


def lookup_order(ctx: ToolContext, args: dict) -> dict:
    order, err = _verify(ctx, args)
    if err:
        return err
    window_end = None
    if order["delivery_date"]:
        window_end = (date.fromisoformat(order["delivery_date"]) + timedelta(days=ctx.settings.return_window_days)).isoformat()
    return {
        "ok": True,
        "order_id": order["order_id"],
        "customer_first_name": order["customer_name"].split()[0],
        "status": order["status"],
        "order_date": order["order_date"],
        "ship_date": order["ship_date"],
        "delivery_date": order["delivery_date"],
        "carrier": order["carrier"],
        "tracking_number": order["tracking_number"],
        "total": order["total"],
        "items": _items(ctx, order["order_id"]),
        "can_cancel": order["status"] == "processing",
        "return_window_ends": window_end,
        "refunded_so_far": _refunded_amount(ctx, order["order_id"]),
    }


def track_shipment(ctx: ToolContext, args: dict) -> dict:
    order, err = _verify(ctx, args)
    if err:
        return err
    if order["status"] == "processing":
        return {"ok": True, "status": "processing", "message": "The order has not shipped yet; it usually ships within 1 business day."}
    events = ctx.conn.execute(
        "SELECT ts, location, status FROM tracking_events WHERE order_id = ? ORDER BY ts", (order["order_id"],)
    ).fetchall()
    latest = events[-1]["status"] if events else "No scans yet"
    estimate = None
    if order["status"] == "shipped" and order["ship_date"]:
        delayed = "delay" in latest.lower()
        estimate = None if delayed else (date.fromisoformat(order["ship_date"]) + timedelta(days=5)).isoformat()
    return {
        "ok": True,
        "status": order["status"],
        "carrier": order["carrier"],
        "tracking_number": order["tracking_number"],
        "latest_event": latest,
        "estimated_delivery": estimate,
        "events": [dict(e) for e in events],
    }


def cancel_order(ctx: ToolContext, args: dict) -> dict:
    order, err = _verify(ctx, args)
    if err:
        return err
    if order["status"] != "processing":
        return {"ok": False, "code": "cannot_cancel", "status": order["status"],
                "message": f"Order is '{order['status']}'. Only orders that have not shipped can be cancelled. "
                           "If shipped, the customer can return it after delivery."}
    ctx.conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order["order_id"],))
    ctx.conn.execute(
        "INSERT INTO refunds (order_id, sku, amount, reason, created_at, conversation_id) VALUES (?,?,?,?,?,?)",
        (order["order_id"], None, order["total"], "order cancelled before shipping", now_iso(), ctx.conversation_id),
    )
    ctx.conn.commit()
    return {"ok": True, "order_id": order["order_id"], "status": "cancelled", "refund_amount": order["total"],
            "message": "Cancelled. Full refund to the original payment method in 5-7 business days."}


def _eligibility(ctx: ToolContext, order: sqlite3.Row, item: dict | None) -> dict | None:
    """Shared return/refund policy checks. Returns an error dict, or None if eligible."""
    if order["status"] in ("cancelled", "refunded"):
        return {"ok": False, "code": "already_closed", "message": f"Order is already {order['status']}."}
    if order["status"] != "delivered":
        return {"ok": False, "code": "not_delivered",
                "message": "Order has not been delivered yet. Returns and refunds start after delivery; "
                           "if it has not shipped it can be cancelled instead."}
    days = _days_since(order["delivery_date"], ctx.today)
    if days is not None and days > ctx.settings.return_window_days:
        return {"ok": False, "code": "outside_return_window", "days_since_delivery": days,
                "message": f"Delivered {days} days ago; the return window is {ctx.settings.return_window_days} days. "
                           "Not eligible for return/refund. Appliances may still be covered by warranty (repair/replace)."}
    if item and item["final_sale"]:
        return {"ok": False, "code": "final_sale",
                "message": "This item is final sale (clearance) and cannot be returned or refunded unless it arrived "
                           "damaged and was reported within 7 days with photos. Escalate if the customer reports damage."}
    return None


_ITEM_STOP = {"the", "and", "set", "of", "my", "item", "order", "with", "for", "from"}


def _words(text: str) -> set[str]:
    out = set()
    for w in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split():
        if len(w) >= 3 and w not in _ITEM_STOP:
            out.add(w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return out


def _pick_item(items: list[dict], sku: str | None) -> tuple[dict | None, dict | None]:
    """Finds the item by exact SKU, or by the item name as the customer describes it ("mug set")."""
    if sku:
        match = [i for i in items if i["sku"].upper() == sku.strip().upper()]
        if not match:
            wanted = _words(sku)
            scored = [(len(wanted & _words(i["name"])), i) for i in items]
            best = max((n for n, _ in scored), default=0)
            match = [i for n, i in scored if best > 0 and n == best]
        if len(match) != 1:
            return None, {"ok": False, "code": "item_not_in_order", "items": items,
                          "message": "Could not identify a single item from that description. Ask the customer "
                                     "which of the listed items they mean."}
        return match[0], None
    if len(items) == 1:
        return items[0], None
    return None, None   # whole order


def initiate_return(ctx: ToolContext, args: dict) -> dict:
    order, err = _verify(ctx, args)
    if err:
        return err
    items = _items(ctx, order["order_id"])
    if not args.get("sku") and len(items) > 1:
        return {"ok": False, "code": "sku_required", "items": items,
                "message": "Order has multiple items. If the customer already named the item, call again now with "
                           "its SKU from this list. Only ask the customer if it is unclear."}
    item, err = _pick_item(items, args.get("sku"))
    if err:
        return err
    err = _eligibility(ctx, order, item)
    if err:
        return err
    exists = ctx.conn.execute("SELECT rma FROM returns WHERE order_id = ? AND sku = ?",
                              (order["order_id"], item["sku"])).fetchone()
    if exists:
        return {"ok": True, "rma": exists["rma"], "already_existed": True,
                "message": "A return for this item already exists; the label was emailed."}
    rma = "RMA-" + secrets.token_hex(3).upper()
    ctx.conn.execute(
        "INSERT INTO returns (rma, order_id, sku, reason, created_at, conversation_id) VALUES (?,?,?,?,?,?)",
        (rma, order["order_id"], item["sku"], args.get("reason", ""), now_iso(), ctx.conversation_id),
    )
    ctx.conn.commit()
    return {"ok": True, "rma": rma, "item": item["name"],
            "message": "Prepaid return label emailed. Refund is issued when the warehouse receives the item."}


def issue_refund(ctx: ToolContext, args: dict) -> dict:
    order, err = _verify(ctx, args)
    if err:
        return err
    items = _items(ctx, order["order_id"])
    item, err = _pick_item(items, args.get("sku"))
    if err:
        return err
    err = _eligibility(ctx, order, item)
    if err:
        return err
    if item is None and any(i["final_sale"] for i in items):
        return {"ok": False, "code": "sku_required", "items": items,
                "message": "Order contains final-sale items. Ask which item is being refunded and pass its SKU."}
    amount = round(item["unit_price"] * item["qty"], 2) if item else float(order["total"])
    if _refunded_amount(ctx, order["order_id"], item["sku"] if item else None) > 0:
        return {"ok": False, "code": "already_refunded",
                "message": "A refund for this has already been issued. Do not issue another one."}
    if amount > ctx.settings.auto_refund_limit:
        return {"ok": False, "code": "requires_human_review", "amount": amount,
                "limit": ctx.settings.auto_refund_limit,
                "message": f"Refund of ${amount:.2f} exceeds the ${ctx.settings.auto_refund_limit:.0f} automatic limit. "
                           "Call escalate_to_human with priority 'high' so a specialist approves it within 1 business day."}
    ctx.conn.execute(
        "INSERT INTO refunds (order_id, sku, amount, reason, created_at, conversation_id) VALUES (?,?,?,?,?,?)",
        (order["order_id"], item["sku"] if item else None, amount, args.get("reason", ""), now_iso(), ctx.conversation_id),
    )
    if item is None or len(items) == 1:
        ctx.conn.execute("UPDATE orders SET status = 'refunded' WHERE order_id = ?", (order["order_id"],))
    ctx.conn.commit()
    return {"ok": True, "order_id": order["order_id"], "amount": amount,
            "message": "Refund issued to the original payment method. Banks take 5-7 business days to show it."}


def escalate_to_human(ctx: ToolContext, args: dict) -> dict:
    priority = (args.get("priority") or "normal").lower()
    if priority not in {"low", "normal", "high", "urgent"}:
        priority = "normal"
    cur = ctx.conn.execute(
        "INSERT INTO tickets (conversation_id, order_id, reason, priority, summary, created_at) VALUES (?,?,?,?,?,?)",
        (ctx.conversation_id, _norm_order(args.get("order_id", "")) or None, args.get("reason", "unspecified"),
         priority, args.get("summary", ""), now_iso()),
    )
    ctx.conn.commit()
    set_outcome(ctx.conn, ctx.conversation_id, "escalated")
    ticket = f"TCK-{cur.lastrowid:05d}"
    sla = "within 1 business day" if priority in ("normal", "low") else "within 4 business hours"
    return {"ok": True, "ticket_id": ticket, "priority": priority,
            "message": f"Ticket created. A specialist will reply {sla} (Mon-Fri 8am-8pm ET, Sat 9am-5pm ET)."}


# ---------------------------------------------------------------- registry

_ORDER_ARGS = {
    "order_id": {"type": "string", "description": "Order number, e.g. ORD-1001"},
    "email": {"type": "string", "description": "Email address used at checkout"},
}

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="search_knowledge_base",
        description="Search the store's policy documents (shipping, returns, refunds, warranty, payments, FAQ). "
                    "Use for any policy or general question before answering.",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}},
                    "required": ["query"]},
        handler=search_knowledge_base, group="knowledge",
        guardrails=["Answers must come from retrieved policy text"],
    ),
    ToolSpec(
        name="lookup_order",
        description="Get order status, items, dates and return window. Requires the order number AND checkout email.",
        parameters={"type": "object", "properties": dict(_ORDER_ARGS), "required": ["order_id", "email"]},
        handler=lookup_order, group="read",
        guardrails=["Identity verified"],
    ),
    ToolSpec(
        name="track_shipment",
        description="Get carrier tracking events and the delivery estimate for a shipped order. Requires order number AND email.",
        parameters={"type": "object", "properties": dict(_ORDER_ARGS), "required": ["order_id", "email"]},
        handler=track_shipment, group="read",
        guardrails=["Identity verified"],
    ),
    ToolSpec(
        name="cancel_order",
        description="Cancel an order that has not shipped yet and refund it in full. Confirm with the customer first.",
        parameters={"type": "object", "properties": dict(_ORDER_ARGS), "required": ["order_id", "email"]},
        handler=cancel_order, group="action",
        guardrails=["Identity verified", "Only before shipping"],
    ),
    ToolSpec(
        name="initiate_return",
        description="Create a return and email a prepaid label for one item of a delivered order.",
        parameters={"type": "object", "properties": {
            **_ORDER_ARGS,
            "sku": {"type": "string", "description": "SKU of the item, or the item name as the customer described it "
                                                     "(e.g. 'mug set'). Required if the order has several items."},
            "reason": {"type": "string", "description": "Customer's reason for the return"},
        }, "required": ["order_id", "email", "reason"]},
        handler=initiate_return, group="action",
        guardrails=["Identity verified", "Within 30-day window", "Not final sale"],
    ),
    ToolSpec(
        name="issue_refund",
        description="Refund a delivered order or one item of it to the original payment method (e.g. damaged or "
                    "defective items). The system enforces policy and amount limits and explains any refusal.",
        parameters={"type": "object", "properties": {
            **_ORDER_ARGS,
            "sku": {"type": "string", "description": "SKU or item name (e.g. 'blender') of a single item to refund; "
                                                     "omit to refund the whole order"},
            "reason": {"type": "string", "description": "Why the refund is being issued"},
        }, "required": ["order_id", "email", "reason"]},
        handler=issue_refund, group="action",
        guardrails=["Identity verified", "Within 30-day window", "Not final sale", "Max $200 auto-approval",
                    "No duplicate refunds"],
    ),
    ToolSpec(
        name="escalate_to_human",
        description="Hand the conversation to a human specialist by creating a support ticket. Use when the customer "
                    "asks for a person, a refund needs review, the issue is unresolved, or the customer is very upset.",
        parameters={"type": "object", "properties": {
            "reason": {"type": "string", "description": "Short reason for escalation"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "summary": {"type": "string", "description": "Summary of the case so the specialist needs no repeat questions"},
            "order_id": {"type": "string", "description": "Related order number, if any"},
        }, "required": ["reason", "priority", "summary"]},
        handler=escalate_to_human, group="handoff",
        guardrails=["Creates ticket with full context"],
    ),
]

TOOL_MAP = {t.name: t for t in TOOLS}


def execute_tool(ctx: ToolContext, name: str, args: dict) -> dict:
    spec = TOOL_MAP.get(name)
    if not spec:
        return {"ok": False, "code": "unknown_tool", "message": f"Unknown tool '{name}'."}
    try:
        return spec.handler(ctx, args or {})
    except Exception as exc:  # never crash the conversation because of a tool
        return {"ok": False, "code": "tool_error", "message": f"Internal error: {type(exc).__name__}"}