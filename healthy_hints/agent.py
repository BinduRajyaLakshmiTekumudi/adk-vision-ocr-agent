import os
import uuid
from pathlib import Path

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.cloud import storage, vision
from google.genai import types

BUCKET_NAME = os.environ["HEALTHY_HINTS_BUCKET"]
UPLOAD_DIR = Path(os.getenv("HEALTHY_HINTS_UPLOAD_DIR", "./images"))
UPLOAD_DIR = UPLOAD_DIR.resolve()
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def upload_image(image_path: str) -> str:
    """Upload a local ingredient image; return its unique gs:// URI.

    image_path must refer to a file within HEALTHY_HINTS_UPLOAD_DIR.
    """
    candidate = Path(image_path)
    if not candidate.is_absolute():
        candidate = UPLOAD_DIR / candidate
    candidate = candidate.resolve()

    if not candidate.is_relative_to(UPLOAD_DIR):
        raise ValueError("Image must be inside the allowed images directory")
    if not candidate.is_file():
        raise FileNotFoundError(f"Image not found: {candidate.name}")
    if candidate.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("Only JPG, PNG, or WebP images are allowed")
    if candidate.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Image exceeds the 8 MiB classroom limit")

    content_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }[candidate.suffix.lower()]
    object_name = f"uploads/{uuid.uuid4().hex}{candidate.suffix.lower()}"
    bucket = storage.Client().bucket(BUCKET_NAME)
    blob = bucket.blob(object_name)
    blob.upload_from_filename(str(candidate), content_type=content_type)
    return f"gs://{BUCKET_NAME}/{object_name}"


def read_ingredients(gcs_uri: str) -> str:
    """Run Google Cloud Vision OCR on a specified GCS image URI."""
    prefix = f"gs://{BUCKET_NAME}/uploads/"
    if not gcs_uri.startswith(prefix):
        raise ValueError("Image URI must be in the approved upload prefix")

    client = vision.ImageAnnotatorClient()
    image = vision.Image(source=vision.ImageSource(image_uri=gcs_uri))
    response = client.text_detection(image=image)

    if response.error.message:
        raise RuntimeError(f"Vision OCR failed: {response.error.message}")
    if not response.text_annotations:
        return "No readable text detected in the image."
    return response.text_annotations[0].description


root_agent = Agent(
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(initial_delay=2, attempts=5),
    ),
    name="healthy_hints_agent",
    description="Analyze ingredient-label images using GCS and Vision OCR",
    instruction=(
        "You help users understand packaged-food ingredient labels. "
        "When the user supplies a local image filename, first call "
        "upload_image(image_path) and capture its returned gs:// URI. "
        "Then call read_ingredients(gcs_uri) with EXACTLY that URI. "
        "Use the returned OCR text to explain the ingredients, "
        "not a remembered or invented ingredient list. "
        "If OCR is incomplete, say so. Avoid categorical claims that "
        "an ingredient is healthy or unhealthy without context. "
        "Do not provide personalized medical advice."
    ),
    tools=[upload_image, read_ingredients],
)