"""Unit tests for the agent's two tools.

Cloud Storage and Cloud Vision are replaced with mocks, so these tests run
offline, cost nothing, and need no Google Cloud credentials.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from healthy_hints import agent

BUCKET = agent.BUCKET_NAME


# ---------- fixtures ----------

@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """Point the tools at a fresh temporary images folder."""
    folder = tmp_path.resolve()
    monkeypatch.setattr(agent, "UPLOAD_DIR", folder)
    return folder


@pytest.fixture
def fake_storage(monkeypatch):
    """Replace storage.Client so uploads never leave the machine."""
    client = MagicMock()
    monkeypatch.setattr(agent.storage, "Client", MagicMock(return_value=client))
    return client


def make_vision_response(texts=None, error=""):
    annotations = [SimpleNamespace(description=t) for t in (texts or [])]
    return SimpleNamespace(
        error=SimpleNamespace(message=error),
        text_annotations=annotations,
    )


@pytest.fixture
def fake_vision(monkeypatch):
    """Replace the Vision client; tests set its return value."""
    client = MagicMock()
    monkeypatch.setattr(
        agent.vision, "ImageAnnotatorClient", MagicMock(return_value=client)
    )
    return client


# ---------- upload_image ----------

def test_upload_returns_unique_gcs_uri(upload_dir, fake_storage):
    (upload_dir / "label_a.jpeg").write_bytes(b"fake image")

    uri = agent.upload_image("label_a.jpeg")

    assert uri.startswith(f"gs://{BUCKET}/uploads/")
    assert uri.endswith(".jpeg")
    blob = fake_storage.bucket.return_value.blob.return_value
    blob.upload_from_filename.assert_called_once_with(
        str(upload_dir / "label_a.jpeg"), content_type="image/jpeg"
    )


def test_two_uploads_get_different_uris(upload_dir, fake_storage):
    (upload_dir / "label_a.jpeg").write_bytes(b"a")

    first = agent.upload_image("label_a.jpeg")
    second = agent.upload_image("label_a.jpeg")

    assert first != second


@pytest.mark.parametrize(
    "file_name, content_type",
    [("x.jpg", "image/jpeg"), ("x.PNG", "image/png"), ("x.webp", "image/webp")],
)
def test_upload_sets_content_type(upload_dir, fake_storage, file_name, content_type):
    (upload_dir / file_name).write_bytes(b"img")

    agent.upload_image(file_name)

    blob = fake_storage.bucket.return_value.blob.return_value
    assert blob.upload_from_filename.call_args.kwargs["content_type"] == content_type


@pytest.mark.parametrize("bad_path", ["../secret.jpeg", "/etc/passwd"])
def test_upload_rejects_paths_outside_images_folder(upload_dir, fake_storage, bad_path):
    with pytest.raises(ValueError, match="allowed images directory"):
        agent.upload_image(bad_path)
    fake_storage.bucket.assert_not_called()


def test_upload_rejects_missing_file(upload_dir, fake_storage):
    with pytest.raises(FileNotFoundError):
        agent.upload_image("does_not_exist.jpeg")


def test_upload_rejects_unsupported_type(upload_dir, fake_storage):
    (upload_dir / "label.gif").write_bytes(b"gif")

    with pytest.raises(ValueError, match="JPG, PNG, or WebP"):
        agent.upload_image("label.gif")


def test_upload_rejects_files_over_8_mib(upload_dir, fake_storage):
    big = upload_dir / "big.jpeg"
    with big.open("wb") as f:
        f.truncate(8 * 1024 * 1024 + 1)

    with pytest.raises(ValueError, match="8 MiB"):
        agent.upload_image("big.jpeg")


# ---------- read_ingredients ----------

def test_read_returns_full_ocr_text(fake_vision):
    fake_vision.text_detection.return_value = make_vision_response(
        ["Ingredients: oats, salt", "Ingredients:", "oats"]
    )
    uri = f"gs://{BUCKET}/uploads/abc.jpeg"

    text = agent.read_ingredients(uri)

    assert text == "Ingredients: oats, salt"
    sent_image = fake_vision.text_detection.call_args.kwargs["image"]
    assert sent_image.source.image_uri == uri


def test_read_handles_image_with_no_text(fake_vision):
    fake_vision.text_detection.return_value = make_vision_response([])

    text = agent.read_ingredients(f"gs://{BUCKET}/uploads/blank.jpeg")

    assert text == "No readable text detected in the image."


def test_read_raises_on_vision_error(fake_vision):
    fake_vision.text_detection.return_value = make_vision_response(
        error="Bad image data"
    )

    with pytest.raises(RuntimeError, match="Bad image data"):
        agent.read_ingredients(f"gs://{BUCKET}/uploads/bad.jpeg")


@pytest.mark.parametrize(
    "bad_uri",
    [
        "gs://someone-elses-bucket/uploads/x.jpeg",
        f"gs://{BUCKET}/private/x.jpeg",
        "https://example.com/x.jpeg",
    ],
)
def test_read_rejects_uris_outside_upload_prefix(fake_vision, bad_uri):
    with pytest.raises(ValueError, match="approved upload prefix"):
        agent.read_ingredients(bad_uri)
    fake_vision.text_detection.assert_not_called()


# ---------- the pipeline ----------

def test_upload_uri_feeds_straight_into_ocr(upload_dir, fake_storage, fake_vision):
    """The core fix: OCR runs on exactly the URI the upload returned."""
    (upload_dir / "label_a.jpeg").write_bytes(b"img")
    fake_vision.text_detection.return_value = make_vision_response(["Milk Protein"])

    uri = agent.upload_image("label_a.jpeg")
    text = agent.read_ingredients(uri)

    sent_image = fake_vision.text_detection.call_args.kwargs["image"]
    assert sent_image.source.image_uri == uri
    assert text == "Milk Protein"


def test_agent_registers_both_tools():
    tool_names = {tool.__name__ for tool in agent.root_agent.tools}
    assert tool_names == {"upload_image", "read_ingredients"}
