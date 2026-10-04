from app.db import connect
from app.tools import ToolContext, execute_tool


def ctx(settings):
    return ToolContext(conn=connect(settings.db_path), settings=settings, conversation_id="t1")


def test_verification_blocks_wrong_email(settings):
    out = execute_tool(ctx(settings), "lookup_order", {"order_id": "ORD-1004", "email": "priya.sharma@example.com"})
    assert out["ok"] is False and out["code"] == "verification_failed"
    assert "espresso" not in str(out).lower()


def test_lookup_normalises_order_id(settings):
    out = execute_tool(ctx(settings), "lookup_order", {"order_id": "ord 1003", "email": " Ananya.Iyer@example.com "})
    assert out["ok"] and out["status"] == "processing" and out["can_cancel"]


def test_refund_within_policy_then_no_duplicate(settings):
    c = ctx(settings)
    args = {"order_id": "ORD-1001", "email": "priya.sharma@example.com", "reason": "arrived broken"}
    first = execute_tool(c, "issue_refund", args)
    assert first["ok"] and first["amount"] == 89.0
    second = execute_tool(c, "issue_refund", args)
    assert second["ok"] is False and second["code"] in ("already_refunded", "already_closed")


def test_refund_over_limit_requires_human(settings):
    out = execute_tool(ctx(settings), "issue_refund",
                       {"order_id": "ORD-1004", "email": "michael.chen@example.com", "reason": "defective"})
    assert out["code"] == "requires_human_review"


def test_refund_outside_window(settings):
    out = execute_tool(ctx(settings), "issue_refund",
                       {"order_id": "ORD-1002", "email": "rahul.verma@example.com", "reason": "dislike"})
    assert out["code"] == "outside_return_window"


def test_final_sale_blocked(settings):
    out = execute_tool(ctx(settings), "initiate_return",
                       {"order_id": "ORD-1005", "email": "sofia.martinez@example.com", "reason": "color"})
    assert out["code"] == "final_sale"


def test_multi_item_return_needs_sku_then_succeeds(settings):
    c = ctx(settings)
    base = {"order_id": "ORD-1007", "email": "priya.sharma@example.com", "reason": "too small"}
    assert execute_tool(c, "initiate_return", base)["code"] == "sku_required"
    out = execute_tool(c, "initiate_return", {**base, "sku": "SKU-MUG-11"})
    assert out["ok"] and out["rma"].startswith("RMA-")


def test_cancel_rules(settings):
    c = ctx(settings)
    assert execute_tool(c, "cancel_order", {"order_id": "ORD-1006", "email": "arjun.mehta@example.com"})["code"] == "cannot_cancel"
    assert execute_tool(c, "cancel_order", {"order_id": "ORD-1003", "email": "ananya.iyer@example.com"})["ok"]


def test_tracking_delayed_has_no_estimate(settings):
    out = execute_tool(ctx(settings), "track_shipment", {"order_id": "ORD-1008", "email": "rahul.verma@example.com"})
    assert out["ok"] and "delay" in out["latest_event"].lower() and out["estimated_delivery"] is None


def test_knowledge_base_finds_shipping_policy(settings):
    out = execute_tool(ctx(settings), "search_knowledge_base", {"query": "when is shipping free"})
    assert out["ok"] and any("$75" in r["text"] for r in out["results"])


def test_unknown_tool(settings):
    assert execute_tool(ctx(settings), "delete_database", {})["code"] == "unknown_tool"


def test_return_by_item_name(settings):
    out = execute_tool(ctx(settings), "initiate_return",
                       {"order_id": "ORD-1007", "email": "priya.sharma@example.com", "reason": "too small", "sku": "mug set"})
    assert out["ok"] and out["item"] == "Stoneware Mug Set of 4"


def test_unknown_item_name_asks_instead_of_guessing(settings):
    out = execute_tool(ctx(settings), "initiate_return",
                       {"order_id": "ORD-1007", "email": "priya.sharma@example.com", "reason": "x", "sku": "toaster"})
    assert out["ok"] is False and out["code"] == "item_not_in_order"