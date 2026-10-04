# ShopAssist evaluation report

Model: `anthropic:claude-haiku-4-5-20251001`  |  Scenarios: 24  |  Run: 2026-10-04T18:58:33+00:00

| Metric | Result |
|---|---|
| Task success rate | 100% (24/24) |
| Tool selection accuracy | 100% |
| Policy violations | 0 (across 15 policy checks) |
| Escalation accuracy | 100% |
| Resolved without a human | 88% |
| Latency per turn, avg / p95 | 2.9s / 4.5s |
| Cost per conversation | $0.0068 |
| Reply quality (LLM judge, 1-5) | 4.43 |

## By category

| Category | Passed | Rate |
|---|---|---|
| Order status | 1/1 | 100% |
| Tracking | 3/3 | 100% |
| Refunds | 5/5 | 100% |
| Returns | 2/2 | 100% |
| Cancellations | 2/2 | 100% |
| Security | 4/4 | 100% |
| Policy questions | 4/4 | 100% |
| Escalation | 2/2 | 100% |
| Scope | 1/1 | 100% |

## Failures

None.
