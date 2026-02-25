# LLM Integration Guide

This document covers how Nemo uses OpenAI models and the Responses API. It serves
as the reference for all LLM-calling code in the project.

## Models

We use three OpenAI models, chosen to cover the cost/speed/reasoning spectrum:

| Model | Use when… | Relative cost | Latency | Reasoning |
|---|---|---|---|---|
| **gpt-5.2** | Deep analysis, complex multi-step reasoning, ambiguous data interpretation | $$$ | Slow | Strongest |
| **gpt-5-mini** | Standard summarization, canonicalization, structured extraction | $$ | Medium | Good |
| **gpt-5-nano** | High-volume, low-stakes tasks — tagging, simple classification, formatting | $ | Fast | Basic |

### Selection guidelines

- Default to **gpt-5-mini** for most Nemo tasks (summarize, canonicalize, reflect).
- Use **gpt-5-nano** when the prompt is templated and the output is constrained
  (e.g. yes/no classification, enum extraction, tag assignment).
- Reserve **gpt-5.2** for tasks that need world knowledge or multi-hop reasoning
  (e.g. interpreting an anomaly across multiple tables, generating an exploration
  strategy, reading a chart image).

---

## The Responses API

All calls use the **Responses API** (`client.responses.create`), not the legacy
Chat Completions API. The Responses API is OpenAI's recommended path for all new
projects and is required to get the best performance from reasoning models.

### Setup

```python
from openai import OpenAI

client = OpenAI()  # reads OPENAI_API_KEY from env
```

In Nemo, the API key comes from `config.openai_api_key` (which falls back to the
`OPENAI_API_KEY` environment variable).

### Basic text generation

```python
response = client.responses.create(
    model="gpt-5-mini",
    input="Summarize the following query result in one sentence: ...",
)

print(response.output_text)
```

`response.output_text` is a convenience property on the SDK that aggregates all
text outputs. The raw `response.output` is a list that can contain multiple items
(text messages, tool calls, reasoning tokens, etc.) — never assume the text is at
`output[0].content[0].text`.

### Using instructions (developer messages)

The `instructions` parameter sets high-priority system-level context. It takes
precedence over anything in the `input` parameter:

```python
response = client.responses.create(
    model="gpt-5-mini",
    instructions="You are a data analyst. Respond with structured JSON.",
    input="What pattern exists in revenue across regions?",
)
```

This is equivalent to passing a `developer` role message in the `input` array:

```python
response = client.responses.create(
    model="gpt-5-mini",
    input=[
        {"role": "developer", "content": "You are a data analyst. Respond with structured JSON."},
        {"role": "user", "content": "What pattern exists in revenue across regions?"},
    ],
)
```

### Message roles

| Role | Purpose | Priority |
|---|---|---|
| `developer` | App-level system instructions (the "function definition") | Highest |
| `user` | End-user input (the "arguments") | Normal |
| `assistant` | Model-generated messages from prior turns | — |

### Reasoning effort

For models that support reasoning (gpt-5.2, gpt-5-mini), you can control how much
thinking the model does:

```python
response = client.responses.create(
    model="gpt-5-mini",
    reasoning={"effort": "low"},   # "low", "medium", or "high"
    input="Is this trend statistically significant?",
)
```

Use `"low"` for straightforward tasks to save tokens and latency.
Use `"high"` when accuracy matters more than speed.

---

## Structured Output

Structured Outputs guarantee the model's response conforms to a JSON schema. This
eliminates the need to parse free-text or retry on malformed JSON.

### With Pydantic (recommended)

The Python SDK has native Pydantic support through `response.output_parsed`:

```python
from pydantic import BaseModel
from openai import OpenAI

client = OpenAI()


class ClaimExtraction(BaseModel):
    metric: str
    direction: str        # "higher" | "lower" | "no_change" | "different"
    population: str
    segment: str | None
    time_range: str | None
    magnitude: float | None
    comparison_base: str | None


response = client.responses.parse(
    model="gpt-5-mini",
    instructions="Extract structured fields from the claim.",
    input="Revenue increased 23% in Q4 for the EMEA segment compared to Q3.",
    text_format=ClaimExtraction,
)

claim = response.output_parsed
print(claim.metric)      # "revenue"
print(claim.direction)   # "higher"
print(claim.magnitude)   # 23.0
```

The SDK's `responses.parse()` method validates the response against the Pydantic
model and gives you a typed object. If the model refuses for safety reasons, the
response will have a `refusal` field instead.

### With raw JSON schema

If you prefer to specify the schema directly:

```python
response = client.responses.create(
    model="gpt-5-mini",
    instructions="Extract structured fields from the claim.",
    input="Revenue increased 23% in Q4 for the EMEA segment compared to Q3.",
    text={
        "format": {
            "type": "json_schema",
            "strict": True,
            "name": "claim_extraction",
            "schema": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "direction": {
                        "type": "string",
                        "enum": ["higher", "lower", "no_change", "different"],
                    },
                    "population": {"type": "string"},
                    "segment": {"type": ["string", "null"]},
                    "time_range": {"type": ["string", "null"]},
                    "magnitude": {"type": ["number", "null"]},
                    "comparison_base": {"type": ["string", "null"]},
                },
                "required": [
                    "metric", "direction", "population", "segment",
                    "time_range", "magnitude", "comparison_base",
                ],
                "additionalProperties": False,
            },
        }
    },
)

import json
claim = json.loads(response.output_text)
```

### Handling refusals

When the model refuses a request for safety reasons, the output won't match your
schema. Check for the `refusal` field:

```python
response = client.responses.parse(
    model="gpt-5-mini",
    text_format=ClaimExtraction,
    input="...",
)

if response.refusal:
    print(f"Model refused: {response.refusal}")
else:
    print(response.output_parsed)
```

### Tips

- **Always use Pydantic models** when possible — they keep your schema and Python
  types in sync and avoid drift.
- **Set `strict: True`** in raw JSON schema mode to get guaranteed adherence.
- **Handle hallucination on bad input** — if user input is unrelated to the schema,
  the model will still try to fill every field. Add instructions like
  "If the input doesn't contain enough information, set fields to null."

---

## Vision — Reading Images and Charts

Models can analyze images (vision). This is how Nemo can read chart screenshots,
exported visualizations, or any image a user provides.

### Supported image inputs

There are three ways to pass an image:

1. **URL** — a publicly accessible image URL
2. **Base64 data URL** — inline the image bytes
3. **File ID** — upload via the Files API first, then reference by ID

### Analyzing a chart image via URL

```python
response = client.responses.create(
    model="gpt-5.2",
    input=[{
        "role": "user",
        "content": [
            {
                "type": "input_text",
                "text": (
                    "This chart shows monthly revenue by region. "
                    "Identify the top-performing region and any notable trends."
                ),
            },
            {
                "type": "input_image",
                "image_url": "https://example.com/charts/revenue_by_region.png",
            },
        ],
    }],
)

print(response.output_text)
```

### Analyzing a local chart image (base64)

```python
import base64
from pathlib import Path

image_bytes = Path("charts/revenue_q4.png").read_bytes()
b64 = base64.b64encode(image_bytes).decode()

response = client.responses.create(
    model="gpt-5.2",
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Describe the key takeaways from this chart."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
        ],
    }],
)
```

### Using the Files API

```python
with open("charts/revenue_q4.png", "rb") as f:
    uploaded = client.files.create(file=f, purpose="vision")

response = client.responses.create(
    model="gpt-5.2",
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "What does this chart show?"},
            {"type": "input_image", "file_id": uploaded.id},
        ],
    }],
)
```

### Image detail level

Control how many tokens the model spends on the image with the `detail` parameter:

```python
{
    "type": "input_image",
    "image_url": "https://example.com/chart.png",
    "detail": "high",   # "low", "high", or "auto" (default)
}
```

- **`low`** — 85 tokens, 512×512px. Good for "what color is the dominant bar?" type questions.
- **`high`** — Full resolution analysis. Use for charts with small text, dense data, or precise readings.
- **`auto`** — Model decides (default).

### Combining vision + structured output

This is the power move for Nemo: read a chart and extract structured data in one call.

```python
from pydantic import BaseModel


class ChartInsight(BaseModel):
    chart_type: str          # "bar", "line", "scatter", "pie", etc.
    title: str | None
    x_axis: str | None
    y_axis: str | None
    key_finding: str
    data_points: list[dict]  # extracted values when readable


response = client.responses.parse(
    model="gpt-5.2",
    instructions=(
        "Analyze the chart image. Extract its type, axes, and key findings. "
        "If individual data points are readable, include them in data_points "
        "as {label, value} dicts. If not readable, leave data_points empty."
    ),
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Extract structured insight from this chart."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
        ],
    }],
    text_format=ChartInsight,
)

insight = response.output_parsed
print(insight.chart_type)    # "bar"
print(insight.key_finding)   # "EMEA revenue grew 23% QoQ, outpacing all other regions"
```

### Image input constraints

| Constraint | Limit |
|---|---|
| File types | PNG, JPEG, WEBP, non-animated GIF |
| Max payload | 50 MB per request |
| Max images | 500 per request |

### Known vision limitations

- Approximate counts (don't rely on exact object counting)
- Struggles with dense small text — enlarge if possible
- Spatial reasoning is imprecise (chess positions, precise pixel coords)
- Graphs with color-only distinctions (solid vs dashed lines) can be misread
- Rotated/upside-down text may be misinterpreted
- Non-Latin text recognition is weaker

---

## Token Costs for Images

Image tokens vary by model family. For gpt-5-mini and gpt-5-nano, the image is
divided into 32×32px patches (capped at 1536 patches), then multiplied:

| Model | Multiplier |
|---|---|
| gpt-5-mini | 1.62× |
| gpt-5-nano | 2.46× |

For gpt-5.2, images use base + tile pricing:

| Detail | Base tokens | Per 512px tile |
|---|---|---|
| low | 70 | — |
| high | 70 | 140 |

Use `detail: "low"` when chart readability isn't critical to keep costs down.

---

## Nemo Integration Patterns

### Where LLM calls happen

| Module | Function | Purpose | Recommended model |
|---|---|---|---|
| `nemo/summarize/summarize.py` | `summarize_result()` | Turn query results into structured insights | gpt-5-mini |
| `nemo/summarize/canonicalize.py` | `canonicalize_claim()` | Extract metric/direction/magnitude from claims | gpt-5-nano |
| `nemo/summarize/canonicalize.py` | `canonicalize_hypothesis()` | Extract structured fields from questions | gpt-5-nano |
| (future) | `reflect()` | Periodic strategy review across accumulated insights | gpt-5.2 |
| (future) | chart/image analysis | Read exported visualizations | gpt-5.2 |

### Creating the client

Keep a single client instance per run. The engine should create it at startup
and pass it (or a thin wrapper) to functions that need it:

```python
from openai import OpenAI
from nemo.config import NemoConfig


def make_client(config: NemoConfig) -> OpenAI:
    return OpenAI(api_key=config.openai_api_key)
```

### Error handling

Always wrap LLM calls with retry logic. The OpenAI SDK raises typed exceptions:

```python
from openai import (
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
)

MAX_RETRIES = 3

for attempt in range(MAX_RETRIES):
    try:
        response = client.responses.create(...)
        break
    except (APIConnectionError, APITimeoutError):
        if attempt == MAX_RETRIES - 1:
            raise
        await asyncio.sleep(2 ** attempt)
    except RateLimitError:
        await asyncio.sleep(5 * (attempt + 1))
```

Alternatively, the SDK supports automatic retries via `OpenAI(max_retries=3)`.

### Config

In `nemo.toml`:

```toml
[llm]
model = "gpt-5-mini"           # default model for most calls
# openai_api_key = "sk-..."    # optional; falls back to OPENAI_API_KEY env var
```

The `config.model` field sets the default. Individual call sites can override the
model when a different cost/reasoning tier is needed.

---

## Quick Reference

```python
# --- Text generation ---
response = client.responses.create(
    model="gpt-5-mini",
    instructions="...",
    input="...",
)
text = response.output_text

# --- Structured output (Pydantic) ---
response = client.responses.parse(
    model="gpt-5-mini",
    input="...",
    text_format=MyPydanticModel,
)
obj = response.output_parsed

# --- Vision (URL) ---
response = client.responses.create(
    model="gpt-5.2",
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Describe this chart."},
            {"type": "input_image", "image_url": "https://..."},
        ],
    }],
)

# --- Vision (base64) ---
response = client.responses.create(
    model="gpt-5.2",
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Describe this chart."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64_string}"},
        ],
    }],
)

# --- Vision + Structured Output ---
response = client.responses.parse(
    model="gpt-5.2",
    input=[{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Extract data from this chart."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64_string}"},
        ],
    }],
    text_format=ChartInsight,
)
```
