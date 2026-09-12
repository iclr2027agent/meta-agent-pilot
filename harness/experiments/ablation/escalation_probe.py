"""Supplementary probe: does ticket_07 escalate cleanly with zero retry budget?
Not part of the main 7-ticket pilot — reported separately, explicitly labeled
with retry_budget=0 (vs. retry_budget=2 used everywhere else in the pilot).

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    cd pilot
    python3 -m harness.experiments.ablation.escalation_probe
"""
from pathlib import Path
import json

import anthropic

from harness import controlplane, llm

# Work around a broken Brotli/httpx2 combination in this environment (the
# installed Brotli build's Decompressor.process() doesn't accept the
# output_buffer_limit kwarg httpx2 passes) by asking the API to not send
# brotli-encoded responses at all, scoped to this client instance only.
llm._client = anthropic.Anthropic(default_headers={"accept-encoding": "identity"})

ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPO_DIR = ROOT / "toy_repo"
RESULTS_DIR = ROOT / "results" / "escalation_probe"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

ticket = ROOT / "tasks" / "ticket_07_discounts.md"

for i in range(1, 4):
    trace = controlplane.run_task(
        ticket, REPO_DIR, retry_budget=0, test_target="tests/test_discounts.py"
    )
    trace.run_index = i
    out = RESULTS_DIR / f"trace_run{i}.json"
    out.write_text(json.dumps(trace.to_dict(), indent=2))
    print(f"run {i}: {trace.final_status}, replans={trace.replans_used}, retries={trace.retries_used}")

controlplane._reset_repo(REPO_DIR)
