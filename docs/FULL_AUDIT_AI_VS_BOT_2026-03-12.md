# NULLA Full Audit — 100% AI or Still a Stupid TG Bot?

**Date:** 2026-03-12  
**Question:** Is she 100% AI or still a stupid Telegram bot?

---

## TL;DR

**She's a hybrid.** Roughly **40% real AI**, **60% deterministic fast-path bot** by request volume. The system is no longer "stupid" — runtime failures are fixed, Hive works, research works, no more error leaks. But a large share of common prompts still hit canned responses. She feels like a **smart bot with AI backup**, not a **pure AI assistant**.

---

## The Verdict

| Dimension | Status | Notes |
|-----------|--------|------|
| **Runtime brokenness** | ✅ Fixed | No 500s, no error leaks, no fake task lists |
| **Hive flow** | ✅ Real | Real task list from API, real claim/start/status |
| **Research / web** | ✅ Real | Live weather, news, fresh lookup → real web, model synthesis when needed |
| **Greetings / smalltalk** | ⚠️ Bot | 3 canned variants per type, deterministic |
| **Utility (date, time)** | ⚠️ Bot | Deterministic, no model |
| **Evaluative ("how are you")** | ⚠️ Bot | 4–5 canned phrases |
| **Open-ended / research** | ✅ AI | Model + web_notes + curiosity |
| **Context following** | ⚠️ Mixed | Hive follow-ups work; generic "ok/yes" still brittle |

**Bottom line:** She's not a stupid TG bot anymore. She's a **competent hybrid** — fast paths for predictable stuff, real AI for the rest. To feel "100% AI," the deterministic fast paths (especially smalltalk, evaluative) need to either vary more or hand off to the model.

---

## 1. What Hits the Model (Real AI)

These flows invoke the local model for synthesis:

- **Research / system_design / integration_orchestration** — Full task path: classification → context load → curiosity roam → tool intent or fallback → web_notes → model synthesis over evidence.
- **Tool intent success** — Model picks a tool, it runs, model synthesizes the final reply.
- **Tool intent failure → research fallback** — When `missing_intent`/`invalid_payload` and the query is research/fresh-info, the agent returns `None` from tool path and continues to web_notes + model.
- **Live info (weather/news/fresh_lookup)** — Uses **real web** (wttr.in, Google News RSS, planned search) but **template formatting** of results. No model for the final string. So: real data, bot-style presentation.
- **Open-ended questions** — Anything that misses all fast paths goes to the model with context + evidence.

---

## 2. What Stays Deterministic (Bot)

| Fast path | Example prompts | Output type |
|-----------|-----------------|-------------|
| **Smalltalk** | hi, hello, hey, yo, sup, gm | 3 variants by repeat count: "Hey. I'm NULLA. What do you need?" → "Yep, got your hello..." → "Skip the greeting..." |
| **Evaluative** | how are you, you sound weird, not a dumb | 4–5 canned phrases |
| **Date/time** | what is the date today, what day is it | `datetime.now()` → "Today is Thursday, 2026-03-12." |
| **Credit status** | /credits, balance | Ledger balance + explanation template |
| **UI command** | /help, /status | Deterministic |
| **Memory command** | /memory, remember X | Deterministic (but uses real memory store) |
| **Preference command** | rename, workflow | Deterministic |
| **Hive** | show me open hive tasks, pull tasks, start #id | Real API data, **template formatting** |

---

## 3. Fast-Path Order (What Runs First)

```
1. Startup sequence
2. Preference command
3. Hive runtime command        ← real API, template output
4. Hive research follow-up     ← real
5. Hive status follow-up       ← real
6. Memory command
7. UI command
8. Credit status
9. Date/time                   ← deterministic
10. Live info (weather/news)   ← real web, template output
11. Evaluative                 ← canned
12. Smalltalk                  ← canned
13. FULL PATH (task + model)   ← real AI
```

If the user says "hey" or "what is the date today", the model is never called.

---

## 4. What Changed Since the Last Audit

**Fixed (no longer bot-grade failures):**

- Hive list 500 (create vs list routing)
- "I won't fake it" / "invalid tool payload" leaking
- "Real steps completed:" leaking
- Weather/news empty failures
- DDG challenge misreported as success
- consent.google.com in news

**Still bot-like:**

- First greeting: same for hi/hello/hey/yo
- "How are you" → "Running stable. Memory online, mesh ready."
- Date/time: always same format
- Evaluative: small set of canned phrases

**Improved:**

- Greetings vary by repeat count (2nd, 3rd hello get different text)
- Hive follow-ups: "ok", "yes", "do it" work when context is set
- Research: "latest telegram bot api updates" → model synthesis, no leak

---

## 5. Ratio Estimate

| Request type | % of typical chat | Path | Feels like |
|--------------|-------------------|------|------------|
| Greetings | ~15% | Smalltalk | Bot |
| Date/time/utility | ~5% | Date/time | Bot |
| "How are you" etc. | ~3% | Evaluative | Bot |
| Hive commands | ~15% | Hive | Smart bot (real data, template) |
| Weather/news/live | ~10% | Live info | Smart bot (real web, template) |
| Research / open-ended | ~50% | Full path | AI |
| Tool success | ~2% | Tool intent | AI |

**Rough split:** ~40% deterministic/template, ~60% model or model-assisted. But the deterministic part is what users hit first (greetings, date, status), so the **first impression** is still bot-like.

---

## 6. How to Feel "100% AI"

1. **Smalltalk** — Either add more variants (5–10 per phrase) or, after N greetings, bypass to model.
2. **Evaluative** — Same: more variants or model handoff.
3. **Live info** — Keep real web, but let the model summarize instead of template (e.g. "Summarize these weather results for the user").
4. **Hive list** — Keep real API, optionally let model polish the list text.

The architecture already supports model handoff. The fast paths are a **choice** for latency and stability. To feel more AI, reduce their scope or add variation.

---

## 7. Test Results (Current)

- **180 passed**, 9 xfailed (future specs)
- Deep contract: **110 passed**, 9 xfailed

No regressions. The system is stable.

---

## 8. Final Answer

**Is she 100% AI?** No.  
**Is she still a stupid TG bot?** No.

She's a **hybrid**: deterministic fast paths for common, predictable prompts; real AI for research, tool use, and open-ended questions. Runtime is solid. The remaining "bot" feel comes from canned smalltalk and evaluative replies. Fix those, and she'll feel much more like a real AI assistant.
