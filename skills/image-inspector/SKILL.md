---
name: image-inspector
description: Caption and summarize images so Nulla can reason over pictures via text.
version: 0.1.0
license: MIT
author: Parad0x Labs
status: experimental
type: tool
entrypoints:
  run: run_image_inspector.py
---

# Image Inspector — helper skill

Image Inspector turns PNG/JPEG inputs into ready-made textual observations. It runs a local Hugging Face image-to-text pipeline (`Salesforce/blip-image-captioning-base` by default) and prints either plain text or JSON describing what it sees. Use the output as the user prompt you send to Nulla’s `/chat` or `/v1/chat/completions` endpoint.

## Quick start

```bash
python skills/image-inspector/run_image_inspector.py assets/screenshot.png --format text
python skills/image-inspector/run_image_inspector.py photos/*.jpg --format json
```

Pass the resulting message body to Nulla:

```bash
curl http://127.0.0.1:11435/chat \
  -H "Content-Type: application/json" \
  -d "$(python skills/image-inspector/run_image_inspector.py photos/menu.png --format json)"
```

## Options

| Flag | Description |
| ---- | ----------- |
| `--model` | Hugging Face model to feed to the pipeline (default: `Salesforce/blip-image-captioning-base`). |
| `--device cpu|auto|cuda` | Choose CPU/GPU. `auto` uses GPU when available. |
| `--num-beams` | Beam width for generation (default: `3`). |
| `--max-new-tokens` | Max caption length (default: `32`). |
| `--template` | Format per-image lines (`{index}`, `{path}`, `{caption}` available). |
| `--format` | `text` (human readable) or `json` (includes metadata). |

## Why it matters

Nulla’s reasoning stack is text-only. Image Inspector lets you stay local: you describe what the picture shows, then feed that description into the usual chat/research loop so the agent can reason about the visual content without adding a new multimodal endpoint.
