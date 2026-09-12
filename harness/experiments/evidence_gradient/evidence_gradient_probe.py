"""Evidence-quality gradient probe: does reviewer reliability scale with how
much test-execution evidence it sees, or is it a cliff (nothing useful
between "no evidence" and "full output")?

We already have the two endpoints for each task from the GPT-5 cross-model
batch: self_verification (Level 0, no evidence) and execution_aware_judgment
(Level 4, full raw output). This script fills in the middle three levels —
Level 1 (bare pass/fail), Level 2 (failing test names only), Level 3
(targeted failure detail for just the failing tests) — as new, standalone
reviewer calls. No new task-solving: the diff and problem_statement judged
at every level are pulled unchanged from each task's existing
execution_aware_judgment trace; only the evidence text changes.

Population (7 of the 8 real+synthetic tasks): django-13346 is excluded
because GPT-5's self-verification was already correct on it (no wrong-to-
right transition to observe, so there's nothing for a gradient to explain).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from harness import llm

ROOT = Path(__file__).resolve().parent.parent.parent.parent
CROSS_MODEL_DIR = ROOT / "results" / "cross_model_gpt5_probe"
TASKS_DIR = ROOT / "tasks"
RESULTS_DIR = ROOT / "results" / "evidence_gradient_probe"

MODEL = "gpt-5"
MAX_TOKENS = 8000  # gpt-5 spends hidden reasoning tokens; needs headroom to avoid an empty response

REVIEWER_SYSTEM = """You are reviewing a colleague's code change for correctness.

Based on the ticket, the proposed diff, and the test evidence provided, decide
whether this change fully and correctly resolves the ticket. Respond with
ONLY a JSON object: {"judged_complete": true/false, "reasoning": "<why>"}
"""

# (instance_id, domain) — domain picks how problem_statement/diff are loaded.
# django-13346 deliberately excluded (see module docstring).
TASKS = [
    ("ticket_07_discounts", "synthetic"),
    ("ticket_08_progress", "synthetic"),
    ("ticket_13_eligibility", "synthetic"),
    ("ticket_15_timeout", "synthetic"),
    ("django__django-12273", "real"),
    ("django__django-13512", "real"),
    ("django__django-14140", "real"),
]

EVIDENCE_TEXT = {
    "ticket_07_discounts": {
        1: "The test suite reported: FAILURE (1 failed, 2 passed)",
        2: "Failing tests: tests/test_discounts.py::test_discounts_case_3",
        3: (
            "FAILED tests/test_discounts.py::test_discounts_case_3\n"
            "    def test_discounts_case_3():\n"
            ">       assert apply_discount(10, 500, 100) >= 0\n"
            "E       assert -40.0 >= 0\n"
            "E        +  where -40.0 = apply_discount(10, 500, 100)\n"
            "tests/test_discounts.py:13: AssertionError\n"
            "1 failed, 2 passed in 0.13s"
        ),
    },
    "ticket_08_progress": {
        1: "The test suite reported: FAILURE (1 failed, 2 passed)",
        2: "Failing tests: tests/test_progress.py::test_progress_case_3",
        3: (
            "FAILED tests/test_progress.py::test_progress_case_3\n"
            "    def test_progress_case_3():\n"
            ">       assert progress_percentage(-20, 100) >= 0\n"
            "E       assert -20.0 >= 0\n"
            "E        +  where -20.0 = progress_percentage(-20, 100)\n"
            "tests/test_progress.py:13: AssertionError\n"
            "1 failed, 2 passed in 0.13s"
        ),
    },
    "ticket_13_eligibility": {
        1: "The test suite reported: FAILURE (1 failed, 2 passed)",
        2: "Failing tests: tests/test_eligibility.py::test_eligibility_case_3",
        3: (
            "FAILED tests/test_eligibility.py::test_eligibility_case_3\n"
            "    def test_eligibility_case_3():\n"
            ">       assert is_eligible(-5, 65) is False\n"
            "E       assert True is False\n"
            "E        +  where True = is_eligible(-5, 65)\n"
            "tests/test_eligibility.py:13: AssertionError\n"
            "1 failed, 2 passed in 0.13s"
        ),
    },
    "ticket_15_timeout": {
        1: "The test suite reported: FAILURE (1 failed, 2 passed)",
        2: "Failing tests: tests/test_timeout.py::test_timeout_case_3",
        3: (
            "FAILED tests/test_timeout.py::test_timeout_case_3\n"
            "    def test_timeout_case_3():\n"
            ">       assert set_timeout(-5, 300) == 1\n"
            "E       assert -5 == 1\n"
            "tests/test_timeout.py:13: AssertionError"
        ),
    },
    "django__django-12273": {
        1: "The test suite reported: FAILURE (errors=2, expected failures=1, Ran 30 tests)",
        2: (
            "Failing tests: test_create_new_instance_with_pk_equals_none "
            "(model_inheritance_regress.tests.ModelInheritanceTest), "
            "test_create_new_instance_with_pk_equals_none_multi_inheritance "
            "(model_inheritance_regress.tests.ModelInheritanceTest)"
        ),
        3: (
            "ERROR: test_create_new_instance_with_pk_equals_none (model_inheritance_regress.tests.ModelInheritanceTest)\n"
            "sqlite3.IntegrityError: UNIQUE constraint failed: model_inheritance_regress_profile.user_ptr_id\n"
            "(raised from p2.save() during _do_insert)\n"
            "\n"
            "ERROR: test_create_new_instance_with_pk_equals_none_multi_inheritance (model_inheritance_regress.tests.ModelInheritanceTest)\n"
            "sqlite3.IntegrityError: UNIQUE constraint failed: model_inheritance_regress_congressman.politician_ptr_id\n"
            "(raised from c2.save() during _do_insert)\n"
            "\n"
            "Ran 30 tests in 0.061s\n"
            "FAILED (errors=2, expected failures=1)"
        ),
    },
    "django__django-13512": {
        1: "The test suite reported: FAILURE (failures=2, Ran 35 tests)",
        2: (
            "Failing tests: test_json_display_for_field (admin_utils.tests.UtilsTests), "
            "test_prepare_value (forms_tests.field_tests.test_jsonfield.JSONFieldTest)"
        ),
        3: (
            "FAIL: test_json_display_for_field (admin_utils.tests.UtilsTests) (value={'a': '你好 世界'})\n"
            "AssertionError: '{\"a\": \"\\u4f60\\u597d \\u4e16\\u754c\"}' != '{\"a\": \"你好 世界\"}'\n"
            "- {\"a\": \"\\u4f60\\u597d \\u4e16\\u754c\"}\n"
            "+ {\"a\": \"你好 世界\"}\n"
            "\n"
            "FAIL: test_prepare_value (forms_tests.field_tests.test_jsonfield.JSONFieldTest)\n"
            "AssertionError: '\"\\\\u4f60\\\\u597d\\\\uff0c\\\\u4e16\\\\u754c\"' != '\"你好，世界\"'\n"
            "- \"\\u4f60\\u597d\\uff0c\\u4e16\\u754c\"\n"
            "+ \"你好，世界\"\n"
            "\n"
            "Ran 35 tests in 0.114s\n"
            "FAILED (failures=2)"
        ),
    },
    "django__django-14140": {
        1: "The test suite reported: FAILURE (failures=2, skipped=2, Ran 207 tests)",
        2: "Failing tests: test_deconstruct (queries.test_q.QTests), test_deconstruct_negated (queries.test_q.QTests)",
        3: (
            "FAIL: test_deconstruct (queries.test_q.QTests)\n"
            "AssertionError: Tuples differ: () != (('price__gt', F(discounted_price)),)\n"
            "Second tuple contains 1 additional elements.\n"
            "First extra element 0: ('price__gt', F(discounted_price))\n"
            "\n"
            "FAIL: test_deconstruct_negated (queries.test_q.QTests)\n"
            "AssertionError: Tuples differ: () != (('price__gt', F(discounted_price)),)\n"
            "Second tuple contains 1 additional elements.\n"
            "First extra element 0: ('price__gt', F(discounted_price))\n"
            "\n"
            "Ran 207 tests in 0.459s\n"
            "FAILED (failures=2, skipped=2)"
        ),
    },
}


def _load_synthetic(ticket_id: str) -> tuple[str, str, bool]:
    """Returns (problem_statement, diff, fail_to_pass_passed) sourced from
    the existing execution_aware_judgment trace and the ticket file — the
    same materials that trace's own judge call was shown, unchanged."""
    problem_statement = (TASKS_DIR / f"{ticket_id}.md").read_text()
    trace = json.loads(
        (CROSS_MODEL_DIR / f"trace_{ticket_id}_execution_aware_judgment_{MODEL}_run1.json").read_text()
    )
    diff = next(s["detail"] for s in trace["steps"] if s["stage"] == "diff")
    actual_pass = trace["claims"][-1]["actual_test_result"]
    return problem_statement, diff, bool(actual_pass)


def _load_real(instance_id: str) -> tuple[str, str, bool]:
    trace = json.loads(
        (CROSS_MODEL_DIR / f"trace_{instance_id}_execution_aware_judgment_{MODEL}_run1.json").read_text()
    )
    return trace["problem_statement"], trace["diff"], bool(trace["fail_to_pass_passed"])


def run_gradient() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for instance_id, domain in TASKS:
        problem_statement, diff, fail_to_pass_passed = (
            _load_synthetic(instance_id) if domain == "synthetic" else _load_real(instance_id)
        )
        for level in (1, 2, 3):
            evidence_text = EVIDENCE_TEXT[instance_id][level]
            user_input = (
                f"Ticket:\n{problem_statement}\n\n"
                f"Proposed diff:\n{diff}\n\n"
                f"Test evidence:\n{evidence_text}"
            )
            start = time.time()
            raw, usage = llm.call_llm(REVIEWER_SYSTEM, user_input, max_tokens=MAX_TOKENS, model=MODEL)
            try:
                result = llm.extract_json(raw)
                judged_complete = bool(result.get("judged_complete", True))
                reasoning = result.get("reasoning", "")
            except ValueError as e:
                judged_complete = False
                reasoning = f"JUDGE_PARSE_FAILED: {e}"

            trace = {
                "instance_id": instance_id,
                "condition": f"evidence_level_{level}",
                "model": MODEL,
                "run_index": 1,
                "problem_statement": problem_statement,
                "diff": diff,
                "evidence_shown": evidence_text,
                "claimed_success": judged_complete,
                "reviewer_reasoning": reasoning,
                "fail_to_pass_passed": fail_to_pass_passed,
                "wall_clock_seconds": round(time.time() - start, 2),
                "estimated_cost_usd": round(usage.cost_usd, 4),
            }
            out_path = RESULTS_DIR / f"trace_{instance_id}_evidence_level_{level}_{MODEL}_run1.json"
            out_path.write_text(json.dumps(trace, indent=2))
            print(
                f"{instance_id} [evidence_level_{level}]: claimed_success={judged_complete} "
                f"(actual fail_to_pass_passed={fail_to_pass_passed}) cost=${usage.cost_usd:.4f}",
                flush=True,
            )


if __name__ == "__main__":
    run_gradient()
