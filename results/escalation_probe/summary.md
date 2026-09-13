# Escalation probe: ticket_07_discounts, retry_budget=0

Supplementary experiment, NOT part of the main 7-ticket pilot and NOT
folded into results/summary.json or results/summary_table.md. Same
ticket and test file as the main run; the only variable changed is
retry_budget (2 in every other run in this pilot, 0 here) — this
removes all room to repair, so the first verification failure escalates
immediately instead of retrying.

| Run | Status | Retries | Re-plans | Time (s) | Cost ($) | Claimed vs. verified |
|---|---|---|---|---|---|---|
| 1 | escalated | 0 | 0 | 10.93 | 0.0140 | claimed=True, verified=False |
| 2 | escalated | 0 | 0 | 16.66 | 0.0145 | claimed=True, verified=False |
| 3 | escalated | 0 | 0 | 26.39 | 0.0145 | claimed=True, verified=False |

**Escalated: 3/3**  
Median time: 16.66s (vs. ~26.3s for the retry_budget=2 runs on the same ticket)  
Median cost: $0.0145 (vs. ~$0.0325 for the retry_budget=2 runs on the same ticket)  
human_interventions: 1 in every run (the only escalation observed in this pilot).