"""Tests for the FastAPI upload service.

Cloud Storage and the agent run are mocked, so no network or credentials are used.
"""

import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from api import main
from healthy_hints import agent

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fake_storage(monkeypatch):
    storage_client = MagicMock()
    monkeypatch.setattr(agent.storage, "Client", MagicMock(return_value=storage_client))
    return storage_client


@pytest.fixture
def fake_agent(monkeypatch):
    run = AsyncMock(return_value="Ingredients: oats. General information only.")
    monkeypatch.setattr(main, "run_agent", run)
    return run


# ---------- content sniffing ----------

@pytest.mark.parametrize(
    "data, expected",
    [(JPEG, ".jpeg"), (PNG, ".png"), (WEBP, ".webp"),
     (b"#!/bin/sh\nrm -rf /", None), (b"GIF89a....", None), (b"", None)],
)
def test_sniff_image_type(data, expected):
    assert agent.sniff_image_type(data) == expected


# ---------- POST /analyze ----------

def test_analyze_uploads_and_returns_analysis(client, fake_storage, fake_agent):
    res = client.post("/analyze", files={"file": ("label.jpg", JPEG, "image/jpeg")})

    assert res.status_code == 200
    body = res.json()
    assert body["gcs_uri"].startswith(f"gs://{agent.BUCKET_NAME}/uploads/")
    assert body["gcs_uri"].endswith(".jpeg")
    assert body["analysis"].startswith("Ingredients: oats")

    blob = fake_storage.bucket.return_value.blob.return_value
    blob.upload_from_string.assert_called_once_with(JPEG, content_type="image/jpeg")


def test_analyze_passes_the_uploaded_uri_to_the_agent(client, fake_storage, fake_agent):
    res = client.post("/analyze", files={"file": ("label.png", PNG, "image/png")})

    prompt = fake_agent.await_args.args[0]
    assert res.json()["gcs_uri"] in prompt
    assert "Do not call upload_image" in prompt


def test_analyze_uses_real_content_not_file_name(client, fake_storage, fake_agent):
    # A PNG named .jpg is stored as .png with the PNG content type.
    res = client.post("/analyze", files={"file": ("photo.jpg", PNG, "image/jpeg")})

    assert res.json()["gcs_uri"].endswith(".png")
    blob = fake_storage.bucket.return_value.blob.return_value
    assert blob.upload_from_string.call_args.kwargs["content_type"] == "image/png"


@pytest.mark.parametrize(
    "data, message",
    [
        (b"#!/bin/sh\necho hi", "not a JPG, PNG, or WebP"),
        (b"", "empty"),
        (b"\xff\xd8\xff" + b"\x00" * agent.MAX_IMAGE_BYTES, "8 MiB"),
    ],
)
def test_analyze_rejects_bad_uploads(client, fake_storage, fake_agent, data, message):
    res = client.post("/analyze", files={"file": ("label.jpg", data, "image/jpeg")})

    assert res.status_code == 400
    assert message in res.json()["detail"]
    fake_storage.bucket.assert_not_called()
    fake_agent.assert_not_awaited()


def test_analyze_returns_502_when_agent_fails(client, fake_storage, monkeypatch):
    monkeypatch.setattr(main, "run_agent", AsyncMock(side_effect=RuntimeError("429 quota")))

    res = client.post("/analyze", files={"file": ("label.jpg", JPEG, "image/jpeg")})

    assert res.status_code == 502
    assert "429 quota" in res.json()["detail"]


def test_analyze_requires_a_file(client):
    assert client.post("/analyze").status_code == 422


# ---------- run_agent with the real ADK runner ----------

class FakeLlm(BaseLlm):
    """Stands in for Gemini so the ADK runner can be exercised offline."""

    model: str = "fake-model"

    async def generate_content_async(
        self, llm_request, stream=False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="Fake analysis")])
        )


def test_run_agent_returns_final_reply(monkeypatch):
    runner = InMemoryRunner(
        agent=Agent(name="test_agent", model=FakeLlm()), app_name=main.APP_NAME
    )
    monkeypatch.setattr(main, "_runner", runner)

    assert asyncio.run(main.run_agent("hi")) == "Fake analysis"


# ---------- other routes ----------

def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_index_serves_upload_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "<form" in res.text
