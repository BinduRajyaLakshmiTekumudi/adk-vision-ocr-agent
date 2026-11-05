"""Render the synthetic label images described in evals/cases.json.

Each synthetic label has a known ingredient list (the ground truth), drawn with
a controlled difficulty: small font, rotation, low contrast, blur, a label with
only a nutrition panel, or a blank image.

Usage (from the repo root):
    python evals/make_synthetic_labels.py
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

EVALS_DIR = Path(__file__).resolve().parent
OUT_DIR = EVALS_DIR / "images" / "synthetic"
WIDTH = 900
MARGIN = 50


def wrap(draw, text, font, max_width):
    """Split text into lines that fit max_width."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def nutrition_panel(body):
    return [
        ("Nutrition Facts", True),
        ("Serving Size 1 Cup (40g)", False),
        ("Calories 160", True),
        ("Total Fat 6g  8%", False),
        ("Sodium 210mg  9%", False),
        ("Total Carbohydrate 24g  9%", False),
        ("Dietary Fiber 3g  11%", False),
        ("Total Sugars 7g", False),
        ("Protein 4g", False),
    ] if body else []


def render(spec):
    if spec.get("blank"):
        return Image.new("RGB", (WIDTH, 700), (238, 236, 230))

    size = spec.get("font_size", 26)
    title_font = ImageFont.load_default(size=size + 12)
    body_font = ImageFont.load_default(size=size)
    bold_font = ImageFont.load_default(size=size + 2)

    bg, fg = ((222, 220, 214), (160, 158, 152)) if spec.get("low_contrast") else ((255, 255, 255), (20, 20, 20))

    canvas = Image.new("RGB", (WIDTH, 1400), bg)
    draw = ImageDraw.Draw(canvas)
    y = MARGIN
    draw.text((MARGIN, y), spec["title"], font=title_font, fill=fg)
    y += size + 40

    for text, bold in nutrition_panel(True):
        draw.text((MARGIN, y), text, font=bold_font if bold else body_font, fill=fg)
        y += size + 12
    y += 20

    if not spec.get("nutrition_only"):
        lines = wrap(draw, "INGREDIENTS: " + spec["ingredients"], body_font, WIDTH - 2 * MARGIN)
        for line in lines:
            draw.text((MARGIN, y), line, font=body_font, fill=fg)
            y += size + 10
        if spec.get("contains"):
            y += 10
            draw.text((MARGIN, y), spec["contains"], font=bold_font, fill=fg)
            y += size + 10

    image = canvas.crop((0, 0, WIDTH, y + MARGIN))
    if spec.get("rotate"):
        image = image.rotate(spec["rotate"], expand=True, fillcolor=bg)
    if spec.get("blur"):
        image = image.filter(ImageFilter.GaussianBlur(spec["blur"]))
    return image


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = json.loads((EVALS_DIR / "cases.json").read_text())
    for case in cases:
        if "synthetic" not in case:
            continue
        path = EVALS_DIR / "images" / case["image"]
        render(case["synthetic"]).save(path)
        print(f"wrote {path.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    main()
