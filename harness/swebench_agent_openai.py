"""OpenAI-native equivalent of swebench_agent.py's Step A grounding loop, for
the cross-model (GPT-5) replication on the 4 real SWE-bench tasks.

Only the model-facing plumbing differs from swebench_agent.py: OpenAI's
function-calling API uses a different tool schema shape ({"type": "function",
"function": {...}} vs Anthropic's flat {"name", "input_schema"}) and a
different message shape for tool results (a "tool" role message keyed by
tool_call_id, vs Anthropic's tool_result content blocks). The actual
filesystem/edit logic (_run_tool, _apply_edits, _safe_resolve, _append_log)
is imported unchanged from swebench_agent.py — that logic has nothing
provider-specific in it.

Step B (real Docker-based verification) needs zero changes for this and
is not touched here — see run_swebench_pilot.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional, TypeVar

import openai

from . import llm
from .swebench_agent import (
    GroundingResult,
    _append_log,
    _apply_edits,
    _run_tool,
)

T = TypeVar("T")

GROUNDING_SYSTEM = """You are an autonomous coding agent working on a real, unfamiliar codebase.
You have been given a GitHub issue describing a bug. You have tools to explore the
checked-out repository: list_directory to see what's in a directory, and read_file to
read a file's contents. Use them to find and understand the relevant code before
proposing a fix. Read the actual file before editing it — do not guess at contents
you have not read.

If you are not certain an old_string will match exactly (whitespace, indentation, or
uniqueness), call check_edit first to verify it WITHOUT applying anything or ending
the session — it's free and does not count against any budget. Do not use submit_fix
itself to test whether something matches: submit_fix is FINAL. The moment it succeeds,
the session ends immediately, even if you only meant it as a test — so never call it
with a placeholder, marker, or exploratory edit, only your real, intended fix.

When you are ready, call submit_fix with one or more find-and-replace edits — each
edit gives a file path, the exact existing text to replace (old_string), and its
replacement (new_string). old_string must match the file's CURRENT content exactly,
including whitespace and indentation, and must be unique within the file — include
enough surrounding lines to disambiguate if the snippet you're changing appears more
than once. Only include the specific lines you're changing, not the whole file. If an
edit is rejected (old_string not found, or not unique), you'll get an error back and
can correct it.

You must call a tool on every turn. Do not respond with plain text only.
"""

TOOLS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and subdirectories at a path relative to the repo root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to repo root, e.g. 'django/db/models' or '' for root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full contents of a file, given a path relative to the repo root.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_edit",
            "description": (
                "Check whether old_string would be found, and found uniquely, in the "
                "given file's CURRENT content — without applying anything and without "
                "ending the session. Free to call, does not count against any budget. "
                "Use this to verify a match before committing to submit_fix, instead of "
                "gambling with submit_fix itself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative file path."},
                    "old_string": {"type": "string", "description": "Exact text to check for."},
                },
                "required": ["path", "old_string"],
            },
        },
    },
]

SUBMIT_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "submit_fix",
        "description": (
            "Apply your fix as one or more exact find-and-replace edits, like a real "
            "code editor. Each old_string must match the file's current content "
            "exactly and must be unique in the file. If any edit is rejected "
            "(not found, or not unique), you'll get an error back and can correct "
            "it — this does not end the session. On success, this ends the session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "description": "One or more find-and-replace edits, across one or more files.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Repo-relative file path."},
                            "old_string": {
                                "type": "string",
                                "description": "Exact existing text to replace — must be unique in the file.",
                            },
                            "new_string": {"type": "string", "description": "Replacement text."},
                        },
                        "required": ["path", "old_string", "new_string"],
                    },
                }
            },
            "required": ["edits"],
        },
    },
}


def _with_retries(fn: Callable[[], T], attempts: int = 10, base_delay: float = 3.0, max_delay: float = 45.0) -> T:
    """Same retry policy as swebench_agent._with_retries, adapted to
    OpenAI's exception hierarchy (APITimeoutError is a subclass of
    APIConnectionError, so catching the latter covers both)."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except openai.APIConnectionError as e:
            last_exc = e
            if attempt < attempts - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                print(f"    [transient error, retrying in {delay:.0f}s: {e}]", flush=True)
                time.sleep(delay)
    raise last_exc


def _stream_and_get_final_openai(client, **kwargs):
    """Streaming call, returning the assembled final completion — analogous
    to swebench_agent._stream_and_get_final. Streaming avoids a long silent
    idle period on longer turns, the same network-drop risk observed on the
    Anthropic side."""
    with client.chat.completions.stream(**kwargs) as stream:
        for _ in stream:
            pass
        return stream.get_final_completion()


def ground_and_implement(
    problem_statement: str,
    repo_dir: Path,
    max_tool_calls: int = 10,
    max_turns: int = 20,
    timeout: float = 120.0,
    log_path: Optional[Path] = None,
    model: str = "gpt-5",
) -> GroundingResult:
    """OpenAI-native Step A: same loop semantics as
    swebench_agent.ground_and_implement (explore via list_directory/
    read_file/check_edit, then submit_fix with find-and-replace edits; the
    real diff is computed mechanically via `git diff`), but speaking
    OpenAI's chat-completions tool-calling shape instead of Anthropic's.
    Returns diff=None if the model never successfully submits within
    budget (max_tool_calls or max_turns exhausted first) — a real,
    loggable outcome, not silently retried."""
    client = llm._get_openai_client()
    usage = llm.LLMUsage()
    transcript = []
    messages = [
        {"role": "system", "content": GROUNDING_SYSTEM},
        {"role": "user", "content": f"## GitHub issue\n{problem_statement}"},
    ]
    tool_calls_used = 0
    turns_used = 0
    loop_start = time.time()
    _append_log(log_path, {"event": "grounding_start", "model": model})

    while True:
        turns_used += 1
        if turns_used > max_turns:
            _append_log(log_path, {"event": "max_turns_exhausted"})
            return GroundingResult(None, usage, transcript, tool_calls_used)
        budget_left = max_tool_calls - tool_calls_used
        if budget_left > 0:
            tools = TOOLS_OPENAI + [SUBMIT_TOOL_OPENAI]
        else:
            # Exploration budget exhausted, but still allow free
            # verification via check_edit before committing — mirrors the
            # fix for the submit-as-probe bug found on the Anthropic side.
            tools = [TOOLS_OPENAI[2], SUBMIT_TOOL_OPENAI]
        turn_max_tokens = 16000 if budget_left <= 3 else 8000

        print(
            f"    [grounding turn, t={time.time() - loop_start:.1f}s, tool_calls_used={tool_calls_used}, "
            f"max_tokens={turn_max_tokens}]",
            flush=True,
        )
        _append_log(log_path, {"event": "turn_start", "tool_calls_used": tool_calls_used, "max_tokens": turn_max_tokens})

        completion = _with_retries(lambda: _stream_and_get_final_openai(
            client,
            model=model,
            max_completion_tokens=turn_max_tokens,
            messages=messages,
            tools=tools,
            tool_choice="required",
            timeout=timeout,
            stream_options={"include_usage": True},
        ))
        msg = completion.choices[0].message
        cached = 0
        details = getattr(completion.usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        print(
            f"    [grounding turn done, t={time.time() - loop_start:.1f}s, "
            f"tools_called={[tc.function.name for tc in (msg.tool_calls or [])]}, "
            f"input={completion.usage.prompt_tokens} cached={cached}]",
            flush=True,
        )
        _append_log(log_path, {
            "event": "turn_done",
            "finish_reason": completion.choices[0].finish_reason,
            "tool_calls": [
                {"name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (msg.tool_calls or [])
            ],
            "input_tokens": completion.usage.prompt_tokens,
            "output_tokens": completion.usage.completion_tokens,
            "cached_tokens": cached,
        })
        usage.add(
            llm.LLMUsage(
                input_tokens=completion.usage.prompt_tokens,
                output_tokens=completion.usage.completion_tokens,
                cache_read_input_tokens=cached,
                calls=1,
                provider="openai",
            )
        )

        assistant_msg = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in (msg.tool_calls or [])
            ] or None,
        }
        messages.append(assistant_msg)

        if not msg.tool_calls:
            # tool_choice="required" should prevent this, but handle it
            # defensively rather than crashing on an unexpected response.
            messages.append({
                "role": "user",
                "content": "You must call submit_fix with your final edits now (or check_edit first if you need to verify a match).",
            })
            continue

        submitted_diff = None
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                tool_input = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError as e:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"ERROR: could not parse arguments as JSON: {e}",
                })
                continue

            if name == "submit_fix":
                edits = tool_input.get("edits", [])
                transcript.append({"tool": "submit_fix", "input": {"n_edits": len(edits)}})
                try:
                    submitted_diff = _apply_edits(repo_dir, edits)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": "submitted"})
                    _append_log(log_path, {
                        "event": "submitted", "n_edits": len(edits), "diff_len": len(submitted_diff),
                    })
                except ValueError as e:
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(e)})
                    _append_log(log_path, {"event": "submit_rejected", "error": str(e)})
                continue

            if name != "check_edit":
                tool_calls_used += 1
            result_text = _run_tool(repo_dir, name, tool_input)
            transcript.append({"tool": name, "input": tool_input, "tool_result": result_text[:500]})
            _append_log(log_path, {
                "event": "tool_result", "tool": name, "input": tool_input, "result_preview": result_text[:300],
            })
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_text})

        if submitted_diff is not None:
            return GroundingResult(submitted_diff, usage, transcript, tool_calls_used)
