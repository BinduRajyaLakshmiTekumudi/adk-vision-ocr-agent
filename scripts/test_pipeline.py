"""Test the upload -> OCR pipeline directly, without calling Gemini.

Usage (from the repo root):
    python scripts/test_pipeline.py label_a.jpeg label_b.jpeg

If this passes but the agent chat fails, the problem is in tool calling.
If this fails, check bucket permissions, credentials, paths, or API enablement.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from healthy_hints.agent import read_ingredients, upload_image  # noqa: E402


def main(image_names: list[str]) -> None:
    uris = []
    for name in image_names:
        uri = upload_image(name)
        uris.append(uri)
        print(f"{name} -> {uri}")

    assert len(set(uris)) == len(uris), "Uploads should get unique object names"

    for name, uri in zip(image_names, uris):
        print(f"\n--- OCR for {name} ---")
        print(read_ingredients(uri))


if __name__ == "__main__":
    main(sys.argv[1:] or ["label_a.jpeg", "label_b.jpeg"])