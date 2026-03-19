"""Describe pictures for Nulla by turning them into ready-made prompts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from PIL import Image, UnidentifiedImageError
from transformers import pipeline
import torch

DEFAULT_MODEL = "Salesforce/blip-image-captioning-base"


def _build_pipeline(model: str, device: int):
    return pipeline("image-to-text", model=model, device=device, return_text=True)


def _normalize_device(device_arg: str) -> int:
    if device_arg == "auto":
        return 0 if torch.cuda.is_available() else -1
    if device_arg == "cpu":
        return -1
    if device_arg == "cuda":
        return 0
    try:
        return int(device_arg)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"unknown device value {device_arg!r}") from exc


def describe_images(
    images: Iterable[Path],
    *,
    model_name: str,
    device: int,
    num_beams: int,
    max_new_tokens: int,
) -> list[dict]:
    pipe = _build_pipeline(model=model_name, device=device)
    observations: list[dict] = []
    for image_path in images:
        try:
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                result = pipe(
                    img,
                    num_beams=num_beams,
                    max_new_tokens=max_new_tokens,
                )
        except UnidentifiedImageError:
            observations.append(
                {
                    "path": str(image_path),
                    "caption": "",
                    "score": 0.0,
                    "error": "not a supported image",
                }
            )
            continue

        generated = result[0]
        caption = generated.get("generated_text", "").strip()
        observations.append(
            {
                "path": str(image_path),
                "caption": caption,
                "score": generated.get("score"),
            }
        )
    return observations


def build_message(
    results: list[dict],
    template: str,
) -> str:
    lines = []
    for index, result in enumerate(results, start=1):
        lines.append(
            template.format(index=index, path=Path(result["path"]).name, caption=result["caption"])
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "images",
        metavar="IMAGE",
        type=Path,
        nargs="+",
        help="Paths to the images you want summarized.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Hugging Face model name for the image-to-text pipeline.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device for the caption pipeline.",
    )
    parser.add_argument(
        "--num-beams",
        type=int,
        default=3,
        help="Beam width used during generation.",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=32,
        help="Maximum number of tokens generated per caption.",
    )
    parser.add_argument(
        "--template",
        default="Image {index}: {caption}",
        help="How to format each caption line.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format. \"json\" includes structured metadata.",
    )

    args = parser.parse_args()
    device = _normalize_device(args.device)

    observations = describe_images(
        args.images,
        model_name=args.model,
        device=device,
        num_beams=args.num_beams,
        max_new_tokens=args.max_new_tokens,
    )

    message = build_message(observations, template=args.template)

    if args.format == "json":
        payload = {"message": message, "observations": observations}
        print(json.dumps(payload, indent=2))
        return

    if not message:
        print("No captions produced.")
        return

    print(message)


if __name__ == "__main__":
    main()
