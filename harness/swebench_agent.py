"""Step A (grounding) and the two judge conditions for the SWE-bench real-task
pilot. Step B (real Docker-based verification) lives in run_swebench_pilot.py,
which calls the actual swebench harness — nothing here touches Docker.

Every LLM call here is a fresh, stateless call (see harness/llm.py) — there is
no conversation history shared between the grounding loop and either judge
call, or between the two judge calls themselves.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional, TypeVar

import anthropic
import openai

from . import llm

T = TypeVar("T")


def _with_retries(fn: Callable[[], T], attempts: int = 10, base_delay: float = 3.0, max_delay: float = 45.0) -> T:
    """Retry on transient network errors. Resets llm._client before each
    retry so a fresh connection pool is built on the next _get_client()
    call — a pooled httpx connection can be left in a bad state after a
    read-timeout, and reusing it can otherwise cause the same timeout to
    repeat indefinitely even once the underlying network issue has
    cleared. Does not retry on non-transient API errors (e.g. bad request,
    auth, credit) — those propagate immediately since retrying won't help.
    Catches both Anthropic's and OpenAI's transient-error types since the
    judge functions below can be called with either provider's model.

    attempts=10 (raised from 5) because prompt caching now makes a retried
    call cheap even deep into a long conversation — reliability, not cost,
    is the binding constraint on how many times it's worth trying. Delay is
    capped at max_delay so a high attempt count doesn't translate into an
    impractically long wait (uncapped 2^attempt would hit 25+ minutes by
    attempt 9)."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except (anthropic.APITimeoutError, anthropic.APIConnectionError, openai.APIConnectionError) as e:
            last_exc = e
            if attempt < attempts - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                print(f"    [transient error, resetting client, retrying in {delay:.0f}s: {e}]", flush=True)
                llm.reset_client()
                time.sleep(delay)
    raise last_exc


def _stream_and_get_final(**kwargs) -> anthropic.types.Message:
    """Make a streaming call and return the assembled final message — same
    shape as a plain messages.create() response, so callers don't need to
    change. Streaming (vs. a plain blocking create()) starts receiving
    bytes within the first few seconds even when total generation takes a
    while, so there's no long silent idle period for something in the
    network path (Docker Desktop's VM network proxy, a NAT table, etc.) to
    drop as dead — the leading hypothesis for the read-timeouts observed
    on longer (later-turn, large-context) calls."""
    with llm._get_client().messages.stream(**kwargs) as stream:
        for _ in stream:
            pass
        return stream.get_final_message()


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
"""

SELF_VERIFICATION_SYSTEM = """You are reviewing your own proposed fix before it is finalized.
You will be given the original GitHub issue and the diff you just wrote. Reread the
issue carefully and decide whether your diff fully resolves it. You do not have
access to run the code or tests. Respond with ONLY a JSON object:
{"judged_complete": true/false, "reasoning": "<why>"}
"""

EXECUTION_AWARE_JUDGE_SYSTEM = """You are deciding whether your fix is complete.
You will be given the GitHub issue, your diff, and the actual output of running the
real test suite against your change. Read the test output carefully and decide
whether it indicates success. Respond with ONLY a JSON object:
{"judged_complete": true/false, "reasoning": "<why>"}
"""

CHECK_EDIT_TOOL = {
    "name": "check_edit",
    "description": (
        "Check whether old_string would be found, and found uniquely, in the "
        "given file's CURRENT content — without applying anything and without "
        "ending the session. Free to call, does not count against any budget. "
        "Use this to verify a match before committing to submit_fix, instead of "
        "gambling with submit_fix itself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Repo-relative file path."},
            "old_string": {"type": "string", "description": "Exact text to check for."},
        },
        "required": ["path", "old_string"],
    },
}

TOOLS = [
    {
        "name": "list_directory",
        "description": "List files and subdirectories at a path relative to the repo root.",
        "input_schema": {
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
    {
        "name": "read_file",
        "description": "Read the full contents of a file, given a path relative to the repo root.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    CHECK_EDIT_TOOL,
]

SUBMIT_TOOL = {
    "name": "submit_fix",
    "description": (
        "Apply your fix as one or more exact find-and-replace edits, like a real "
        "code editor. Each old_string must match the file's current content "
        "exactly and must be unique in the file. If any edit is rejected "
        "(not found, or not unique), you'll get an error back and can correct "
        "it — this does not end the session. On success, this ends the session."
    ),
    "input_schema": {
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
}


class GroundingResult:
    def __init__(self, diff: Optional[str], usage: "llm.LLMUsage", transcript: list, tool_calls_used: int):
        self.diff = diff
        self.usage = usage
        self.transcript = transcript
        self.tool_calls_used = tool_calls_used


def _safe_resolve(repo_dir: Path, rel_path: str) -> Path:
    """Resolve rel_path against repo_dir, refusing to escape it."""
    candidate = (repo_dir / rel_path).resolve()
    repo_resolved = repo_dir.resolve()
    if repo_resolved != candidate and repo_resolved not in candidate.parents:
        raise ValueError(f"path escapes repo root: {rel_path!r}")
    return candidate


def _run_tool(repo_dir: Path, name: str, tool_input: dict) -> str:
    try:
        if name == "list_directory":
            p = _safe_resolve(repo_dir, tool_input.get("path", ""))
            if not p.exists():
                return f"ERROR: path does not exist: {tool_input.get('path')!r}"
            if not p.is_dir():
                return f"ERROR: not a directory: {tool_input.get('path')!r}"
            entries = sorted(os.listdir(p))
            return "\n".join(entries) if entries else "(empty directory)"
        elif name == "read_file":
            p = _safe_resolve(repo_dir, tool_input.get("path", ""))
            if not p.exists():
                return f"ERROR: file does not exist: {tool_input.get('path')!r}"
            if not p.is_file():
                return f"ERROR: not a file: {tool_input.get('path')!r}"
            text = p.read_text(errors="replace")
            # Defensive cap so one huge file can't blow the context budget.
            if len(text) > 60_000:
                text = text[:60_000] + "\n... [truncated]"
            return text
        elif name == "check_edit":
            p = _safe_resolve(repo_dir, tool_input.get("path", ""))
            if not p.exists():
                return f"ERROR: file does not exist: {tool_input.get('path')!r}"
            content = p.read_text(errors="replace")
            old = tool_input.get("old_string", "")
            count = content.count(old)
            if count == 0:
                return "NOT FOUND: old_string does not appear in the file's current content."
            elif count == 1:
                return "OK: old_string found exactly once. Safe to use in submit_fix."
            else:
                return f"NOT UNIQUE: old_string appears {count} times. Include more surrounding context to disambiguate."
        else:
            return f"ERROR: unknown tool {name!r}"
    except Exception as e:  # noqa: BLE001 - tool errors are reported back to the model, not raised
        return f"ERROR: {e}"


def _apply_edits(repo_dir: Path, edits: list[dict]) -> str:
    """Apply find-and-replace edits to files in repo_dir, then compute the
    real diff via `git diff` — this is what actually gets verified, so the
    diff is always mechanically well-formed (no hand-written diff syntax
    to get wrong), and unlike full-file-content replacement, cost/output
    size scales with the size of the CHANGE, not the size of the file
    (a large file needing a one-line fix no longer risks truncating a
    from-scratch rewrite against the output token cap).

    Raises ValueError with a clear, model-facing message if any edit's
    old_string is missing or non-unique in its file — the caller is
    expected to surface this back to the model as a tool_result error and
    let it retry, not treat it as fatal. Resets the working tree to clean
    afterward, on both success and failure, so a partially-applied bad
    edit never leaks into the next attempt."""
    if not edits:
        raise ValueError(
            "submit_fix was called with no edits. Provide at least one "
            "{path, old_string, new_string} edit describing your actual fix."
        )

    file_contents: dict[str, str] = {}
    try:
        for i, edit in enumerate(edits):
            path = edit["path"]
            old = edit["old_string"]
            new = edit["new_string"]
            if old == new:
                raise ValueError(
                    f"edit {i} ({path!r}): old_string and new_string are identical — "
                    f"this edit would be a no-op. Provide an edit that actually changes "
                    f"the code."
                )
            if path not in file_contents:
                p = _safe_resolve(repo_dir, path)
                if not p.exists():
                    raise ValueError(f"edit {i} ({path!r}): file does not exist")
                file_contents[path] = p.read_text()
            content = file_contents[path]
            count = content.count(old)
            if count == 0:
                raise ValueError(
                    f"edit {i} ({path!r}): old_string not found in the file's current "
                    f"content. Re-read the file to confirm its exact current text."
                )
            if count > 1:
                raise ValueError(
                    f"edit {i} ({path!r}): old_string appears {count} times, must be "
                    f"unique. Include more surrounding context to disambiguate."
                )
            file_contents[path] = content.replace(old, new, 1)

        for path, content in file_contents.items():
            _safe_resolve(repo_dir, path).write_text(content)

        diff_proc = subprocess.run(
            ["git", "diff", "--no-color"], cwd=repo_dir, capture_output=True, text=True
        )
        return diff_proc.stdout
    finally:
        # Leave the working tree clean for whatever runs next, whether
        # this succeeded, partially applied, or failed outright.
        subprocess.run(["git", "checkout", "--force", "--quiet", "."], cwd=repo_dir, capture_output=True)
        subprocess.run(["git", "clean", "-fdq"], cwd=repo_dir, capture_output=True)


def _append_log(log_path: Optional[Path], event: dict) -> None:
    """Append one JSON-line event to a durable on-disk log immediately, so
    a killed/crashed/timed-out run still leaves a real record of exactly
    what happened up to that point, not just whatever made it to stdout."""
    if log_path is None:
        return
    event = {"t": round(time.time(), 2), **event}
    with open(log_path, "a") as f:
        f.write(json.dumps(event) + "\n")


def _to_content_blocks(resp_content) -> list[dict]:
    """Convert SDK response content blocks to plain dicts so `messages`
    holds a uniform representation we can both re-send and attach a cache
    breakpoint to."""
    blocks = []
    for b in resp_content:
        if b.type == "text":
            blocks.append({"type": "text", "text": b.text})
        elif b.type == "tool_use":
            blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
    return blocks


def _with_cache_marker(messages: list[dict]) -> list[dict]:
    """Return a COPY of messages with a prompt-cache breakpoint on the last
    content block of the last message — everything up to that point (the
    growing history from prior turns) can then be served from cache on the
    next call instead of reprocessed at full price. `messages` itself is
    never mutated, so no cache_control marker is ever persisted into the
    stored history (avoids accumulating stale breakpoints past the API's
    4-breakpoint limit)."""
    if not messages:
        return messages
    copied = list(messages)
    last = dict(copied[-1])
    content = last["content"]
    content = [dict(b) for b in content]  # shallow copy each block
    if content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}
    last["content"] = content
    copied[-1] = last
    return copied


def ground_and_implement(
    problem_statement: str,
    repo_dir: Path,
    max_tool_calls: int = 10,
    max_turns: int = 20,
    timeout: float = 120.0,
    log_path: Optional[Path] = None,
) -> GroundingResult:
    """Step A: multi-turn tool loop. The model explores repo_dir (a plain
    checkout, no dependencies installed) via list_directory/read_file, then
    calls submit_fix with one or more find-and-replace edits; the real
    diff is computed mechanically via `git diff`, not hand-written by the
    model. A rejected edit (old_string not found/not unique) comes back as
    a tool error the model can correct, not a fatal failure. Returns
    diff=None if the model never successfully submits within budget (a
    real, loggable outcome, not silently retried) — either because
    max_tool_calls of exploration ran out with no submission, or max_turns
    (a hard safety cap on total turns, exploration + failed submit
    attempts combined) was hit first.

    If log_path is given, every turn is appended to it immediately (JSONL)
    so a killed or crashed run still leaves a real record on disk."""
    usage = llm.LLMUsage()
    transcript = []
    messages = [{
        "role": "user",
        "content": [{"type": "text", "text": f"## GitHub issue\n{problem_statement}"}],
    }]
    tool_calls_used = 0
    turns_used = 0
    loop_start = time.time()
    _append_log(log_path, {"event": "grounding_start"})
    system_blocks = [{"type": "text", "text": GROUNDING_SYSTEM, "cache_control": {"type": "ephemeral"}}]

    while True:
        turns_used += 1
        if turns_used > max_turns:
            _append_log(log_path, {"event": "max_turns_exhausted"})
            return GroundingResult(None, usage, transcript, tool_calls_used)
        budget_left = max_tool_calls - tool_calls_used
        if budget_left > 0:
            tools = TOOLS + [SUBMIT_TOOL]
        else:
            # Exploration budget exhausted, but still allow free
            # verification via check_edit before committing — this is
            # exactly the phase where a forced, unconditional submit_fix
            # previously let a mid-verification probe accidentally become
            # the final (terminal) answer.
            tools = [CHECK_EDIT_TOOL, SUBMIT_TOOL]
        tool_choice = {"type": "auto"}
        # Observed real usage: intermediate (exploration) turns only ever
        # produced 60-1400 output tokens; only a submission needs to write
        # full file contents. Once budget is nearly exhausted a submission
        # is plausible/imminent, so give it full headroom rather than risk
        # truncating a real file. This bounds worst-case per-call cost for
        # the many early turns without risking truncation of a real fix.
        turn_max_tokens = 8000 if budget_left <= 3 else 4096

        print(
            f"    [grounding turn, t={time.time() - loop_start:.1f}s, tool_calls_used={tool_calls_used}, "
            f"max_tokens={turn_max_tokens}]",
            flush=True,
        )
        _append_log(log_path, {"event": "turn_start", "tool_calls_used": tool_calls_used, "max_tokens": turn_max_tokens})
        call_messages = _with_cache_marker(messages)
        resp = _with_retries(lambda: _stream_and_get_final(
            model=llm.MODEL,
            max_tokens=turn_max_tokens,
            system=system_blocks,
            messages=call_messages,
            tools=tools,
            tool_choice=tool_choice,
            timeout=timeout,
        ))
        cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        print(
            f"    [grounding turn done, t={time.time() - loop_start:.1f}s, "
            f"tools_called={[b.type for b in resp.content if b.type == 'tool_use']}, "
            f"input={resp.usage.input_tokens} cache_read={cache_read} cache_write={cache_write}]",
            flush=True,
        )
        _append_log(log_path, {
            "event": "turn_done",
            "stop_reason": resp.stop_reason,
            "tool_calls": [
                {"name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"
            ],
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        })
        usage.add(
            llm.LLMUsage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_creation_input_tokens=cache_write,
                cache_read_input_tokens=cache_read,
                calls=1,
            )
        )
        messages.append({"role": "assistant", "content": _to_content_blocks(resp.content)})

        tool_use_blocks = [b for b in resp.content if b.type == "tool_use"]
        if not tool_use_blocks:
            # Model responded with text only and no tool call — force a
            # final submit on the next turn rather than looping forever.
            messages.append(
                {
                    "role": "user",
                    "content": [{
                        "type": "text",
                        "text": "You must call submit_fix with your final edits now (or check_edit first if you need to verify a match).",
                    }],
                }
            )
            continue

        submitted_diff = None
        tool_results = []
        for block in tool_use_blocks:
            if block.name == "submit_fix":
                edits = block.input.get("edits", [])
                transcript.append({"tool": "submit_fix", "input": {"n_edits": len(edits)}})
                try:
                    submitted_diff = _apply_edits(repo_dir, edits)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "submitted",
                    })
                    _append_log(log_path, {
                        "event": "submitted", "n_edits": len(edits), "diff_len": len(submitted_diff),
                    })
                except ValueError as e:
                    # Rejected edit (old_string missing/non-unique): a
                    # retryable tool error, not a fatal failure — the model
                    # sees this and can correct it on the next turn.
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(e),
                        "is_error": True,
                    })
                    _append_log(log_path, {"event": "submit_rejected", "error": str(e)})
                continue
            if block.name != "check_edit":
                # check_edit is free (doesn't count against the exploration
                # budget) so there's no cost-based disincentive to using it
                # before submit_fix, which is the whole point of adding it.
                tool_calls_used += 1
            result_text = _run_tool(repo_dir, block.name, block.input)
            transcript.append({"tool": block.name, "input": block.input, "tool_result": result_text[:500]})
            _append_log(log_path, {
                "event": "tool_result",
                "tool": block.name,
                "input": block.input,
                "result_preview": result_text[:300],
            })
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                }
            )

        if submitted_diff is not None:
            return GroundingResult(submitted_diff, usage, transcript, tool_calls_used)

        messages.append({"role": "user", "content": tool_results})


def self_verification_judge(
    problem_statement: str, diff: str, timeout: float = 90.0, model: Optional[str] = None,
    max_tokens: int = 4000,
) -> tuple[bool, str, "llm.LLMUsage"]:
    review_input = f"## GitHub issue\n{problem_statement}\n\n## Your diff\n{diff}"
    raw, usage = _with_retries(
        lambda: llm.call_llm(SELF_VERIFICATION_SYSTEM, review_input, timeout=timeout, model=model, max_tokens=max_tokens)
    )
    try:
        result = llm.extract_json(raw)
        return bool(result.get("judged_complete", True)), result.get("reasoning", ""), usage
    except ValueError as e:
        # A malformed judge response shouldn't discard the Step A/B work
        # already paid for and completed — treat conservatively as a
        # failed judgment, same posture as elsewhere in this project.
        return False, f"JUDGE_PARSE_FAILED: {e}", usage


def execution_aware_judge(
    problem_statement: str, diff: str, real_test_output: str, timeout: float = 90.0, model: Optional[str] = None,
    max_tokens: int = 4000,
) -> tuple[bool, str, "llm.LLMUsage"]:
    review_input = (
        f"## GitHub issue\n{problem_statement}\n\n## Your diff\n{diff}\n\n"
        f"## Test output\n{real_test_output}"
    )
    raw, usage = _with_retries(
        lambda: llm.call_llm(EXECUTION_AWARE_JUDGE_SYSTEM, review_input, timeout=timeout, model=model, max_tokens=max_tokens)
    )
    try:
        result = llm.extract_json(raw)
        return bool(result.get("judged_complete", True)), result.get("reasoning", ""), usage
    except ValueError as e:
        return False, f"JUDGE_PARSE_FAILED: {e}", usage
