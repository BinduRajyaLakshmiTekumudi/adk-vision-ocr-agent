"""FastAPI service: upload a food-label photo from a browser and get the agent's analysis.

Run locally:
    uvicorn api.main:app --reload --port 8080

Endpoints:
    GET  /         simple upload page
    GET  /health   liveness check
    POST /analyze  multipart image upload -> {"gcs_uri", "analysis"}
"""

import uuid
from pathlib import Path

from dotenv import load_dotenv

# `adk web` reads healthy_hints/.env automatically; uvicorn does not, so load it
# here before the agent module reads its settings. Existing environment
# variables (for example on Cloud Run) take priority.
load_dotenv(Path(__file__).resolve().parent.parent / "healthy_hints" / ".env")

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from healthy_hints.agent import (  # noqa: E402
    MAX_IMAGE_BYTES,
    root_agent,
    store_uploaded_image,
)

APP_NAME = "healthy_hints"
INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()

app = FastAPI(
    title="ADK Vision OCR Agent",
    description="Upload a food-label photo; the ADK agent reads it with Cloud Vision OCR and explains the ingredients.",
    version="1.0.0",
)

_runner: InMemoryRunner | None = None


def get_runner() -> InMemoryRunner:
    """Create the ADK runner once, on first use."""
    global _runner
    if _runner is None:
        _runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)
    return _runner


async def run_agent(prompt: str) -> str:
    """Send one message to the agent in a fresh session and return its final reply."""
    runner = get_runner()
    user_id = "web-user"
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=uuid.uuid4().hex
    )
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    reply = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            reply = "".join(part.text or "" for part in event.content.parts)
    return reply


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(file: UploadFile = File(...)) -> dict:
    # Read one byte past the limit so oversized files are caught without
    # loading arbitrarily large uploads into memory.
    data = await file.read(MAX_IMAGE_BYTES + 1)
    try:
        gcs_uri = store_uploaded_image(data)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    prompt = (
        f"The ingredient-label image is already uploaded at {gcs_uri}. "
        "Do not call upload_image. Call read_ingredients with exactly that URI, "
        "then tell me what text was recognized and explain the ingredients "
        "with appropriate caveats."
    )
    try:
        analysis = await run_agent(prompt)
    except Exception as err:  # model or cloud errors after retries
        raise HTTPException(status_code=502, detail=f"Agent failed: {err}") from err

    return {"gcs_uri": gcs_uri, "analysis": analysis}
