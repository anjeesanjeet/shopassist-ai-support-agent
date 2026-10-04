from app.agent import FALLBACK_REPLY, SupportAgent
from app.db import connect
from app.flowchart import architecture_mermaid, decision_mermaid, trace_mermaid
from app.metrics import dashboard_payload
from tests.conftest import ScriptedProvider, text, tool_call


def test_tool_loop_refund_and_logging(settings):
    provider = ScriptedProvider([
        tool_call("issue_refund", order_id="ORD-1001", email="priya.sharma@example.com", reason="broken"),
        text("Done! I've refunded $89.00 to your original payment method."),
    ])
    agent = SupportAgent(provider, settings)
    res = agent.handle_message("conv-1", "Refund ORD-1001 please, priya.sharma@example.com")
    assert res.reply.startswith("Done") and res.outcome == "handled_by_ai"
    assert res.tool_calls[0]["name"] == "issue_refund" and res.tool_calls[0]["ok"]
    assert res.llm_calls == 2 and res.cost_usd > 0
    conn = connect(settings.db_path)
    assert conn.execute("SELECT COUNT(*) n FROM refunds WHERE order_id='ORD-1001'").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM turn_logs").fetchone()["n"] == 1


def test_history_is_passed_on_next_turn(settings):
    provider = ScriptedProvider([text("Sure, what's your email?"), text("Thanks!")])
    agent = SupportAgent(provider, settings)
    agent.handle_message("conv-2", "Track ORD-1006")
    agent.handle_message("conv-2", "arjun.mehta@example.com")
    assert len(provider.calls[1]) == 3  # user, assistant, user


def test_escalation_sets_outcome(settings):
    provider = ScriptedProvider([
        tool_call("escalate_to_human", reason="wants a person", priority="normal", summary="Customer asked for a human"),
        text("I've passed you to a specialist."),
    ])
    res = SupportAgent(provider, settings).handle_message("conv-3", "Human please")
    assert res.outcome == "escalated"


def test_provider_failure_falls_back_and_escalates(settings):
    class Broken(ScriptedProvider):
        def complete(self, *a, **k):
            raise TimeoutError("upstream timeout")
    res = SupportAgent(Broken([]), settings).handle_message("conv-4", "hello")
    assert res.reply == FALLBACK_REPLY and res.outcome == "escalated" and "TimeoutError" in res.error


def test_flowcharts_and_dashboard(settings):
    provider = ScriptedProvider([
        tool_call("lookup_order", order_id="ORD-1004", email="wrong@example.com"),
        text("I couldn't verify that order."),
    ])
    SupportAgent(provider, settings).handle_message("conv-5", "What's in ORD-1004?")
    arch = architecture_mermaid(settings)
    assert arch.startswith("flowchart LR") and "T_issue_refund" in arch
    assert "A_issue_refund" in decision_mermaid(settings)
    trace = trace_mermaid(settings, "conv-5")
    assert "lookup_order" in trace and "blocked" in trace
    payload = dashboard_payload(settings)
    assert payload["live"]["conversations"] == 1 and payload["eval"] is None
