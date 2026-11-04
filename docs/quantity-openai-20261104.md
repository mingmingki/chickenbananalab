# Quantity: AI calls move from Gemini to OpenAI — 2026-11-04

Builds on the all-pages increment (PR #10, c062584). Branch `feat/quantity-openai-20261104`.

Every AI call of the quantity tool now goes to OpenAI. The other AI features of the site (posts, images, keywords) still use Gemini and were not touched.

## What changed
- **New layer:** `core/quantity_ai.py` holds the only OpenAI call of the tool. `client.generate(contents=..., system=..., max_output_tokens=..., effort=..., timeout=...)` takes text and `image_part(...)` items in order and returns an `AiResponse`.
- **Call sites:** the 12 calls in `core/quantity_views.py` use it. Their prompts, batches and JSON parsing stay as they were, apart from the one prompt below.
- **Model:** `OPENAI_QUANTITY_MODEL`, default `gpt-6.1-sol`. The model is part of the saved step key, so changing it asks the AI steps again.
- **Key:** `OPENAI_QUANTITY_API_KEY` in `.env`. Without it the tool reports the missing key, as it did for Gemini.
- **Why its own key name:** `OPENAI_API_KEY` is also read by other features of the site. They stay as they are while that name is empty, and their cost stays apart from this tool's. Review those features' access before ever setting that name.
- **Usage:** calls are logged to the existing usage table (`GeminiUsageLog`) with the model name, so the admin usage page shows them next to the Gemini features. No prompt, response or drawing is stored, and `store=False` keeps them out of OpenAI's response storage.

## Differences handled in the layer
| OpenAI behaviour | What the layer does |
|---|---|
| Reasoning models reject `temperature` | Never sends it. Answers are no longer pinned to temperature 0. |
| Reasoning tokens count toward `max_output_tokens` | Adds 8,192 (`low`) or 16,384 (`medium`) to the limit each call asks for, up to the model's 128,000. |
| JSON mode returns HTTP 400 unless the input messages contain "json". The instructions do not count, as a real call showed. | Puts a short sentence first in the input when no text item has the word. |
| A cut-off answer has status `incomplete` | Maps it to `MAX_TOKENS` and keeps the partial text, so the existing repair and page retry still work. |
| A timeout's message is "Request timed out." | Raises the built-in `TimeoutError`, because callers look for that type or the word "timeout". |

Effort maps from the old settings. Calls that had a thinking budget (locators, classifiers, transcription) use `low`. The others use `medium`.

Images use `detail: auto`, which every model accepts. The image guide's sizing table does not list `gpt-6.1-sol`. Pages are sent at 1536 px on the long edge or less, about 1,960 tokens for an A3 page at 1.2 tokens per 32 px patch.

## Other changes
- **Prompt:** `GENERAL_NOTES_LOCATOR_PROMPT` named no top-level key. JSON mode must return an object, so a key chosen by the model would have left the page list empty without an error. It now asks for `{"pages": [...]}`.
- **Time limits:** a request may take longer than it did on the fast Gemini model, and these limits decide how many pages get read.

  | Limit | Before | Now |
  |---|---|---|
  | Overview page search, whole search | 120 s | 420 s |
  | One overview vision request (and its page render) | 60 s | 180 s |
  | General notes step | 120 s | 300 s |

  All of these steps run in background jobs. The values are estimates until a real run measures them.
- **Request timeout and retries:** `QTY_AI_TIMEOUT_SEC` (default 900 s) with 3 retries. A retried request can be billed again.
- **Names and text:** `Gemini` in the tool's code, messages and screens became `AI`. Saved notes from old jobs are not migrated; job state expires after 7 days.

## Cost estimate for one full run of the 300-page test set
Prices for `gpt-6.1-sol`: input $2.00, cached input $0.10, output $10.00 per 1M tokens.

| Part | Tokens | Cost |
|---|---|---|
| Page images, including re-read pages | about 0.7M | about $1.4 |
| Prompts and CAD summaries, mostly a repeated prefix | about 1.1M | $0.1–2.2 |
| Output and reasoning | 0.2–0.6M | $2–6 |
| **Total** | | **about $4–10** |

This is an estimate, not a measurement. The earlier estimate for the Gemini model was $1.2–3.8. `OPENAI_QUANTITY_MODEL=gpt-6.1-terra` costs half per token and needs no code change.

## Tests
`core/test_quantity_ai.py` runs the real OpenAI SDK against a fake transport, so it checks the actual request body and response parsing without a network call or a key. It also runs member extraction and the overview classifier through the SDK.

## Small real run (2026-11-04)
Two calls with `gpt-6.1-sol` through the real code path, images only, no CAD text.

| Call | Images | Time | Tokens in / out | Cost |
|---|---|---|---|---|
| Member extraction: one beam schedule and one dense wall schedule | 2 | 99 s | 10,999 / 13,966 | $0.16 |
| Overview page classifier: first 10 structural pages | 10 | 13 s | 34,642 / 692 | $0.08 |

The answers were compared cell by cell with the CAD schedule parser, which reads the DWG without AI:

| Table | Marks found (CAD / AI) | Cells compared | Different |
|---|---|---|---|
| Walls | 42 / 42 | 252 | 0 |
| Beams | 17 / 17 | 80 | 0 |

The classifier sorted all 10 pages, with no page missing and no cut-off.

What this shows and does not show:
- The first real call failed with HTTP 400, because the instructions do not count for the "json" rule. Tests with a fake transport had passed. That is why the rule is now applied to the input.
- `detail: auto` kept the images at full size: about 3,400 tokens for a 2000 px page, which is the original-size count.
- Output runs at about 140 tokens per second, so a batch of 8 dense schedule pages would take about 6.5 minutes, inside the 900 s limit.
- Two schedule pages are a small sample. Plan pages, member counts, architectural quantities and elevations have not been measured against anything, and there is no comparison with Gemini.

**Not verified:** a full run, its real cost, and accuracy against a manual takeoff.
