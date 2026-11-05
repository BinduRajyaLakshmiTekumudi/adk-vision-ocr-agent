"""Score one agent run against its test case. Pure functions, no cloud calls."""

import re

MISSING_LIST = re.compile(
    r"(no|not|missing|absent|without|cannot|can't|unable|isn't|wasn't)[^.\n]{0,60}ingredient"
    r"|ingredient[^.\n]{0,60}(not (visible|present|shown|included|listed|available)|missing|absent|unavailable)",
    re.IGNORECASE,
)
NO_TEXT = re.compile(r"no (readable )?text|could not (read|detect)|unable to (read|detect)", re.IGNORECASE)
DISCLAIMER = re.compile(
    r"medical advice|not (a substitute|personalized)|educational|informational purposes|consult",
    re.IGNORECASE,
)


def mentions(text: str, term: str) -> bool:
    """Whole-word, case-insensitive match; tolerates a trailing plural 's'."""
    return re.search(rf"\b{re.escape(term)}s?\b", text, re.IGNORECASE) is not None


def score_case(case: dict, trace: dict) -> dict:
    """Return per-case metrics.

    trace = {"calls": [(name, args)], "responses": [(name, response)],
             "answer": str, "latency_s": float, "error": str | None}
    """
    answer = trace.get("answer") or ""
    calls = trace.get("calls", [])
    responses = dict(trace.get("responses", []))
    call_names = [name for name, _ in calls]

    tool_flow_ok = call_names == ["upload_image", "read_ingredients"]

    upload_result = (responses.get("upload_image") or {}).get("result")
    ocr_arg = next((args.get("gcs_uri") for name, args in calls if name == "read_ingredients"), None)
    uri_linked = bool(upload_result) and upload_result == ocr_arg

    expected = case["expected_ingredients"]
    found = [ing for ing in expected if mentions(answer, ing)]
    recall = len(found) / len(expected) if expected else None

    unlisted = [term for term in case["unlisted_traps"] if mentions(answer, term)]

    if case["has_ingredient_list"]:
        handled_missing = None
    else:
        handled_missing = bool(MISSING_LIST.search(answer) or NO_TEXT.search(answer))

    disclaimer = bool(DISCLAIMER.search(answer))

    passed = (
        not trace.get("error")
        and tool_flow_ok
        and uri_linked
        and (recall is None or recall >= 0.8)
        and not unlisted
        and handled_missing is not False
        and disclaimer
    )

    return {
        "id": case["id"],
        "condition": case["condition"],
        "status": "ERROR" if trace.get("error") else ("PASS" if passed else "FAIL"),
        "passed": passed,
        "tool_flow_ok": tool_flow_ok,
        "uri_linked": uri_linked,
        "recall": recall,
        "missed": [ing for ing in expected if ing not in found],
        "unlisted_mentions": unlisted,
        "handled_missing_list": handled_missing,
        "disclaimer": disclaimer,
        "latency_s": round(trace.get("latency_s", 0.0), 1),
        "error": trace.get("error"),
    }


def summarize(scores: list[dict]) -> dict:
    """Aggregate metrics over the cases that completed.

    Cases that hit an infrastructure error (quota, network) say nothing about
    the agent's quality, so they are counted separately, not as failures.
    """
    errored = [s for s in scores if s["status"] == "ERROR"]
    done = [s for s in scores if s["status"] != "ERROR"]
    n = len(done)
    recalls = [s["recall"] for s in done if s["recall"] is not None]
    missing_cases = [s for s in done if s["handled_missing_list"] is not None]
    return {
        "cases": len(scores),
        "completed": n,
        "errored": len(errored),
        "passed": sum(s["passed"] for s in done),
        "tool_flow_ok": sum(s["tool_flow_ok"] for s in done),
        "uri_linked": sum(s["uri_linked"] for s in done),
        "mean_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
        "cases_with_unlisted_mentions": sum(bool(s["unlisted_mentions"]) for s in done),
        "handled_missing_list": f"{sum(s['handled_missing_list'] for s in missing_cases)}/{len(missing_cases)}",
        "disclaimer": sum(s["disclaimer"] for s in done),
        "mean_latency_s": round(sum(s["latency_s"] for s in done) / n, 1) if n else None,
    }
