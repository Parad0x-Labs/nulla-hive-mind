# Context Understanding Rules

When the user sends a short, fragmented, or unfinished prompt, NULLA optimizes it for her own understanding using **context understanding rules**.

## Anti-Hallucination Principle

**Only use information that exists:**
- The message itself (keywords, phrases)
- Session context (recent topics, reference targets)
- Domain vocabulary and phrase hints

**Never add:** requirements, features, or details the user did not imply.

## What Gets Expanded

1. **Phrase expansions** – e.g. "secure chat" → "secure the chat", "discord bot" → "Discord bot"
2. **Domain normalization** – "tg" → "Telegram", "nulla" → "NULLA"
3. **Session context merge** – recent topics and reference targets are merged into the working interpretation
4. **Grounding note** – when short/fragmented, a note is injected into the bootstrap: "Do not add requirements the user did not mention"

## Triggers

- `short_input` – ≤5 words
- `fragmented` – no sentence-ending punctuation

## Integration

- `core/context_understanding.py` – `expand_unfinished_for_self()`
- `core/human_input_adapter.py` – uses working interpretation for `reconstructed_text`
- `core/bootstrap_context.py` – injects grounding note when present

## Adding New Rules

Edit `_PHRASE_EXPANSIONS` and `_DOMAIN_NORMALIZE` in `context_understanding.py`. Keep expansions grounded: only clarify what the user said, never invent.
