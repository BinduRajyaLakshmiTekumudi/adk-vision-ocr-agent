"""Tests for the evaluation harness: scoring rules and an offline end-to-end run.

The end-to-end test uses a fake model that makes the same tool calls Gemini
would, with Storage and Vision mocked, so the harness is checked without any
API calls.
"""

import asyncio
import re
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import MagicMock

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from evals import run_eval
from evals.scoring import score_case, summarize
from healthy_hints import agent

CASE = {
    "id": "granola_bar",
    "condition": "clean",
    "has_ingredient_list": True,
    "expected_ingredients": ["rolled oats", "honey", "almonds", "sea salt"],
    "unlisted_traps": ["peanut", "chocolate"],
}
NO_LIST_CASE = {
    "id": "nutrition_only",
    "condition": "no ingredient list",
    "has_ingredient_list": False,
    "expected_ingredients": [],
    "unlisted_traps": ["flour", "corn"],
}
URI = "gs://test-bucket/uploads/abc.png"
GOOD_CALLS = [("upload_image", {"image_path": "x.png"}), ("read_ingredients", {"gcs_uri": URI})]
GOOD_RESPONSES = [("upload_image", {"result": URI}), ("read_ingredients", {"result": "INGREDIENTS: ..."})]


def trace(answer, calls=GOOD_CALLS, responses=GOOD_RESPONSES, error=None):
    return {"calls": calls, "responses": responses, "answer": answer, "latency_s": 2.0, "error": error}


# ---------- scoring ----------

def test_perfect_run_passes():
    answer = "Rolled oats, honey, almonds and sea salt. Not medical advice."
    result = score_case(CASE, trace(answer))
    assert result["passed"] and result["recall"] == 1.0 and result["uri_linked"]


def test_invented_ingredient_fails():
    answer = "Rolled oats, honey, almonds, sea salt, and peanuts. Not medical advice."
    result = score_case(CASE, trace(answer))
    assert not result["passed"]
    assert result["unlisted_mentions"] == ["peanut"]


def test_low_recall_fails_and_lists_missed():
    answer = "Rolled oats and honey. Educational information only."
    result = score_case(CASE, trace(answer))
    assert result["recall"] == 0.5
    assert result["missed"] == ["almonds", "sea salt"]
    assert not result["passed"]


def test_wrong_tool_order_and_unlinked_uri_fail():
    calls = [("read_ingredients", {"gcs_uri": "gs://test-bucket/uploads/other.png"})]
    answer = "Rolled oats, honey, almonds, sea salt. Not medical advice."
    result = score_case(CASE, trace(answer, calls=calls))
    assert not result["tool_flow_ok"] and not result["uri_linked"] and not result["passed"]


def test_missing_list_must_be_reported():
    good = "The image shows a Nutrition Facts panel; the ingredient list is not visible. Consult a dietitian."
    bad = "Calories 160, protein 4g. Consult a dietitian."
    assert score_case(NO_LIST_CASE, trace(good))["handled_missing_list"] is True
    assert score_case(NO_LIST_CASE, trace(bad))["passed"] is False


def test_missing_disclaimer_fails():
    answer = "Rolled oats, honey, almonds, sea salt."
    assert not score_case(CASE, trace(answer))["passed"]


def test_summary_counts():
    scores = [
        score_case(CASE, trace("Rolled oats, honey, almonds, sea salt. Not medical advice.")),
        score_case(CASE, trace("Rolled oats, peanuts. Not medical advice.")),
    ]
    summary = summarize(scores)
    assert summary["passed"] == 1
    assert summary["cases_with_unlisted_mentions"] == 1
    assert summary["mean_recall"] == 0.625  # (4/4 + 1/4) / 2


# ---------- offline end-to-end run ----------

class ToolCallingFakeLlm(BaseLlm):
    """Behaves like Gemini on this task: upload, then OCR the returned URI, then answer."""

    model: str = "fake-tool-caller"

    async def generate_content_async(self, llm_request, stream=False) -> AsyncGenerator[LlmResponse, None]:
        parts = [p for c in llm_request.contents for p in (c.parts or [])]
        responses = {p.function_response.name: p.function_response.response for p in parts if p.function_response}
        prompt = next(p.text for p in parts if p.text)

        if "read_ingredients" in responses:
            ocr = responses["read_ingredients"]["result"]
            part = types.Part(text=f"Recognized: {ocr}\nThis is educational information, not medical advice.")
        elif "upload_image" in responses:
            uri = responses["upload_image"]["result"]
            part = types.Part(function_call=types.FunctionCall(name="read_ingredients", args={"gcs_uri": uri}))
        else:
            image = re.search(r"ingredients in (\S+?)\. Upload", prompt).group(1)
            part = types.Part(function_call=types.FunctionCall(name="upload_image", args={"image_path": image}))
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def test_harness_records_tool_flow_and_scores_it(tmp_path, monkeypatch):
    image_dir = tmp_path.resolve()
    (image_dir / "synthetic").mkdir()
    (image_dir / "synthetic" / "granola_bar.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(agent, "UPLOAD_DIR", image_dir)
    monkeypatch.setattr(agent.storage, "Client", MagicMock())
    vision_client = MagicMock()
    vision_client.text_detection.return_value = SimpleNamespace(
        error=SimpleNamespace(message=""),
        text_annotations=[SimpleNamespace(description="INGREDIENTS: Rolled Oats, Honey, Almonds, Sea Salt.")],
    )
    monkeypatch.setattr(agent.vision, "ImageAnnotatorClient", MagicMock(return_value=vision_client))

    test_agent = Agent(
        name="eval_test_agent",
        model=ToolCallingFakeLlm(),
        tools=[agent.upload_image, agent.read_ingredients],
    )
    runner = InMemoryRunner(agent=test_agent, app_name=run_eval.APP_NAME)
    case = dict(CASE, image="synthetic/granola_bar.png")

    result_trace = asyncio.run(run_eval.run_case(runner, case))
    result = score_case(case, result_trace)

    assert [name for name, _ in result_trace["calls"]] == ["upload_image", "read_ingredients"]
    assert result["uri_linked"]
    assert result["recall"] == 1.0
    assert result["passed"], result


# ---------- infrastructure errors are not agent failures ----------

QUOTA_ERROR = "_ResourceExhaustedError: 429 RESOURCE_EXHAUSTED ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"


def test_errored_case_is_reported_separately():
    result = score_case(CASE, trace("", calls=[], responses=[], error=QUOTA_ERROR))
    assert result["status"] == "ERROR"


def test_summary_excludes_errored_cases():
    scores = [
        score_case(CASE, trace("Rolled oats, honey, almonds, sea salt. Not medical advice.")),
        score_case(CASE, trace("", calls=[], responses=[], error=QUOTA_ERROR)),
    ]
    summary = summarize(scores)
    assert summary["completed"] == 1 and summary["errored"] == 1
    assert summary["passed"] == 1
    assert summary["mean_recall"] == 1.0


def test_report_explains_quota_errors():
    scores = [score_case(CASE, trace("", calls=[], responses=[], error=QUOTA_ERROR))]
    report = run_eval.to_markdown(summarize(scores), scores, "test-model", "now")
    assert "| ERROR |" in report
    assert "daily Gemini quota exhausted" in report


def test_merge_combines_partial_runs(tmp_path, monkeypatch):
    """Day 1 hits the quota on case 2; day 2 reruns only case 2 with --merge."""
    import json

    cases = [dict(CASE, id="one", image="one.png"), dict(CASE, id="two", image="two.png")]
    (tmp_path / "cases.json").write_text(json.dumps(cases))
    (tmp_path / "images").mkdir()
    for c in cases:
        (tmp_path / "images" / c["image"]).write_bytes(b"x")
    monkeypatch.setattr(run_eval, "EVALS_DIR", tmp_path)

    good = trace("Rolled oats, honey, almonds, sea salt. Not medical advice.")
    quota = trace("", calls=[], responses=[], error=QUOTA_ERROR + " PerDay")

    async def day1(runner, case):
        return good if case["id"] == "one" else quota

    async def day2(runner, case):
        return good

    monkeypatch.setattr(run_eval, "run_case", day1)
    asyncio.run(run_eval.main([], 0, merge=False))
    monkeypatch.setattr(run_eval, "run_case", day2)
    asyncio.run(run_eval.main(["two"], 0, merge=True))

    latest = json.loads((tmp_path / "results" / "latest.json").read_text())
    assert [s["id"] for s in latest["scores"]] == ["one", "two"]
    assert latest["summary"]["passed"] == 2 and latest["summary"]["errored"] == 0


def test_prepaid_credit_error_stops_the_run():
    error = "ClientError: 402 RESOURCE_EXHAUSTED. Your prepayment credits are depleted."
    assert run_eval.is_daily_quota_error(error)
    assert "add credits" in run_eval.short_error(error)
