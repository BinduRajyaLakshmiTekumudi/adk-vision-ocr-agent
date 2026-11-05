"""Run the agent on every case in evals/cases.json and score the results.

This calls the real Gemini, Cloud Storage, and Cloud Vision APIs, so it needs
the same setup as the agent (healthy_hints/.env and gcloud credentials).

Usage (from the repo root):
    python evals/make_synthetic_labels.py      # once, to render test images
    python evals/run_eval.py                   # all cases
    python evals/run_eval.py --only granola_bar blank_image
    python evals/run_eval.py --delay 10        # slower, for free-tier limits
    python evals/run_eval.py --only blank_image --merge   # rerun some, keep the rest

Writes evals/results/results-<timestamp>.json and evals/results/latest.md.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import logging

from dotenv import load_dotenv

EVALS_DIR = Path(__file__).resolve().parent
REPO = EVALS_DIR.parent
sys.path.insert(0, str(REPO))

load_dotenv(REPO / "healthy_hints" / ".env")
# ADK logs a full traceback for every failed model call; the report already
# records each error, so keep the console readable.
logging.getLogger("google_adk").setLevel(logging.CRITICAL)
# The agent may only upload from its images folder; point it at the eval images.
os.environ["HEALTHY_HINTS_UPLOAD_DIR"] = str(EVALS_DIR / "images")

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from evals.scoring import score_case, summarize  # noqa: E402
from healthy_hints.agent import root_agent  # noqa: E402

APP_NAME = "healthy_hints_eval"
PROMPT = (
    "Analyze the ingredients in {image}. Upload that file first, then use OCR "
    "on the returned GCS URI. Tell me what text was recognized and explain the "
    "ingredients with appropriate caveats."
)


async def run_case(runner: InMemoryRunner, case: dict) -> dict:
    """Run one case in a fresh session and record the tool calls and answer."""
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id="eval", session_id=uuid.uuid4().hex
    )
    message = types.Content(role="user", parts=[types.Part(text=PROMPT.format(image=case["image"]))])
    trace = {"calls": [], "responses": [], "answer": "", "error": None}

    start = time.perf_counter()
    try:
        async for event in runner.run_async(
            user_id="eval", session_id=session.id, new_message=message
        ):
            for call in event.get_function_calls():
                trace["calls"].append((call.name, dict(call.args or {})))
            for resp in event.get_function_responses():
                trace["responses"].append((resp.name, dict(resp.response or {})))
            if event.is_final_response() and event.content and event.content.parts:
                trace["answer"] = "".join(p.text or "" for p in event.content.parts)
    except Exception as err:  # keep going; the failure is recorded and scored
        trace["error"] = f"{type(err).__name__}: {err}"
    trace["latency_s"] = time.perf_counter() - start
    return trace


def is_daily_quota_error(error: str | None) -> bool:
    """True for errors that will repeat on every call until billing or quota changes."""
    if not error or "RESOURCE_EXHAUSTED" not in error:
        return False
    return "PerDay" in error or "prepayment credits" in error


def short_error(error: str) -> str:
    if "prepayment credits" in error:
        return "Gemini prepaid credits are $0 (402); add credits in AI Studio, then rerun"
    if is_daily_quota_error(error):
        return "daily Gemini quota exhausted (free tier); rerun after the quota resets"
    if "RESOURCE_EXHAUSTED" in error:
        return "Gemini rate limit (429); rerun with a longer --delay"
    return error.splitlines()[0][:200]


def yes_no(value) -> str:
    return "n/a" if value is None else ("yes" if value else "no")


def to_markdown(summary: dict, scores: list[dict], model: str, when: str) -> str:
    lines = [
        "# Evaluation results",
        "",
        f"Run: {when} · Model: `{model}` · Cases: {summary['cases']} "
        f"({summary['completed']} completed, {summary['errored']} not completed due to errors)",
        "",
        "Metrics below cover completed cases only.",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Cases passed | {summary['passed']}/{summary['completed']} |",
        f"| Correct tool order (upload, then OCR) | {summary['tool_flow_ok']}/{summary['completed']} |",
        f"| OCR used the exact uploaded URI | {summary['uri_linked']}/{summary['completed']} |",
        f"| Mean ingredient recall | {summary['mean_recall']} |",
        f"| Cases mentioning unlisted ingredients | {summary['cases_with_unlisted_mentions']} |",
        f"| Reported a missing list or no text | {summary['handled_missing_list']} |",
        f"| Included a disclaimer | {summary['disclaimer']}/{summary['completed']} |",
        f"| Mean latency | {summary['mean_latency_s']} s |",
        "",
        "| Case | Condition | Pass | Tools | URI linked | Recall | Missed | Unlisted mentioned | Missing list handled | Latency (s) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in scores:
        recall = "n/a" if s["recall"] is None else f"{s['recall']:.2f}"
        lines.append(
            f"| {s['id']} | {s['condition']} | {s['status']} | "
            f"{yes_no(s['tool_flow_ok'])} | {yes_no(s['uri_linked'])} | {recall} | "
            f"{', '.join(s['missed']) or '-'} | {', '.join(s['unlisted_mentions']) or '-'} | "
            f"{yes_no(s['handled_missing_list'])} | {s['latency_s']} |"
        )
    errors = [s for s in scores if s["error"]]
    if errors:
        lines += ["", "Not completed:", ""] + [f"- {s['id']}: {short_error(s['error'])}" for s in errors]
    return "\n".join(lines) + "\n"


async def main(only: list[str], delay: float, merge: bool) -> None:
    cases = json.loads((EVALS_DIR / "cases.json").read_text())
    if only:
        cases = [c for c in cases if c["id"] in only]

    missing = [c["image"] for c in cases if not (EVALS_DIR / "images" / c["image"]).is_file()]
    if missing:
        sys.exit(
            "Missing images: " + ", ".join(missing)
            + "\nRun evals/make_synthetic_labels.py and copy your real photos into evals/images/real/."
        )

    runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    scores, traces = [], {}
    quota_hit = False
    stop_reason = ""
    for i, case in enumerate(cases):
        print(f"[{i + 1}/{len(cases)}] {case['id']} ...", end=" ", flush=True)
        if quota_hit:
            # No point spending time on calls that will fail until the quota resets.
            trace = {"calls": [], "responses": [], "answer": "", "latency_s": 0.0,
                     "error": "Skipped: " + stop_reason}
            print("SKIPPED (quota or credits exhausted)")
        else:
            if i:
                await asyncio.sleep(delay)
            trace = await run_case(runner, case)
            quota_hit = is_daily_quota_error(trace["error"])
            stop_reason = trace["error"] or ""
        result = score_case(case, trace)
        scores.append(result)
        traces[case["id"]] = trace
        if not trace["error"] or not trace["error"].startswith("Skipped"):
            print(result["status"], f"({result['latency_s']}s)")

    remaining = [s["id"] for s in scores if s["status"] == "ERROR"]
    if remaining:
        print("\nTo rerun the cases that did not complete:")
        print("  python evals/run_eval.py --only " + " ".join(remaining))

    out_dir = EVALS_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    latest_json = out_dir / "latest.json"

    if merge and latest_json.is_file():
        # Keep earlier runs of cases not rerun now. They are re-scored from their
        # saved traces, so reports stay consistent if the scoring rules change.
        previous_traces = json.loads(latest_json.read_text()).get("traces", {})
        all_cases = json.loads((EVALS_DIR / "cases.json").read_text())
        traces = {**{k: v for k, v in previous_traces.items() if k not in traces}, **traces}
        scores = [score_case(c, traces[c["id"]]) for c in all_cases if c["id"] in traces]

    summary = summarize(scores)
    model = getattr(root_agent.model, "model", str(root_agent.model))
    when = datetime.now().strftime("%Y-%m-%d %H:%M")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = json.dumps(
        {"model": model, "when": when, "summary": summary, "scores": scores, "traces": traces}, indent=2
    )
    (out_dir / f"results-{stamp}.json").write_text(payload)
    latest_json.write_text(payload)
    report = to_markdown(summary, scores, model, when)
    (out_dir / "latest.md").write_text(report)
    print("\n" + report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", default=[], help="case ids to run")
    parser.add_argument("--delay", type=float, default=5.0, help="seconds between cases")
    parser.add_argument("--merge", action="store_true",
                        help="combine with the previous run, replacing only the cases run now")
    args = parser.parse_args()
    asyncio.run(main(args.only, args.delay, args.merge))
