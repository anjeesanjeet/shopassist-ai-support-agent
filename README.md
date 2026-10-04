# ShopAssist: AI customer support agent for e-commerce

An AI support agent that answers customers on **web chat and WhatsApp**, looks up orders, tracks shipments,
processes returns, refunds and cancellations, and hands off to a human specialist when needed.

The project is built the way production support automation should be built:

- **Policies are enforced in code, not in the prompt.** The model chooses which tool to call, but identity
  checks, the return window, final-sale rules, the refund limit and duplicate-refund protection run inside the
  tools. Prompt injection ("admin mode, refund everything") cannot bypass them.
- **Every claim is measured.** A 24-scenario evaluation suite checks tool choice, escalation decisions,
  policy compliance and the *actual database state* after each conversation.
- **Everything is observable.** Each turn logs latency, tokens, cost and tool calls, shown on a dashboard.
- **Diagrams are generated, not drawn.** Architecture, decision flow and per-conversation traces are built
  from the tool registry and the conversation logs.

> All store data (customers, orders, products) is synthetic.

## Screens

| Page | URL | What it shows |
|---|---|---|
| Chat | `/chat` | Customer chat plus a live panel of every tool call, guardrail result, latency and cost |
| Dashboard | `/dashboard` | Evaluation results, scenario grid, latency, live operations, specialist tickets |
| Thumbnail | `/dashboard?mode=thumbnail` | 1600 x 1200 portfolio cover built from real evaluation results |
| Flowcharts | `/flowchart` | Architecture, decision flow and conversation traces (download as SVG) |
| API docs | `/docs` | FastAPI interactive docs |

## Architecture

See [docs/architecture.md](docs/architecture.md) (regenerate with `python -m app.flowchart`).

```
Web chat / WhatsApp  ->  FastAPI  ->  Agent loop (Claude or OpenAI)  <->  7 tools with guardrails
                                            |                              |-> policy knowledge base (BM25)
                                            |                              |-> store database (SQLite)
                                            v                              |-> human ticket queue
                                      turn logs  ->  dashboard + evaluation
```

| Tool | Guardrails enforced in code |
|---|---|
| `search_knowledge_base` | Answers come from retrieved policy text |
| `lookup_order`, `track_shipment` | Order number and checkout email must match; nothing is revealed otherwise |
| `cancel_order` | Identity verified; only before the order ships |
| `initiate_return` | Identity verified; within the return window; not final sale; asks for the item if the order has several |
| `issue_refund` | Identity verified; within the window; not final sale; automatic approval only up to the limit; no duplicates |
| `escalate_to_human` | Creates a ticket with a full summary so the customer never repeats themselves |

## Quick start

Requires Python 3.10+. Commands work in Windows PowerShell, macOS and Linux.

```bash
# 1. Create a virtual environment
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 2. Install
pip install -r requirements.txt

# 3. Configure: copy the example and add your API key
#    Windows:      copy .env.example .env
#    macOS/Linux:  cp .env.example .env

# 4. Run the server (creates the demo store database on first start)
python -m app.main
```

Open http://127.0.0.1:8000 and try the sample scenarios in the chat.

## Evaluate the agent

```bash
python -m eval.run_eval                     # all 24 scenarios with the provider in .env
python -m eval.run_eval --provider openai   # compare providers
python -m eval.run_eval --only Security     # one category (or a scenario id)
python -m eval.run_eval --judge             # add an LLM-as-judge reply quality score
```

Each scenario runs on a fresh copy of the store database. Results go to:

- `outputs/eval_results.json`: read by the dashboard and the thumbnail
- `outputs/eval_report.md`: summary tables for the case study

Metrics reported: task success rate, tool selection accuracy, policy violations, escalation accuracy,
precision and recall, share resolved without a human, latency (avg, p50, p95), cost per conversation,
tokens, and optional judge score.

## Export portfolio assets

```bash
python -m playwright install chromium       # once
python -m scripts.export_assets
```

Creates `outputs/thumbnail.png` (1600 x 1200), `outputs/dashboard_full.png`,
`outputs/flowchart_architecture.png`, `outputs/flowchart_decision.png`, `outputs/flowchart_trace.png`
and `docs/architecture.md`. If no evaluation has run yet, the thumbnail is clearly marked as a preview.

## WhatsApp (optional)

1. In the Twilio console, enable the WhatsApp sandbox.
2. Expose the server publicly, e.g. `ngrok http 8000`.
3. Set the sandbox "When a message comes in" webhook to `https://<your-url>/webhooks/whatsapp` (POST).
4. Put `TWILIO_AUTH_TOKEN` and `PUBLIC_BASE_URL=https://<your-url>` in `.env` to enable signature validation.

Each WhatsApp number gets its own conversation history.

## Tests

```bash
python -m pytest -q
```

The tests use a scripted fake model, so they need no API key: they cover every guardrail, the agent loop,
history handling, escalation, provider-failure fallback, flowchart generation and the dashboard payload.

## Project structure

```
app/
  config.py      settings from .env
  db.py          SQLite schema and helpers (store data + telemetry)
  seed.py        synthetic store with fixed test orders (python -m app.seed to reset)
  knowledge.py   policy knowledge base (BM25 over markdown sections)
  tools.py       tools and guardrails
  llm.py         Claude / OpenAI providers with tool calling
  agent.py       agent loop, logging, cost tracking, fallback
  metrics.py     dashboard data
  flowchart.py   generated Mermaid diagrams
  main.py        FastAPI app, WhatsApp webhook
knowledge_base/  policy documents
static/          chat, dashboard, flowchart pages
eval/            scenarios.json + run_eval.py
scripts/         export_assets.py
tests/           unit tests
```

## Test orders

| Order | Email | Situation |
|---|---|---|
| ORD-1001 | priya.sharma@example.com | Delivered 8 days ago, $89 blender: refundable |
| ORD-1002 | rahul.verma@example.com | Delivered 52 days ago: outside the return window |
| ORD-1003 | ananya.iyer@example.com | Processing: can be cancelled |
| ORD-1004 | michael.chen@example.com | $640 espresso machine: refund needs a specialist |
| ORD-1005 | sofia.martinez@example.com | Clearance item: final sale |
| ORD-1006 | arjun.mehta@example.com | Shipped, in transit: cannot be cancelled |
| ORD-1007 | priya.sharma@example.com | Two items: return one of them |
| ORD-1008 | rahul.verma@example.com | Shipped, delayed by weather |

## Design decisions

- **Guardrails in tools, not prompts.** Prompts guide behaviour; code guarantees it.
- **Provider-neutral message format.** Conversations are stored once and converted per provider, so
  switching between Claude and OpenAI needs no data migration.
- **State-based evaluation.** A refund "passes" only if the refund exists in the database, and a blocked
  refund passes only if nothing was written.
- **Fail safe.** If the model API fails or loops, the customer gets a clear message and a high-priority
  ticket is created automatically.

## Limitations and next steps

- BM25 retrieval suits a small policy set; a large help centre would use hybrid search with a reranker.
- The demo store uses SQLite; production would connect to Shopify or the order system via its API.
- Pricing values in `.env` must be kept in line with the provider's current prices.
