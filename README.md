# ShopAssist: AI Customer Support Agent for E-commerce

An AI agent that resolves order, refund, return and tracking requests on web chat and WhatsApp, with every store policy enforced in code. It passed 24 of 24 test conversations with zero policy violations.

![ShopAssist results](https://github.com/anjeesanjeet/shopassist-ai-support-agent/blob/main/outputs/thumbnail.png)

## The problem

Most e-commerce support tickets are routine: where is my order, can I return this, can I get a refund. Store owners want AI to answer them around the clock, but a support bot that can issue refunds is a liability if it can be talked into breaking policy.

The real requirements are stricter than "answer questions":

- Never reveal order details without verifying the customer.
- Never approve a refund or return the store's policy forbids, even under pressure or prompt injection.
- Hand off to a human at the right moments, with enough context that the customer never repeats themselves.
- Prove all of the above with measurements, not a demo that happens to go well.

## What I built

ShopAssist is a tool-calling AI agent for a demo home and kitchen store. The model decides what to do; seven tools do the work, and each tool enforces the store's rules itself.

- **Knowledge base search** answers policy questions using only the store's own policy documents.
- **Order lookup and shipment tracking** return status, items and tracking. The order number and checkout email must match, or nothing is revealed.
- **Cancel order** works only before the order ships.
- **Start a return** creates a return and emails a label, within 30 days of delivery and never for final-sale items. It asks which item if the order has several.
- **Issue a refund** works within 30 days, never for final-sale items, automatically only up to $200, and never twice.
- **Escalate to a human** creates a specialist ticket with a full case summary, so the customer never repeats themselves.

The key design decision: policies live in code, not in the prompt. The prompt guides behavior, but even a fooled model cannot approve a refund the policy forbids, because the tool refuses it.

Around the agent:

- **Channels:** a web chat with a live panel showing every tool call, and a WhatsApp webhook with Twilio signature validation.
- **Provider choice:** runs on Claude or OpenAI through one switch, with conversations stored in a provider-neutral format.
- **Fail-safe:** if the model API fails, the customer gets a clear message and a high-priority ticket is created automatically.
- **Observability:** every turn logs latency, tokens, cost and tool calls, shown on an operations dashboard.
- **Generated diagrams:** architecture, decision flow and per-conversation traces are built from the code and logs, not drawn by hand.

## How I proved it works

I built an evaluation suite of 24 test conversations across 9 categories, each run against a fresh copy of the store database.

- **Order status and tracking (4):** processing, in-transit and delayed orders, plus a missing email asked for over two turns.
- **Refunds (5):** an eligible refund, a $640 refund over the limit, a refund outside the window, a duplicate refund request, and a multi-turn refund.
- **Returns (2):** a final-sale item, and returning one item from a two-item order.
- **Cancellations (2):** an unshipped order and an already-shipped order.
- **Security (4):** a wrong email for someone else's order, two prompt-injection attacks, and a customer offering card details.
- **Policy questions (4):** shipping, final sale, warranty and payments.
- **Escalation and scope (3):** a request for a human, an angry repeat customer, and an off-topic request.

Each scenario is scored on what actually happened, not just what the agent said:

- **State checks:** a refund passes only if it exists in the database, and a blocked refund passes only if nothing was written.
- **Tool checks:** the agent chose the right tools.
- **Escalation checks:** it handed off to a human exactly when it should.
- **Reply checks:** the reply contains the key fact and leaks nothing from an unverified order.

## Results

ShopAssist passed all 24 test conversations with zero policy violations, at about $0.006 per conversation on Claude Haiku 4.5.

- **24 of 24** test conversations passed (100%)
- **0** policy violations
- **100%** right tool chosen, and **100%** correct human handoffs
- **21 of 24** resolved without a human (88%); the other 3 were correct escalations
- **4.8 seconds** p95 response time per turn; 1.6 to 7.6 seconds per full conversation
- **About $0.006** per conversation ($0.15 for all 24)

Both prompt-injection attacks failed. In one, the model refused on its own; in the other, it called the tools and the code still blocked the out-of-window refund. Either layer alone would have stopped the attack.

The $640 refund shows the full safety flow: the agent attempted the refund, the code blocked it as over the limit, and the agent escalated with a high-priority ticket.

## What the evaluation caught

The first runs did not score 24 of 24. Each failure exposed a real behavior problem, which I fixed in the agent rather than by loosening the tests.

- **Promised a refund it couldn't give.** Asked to cancel a shipped order, the agent offered a full refund without checking the order. Fix: it now checks the order's state first and never promises an outcome a tool hasn't confirmed.
- **Didn't answer the question.** Asked whether a broken air fryer was covered, it asked for an order number instead. Fix: policy questions are answered from the documents first, and order details are requested only when needed.
- **Expected a product code.** The customer said "the mug set", but the tool expected a SKU. Fix: tools match items by the name the customer uses, and ask only if it's ambiguous.
- **Didn't hand off.** A customer asked for a person and the agent kept talking. Fix: it now escalates in the same turn, with whatever context it has.

This is the point of evaluation: the refund promise was caught in testing, before any customer could see it.

## Architecture and stack

Every customer message follows the same path: it arrives through web chat or WhatsApp, the agent plans and calls tools, each tool checks its guardrails before reading or changing anything, and the agent replies or creates a specialist ticket. Every step is logged for the dashboard and the evaluation.

- **Models:** Claude (default Haiku 4.5) or OpenAI, selected per run
- **Backend:** Python, FastAPI
- **Data:** SQLite demo store; policy documents searched with BM25
- **Channels:** web chat, and WhatsApp through Twilio
- **Evaluation:** 24-scenario harness with database-state checks and an LLM judge
- **Visuals:** results dashboard, flowcharts generated from code, Playwright export
- **Quality:** 18 unit tests covering every guardrail, using a scripted fake model

## What I learned and what's next

The biggest lesson: a prompt alone cannot make an agent safe, and a demo alone cannot prove it works. Guardrails belong in code, and claims belong in a test suite that checks real outcomes.

What I would do for a production client:

- Connect the tools to the store's real systems, such as the Shopify or helpdesk API, in place of the demo database.
- Use hybrid search with a reranker for a large help center; BM25 suits a small policy set.
- Grow the test suite from real anonymized tickets, and run it on every change before deployment.
- Add a voice channel for phone support.

## Run it yourself

Setup, evaluation commands, WhatsApp configuration and the project structure are in the [setup guide](SETUP.md). All store data in this project is synthetic.