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
MAX_IMAGE_BYTES = 8 * 1024 * 1024
CONTENT_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}


def sniff_image_type(data: bytes) -> str | None:
    """Return the real file type from the first bytes ('.jpeg', '.png', '.webp'), or None.

    Checks the file's actual contents (its "magic bytes"), not its name, so a
    renamed text file or script is rejected.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def store_uploaded_image(data: bytes) -> str:
    """Validate image bytes received over HTTP, upload them, and return the gs:// URI.

    Used by the web API, not registered as an agent tool.
    """
    if not data:
        raise ValueError("The uploaded file is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 8 MiB limit")
    suffix = sniff_image_type(data)
    if suffix is None:
        raise ValueError("File content is not a JPG, PNG, or WebP image")

    object_name = f"uploads/{uuid.uuid4().hex}{suffix}"
    blob = storage.Client().bucket(BUCKET_NAME).blob(object_name)
    blob.upload_from_string(data, content_type=CONTENT_TYPES[suffix])
    return f"gs://{BUCKET_NAME}/{object_name}"


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
    if candidate.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 8 MiB classroom limit")

    content_type = CONTENT_TYPES[candidate.suffix.lower()]
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
        "If the user instead gives a gs:// URI of an image that is already "
        "uploaded, skip upload_image and call read_ingredients with exactly "
        "that URI. "
        "Use the returned OCR text to explain the ingredients, "
        "not a remembered or invented ingredient list. "
        "If OCR is incomplete, say so. Avoid categorical claims that "
        "an ingredient is healthy or unhealthy without context. "
        "Do not provide personalized medical advice."
    ),
    tools=[upload_image, read_ingredients],
)