# OpenAI Responses API — Quick Reference

> The Responses API is OpenAI's newer API primitive, replacing Chat Completions for new projects. It's simpler, supports built-in tools natively, and handles multi-turn state for you.

## Key Differences from Chat Completions

| Concept | Chat Completions | Responses API |
|---|---|---|
| Endpoint | `POST /v1/chat/completions` | `POST /v1/responses` |
| Input | `messages` array | `input` (string or array) + `instructions` |
| Output | `choices[0].message.content` | `response.output_text` |
| System prompt | `{ role: "system", content: "..." }` in messages | Top-level `instructions` param |
| Structured output | `response_format` | `text.format` |
| Multi-turn | Manual — you manage the messages array yourself | `previous_response_id` or append `response.output` to input |
| Functions | Wrapped in `{ type: "function", function: { ... } }` | Flat: `{ type: "function", name: "...", parameters: { ... } }` |
| Built-in tools | None — you implement them yourself | `web_search_preview`, `file_search`, `code_interpreter`, etc. |
| Responses stored | Opt-in | Stored by default (`store: true`) |

## Models

| Model | Use Case |
|---|---|
| `gpt-5.2` | Flagship — best quality, most capable |
| `gpt-5-mini` | Smaller/faster reasoning model, good balance of cost and quality |
| `gpt-5-nano` | Smallest/cheapest, best for high-volume low-complexity tasks |

All three are reasoning models and support the `reasoning` parameter.

---

## Parameters

### Core Parameters

```typescript
openai.responses.create({
  model: string,          // Required — "gpt-5.2", "gpt-5-mini", "gpt-5-nano"
  input: string | Item[], // The user input — can be a plain string or array of items
  instructions: string,   // System/developer message (replaces the "system" role message)
  stream: boolean,        // Default: false — enable SSE streaming
  store: boolean,         // Default: true — whether to persist the response server-side
})
```

### Reasoning

Available on all reasoning models. Controls how much "thinking" the model does before answering.

```typescript
reasoning: {
  effort: "low" | "medium" | "high",  // Default: "medium"
  summary: "auto" | "concise" | "detailed" | null,  // Optional — get a summary of the model's reasoning
}
```

- **`effort`**: `"low"` = fast/cheap, `"high"` = thorough/expensive. Default is `"medium"`.
- **`summary`**: Set to `"auto"` to get a text summary of the model's internal reasoning in the response output. Useful for debugging or transparency.

### Output Limits

```typescript
max_output_tokens: number,  // Upper bound on total generated tokens (reasoning + visible output)
```

> **Important:** `max_output_tokens` includes *both* reasoning tokens and visible output tokens. If the limit is hit during reasoning, you get no visible output but still pay for reasoning tokens. OpenAI recommends reserving at least 25,000 tokens when starting out.

### Sampling (non-reasoning models or when not using reasoning)

```typescript
temperature: number,  // 0–2, default 1. Higher = more random.
top_p: number,        // 0–1, default 1. Nucleus sampling. Don't combine with temperature.
```

> **Note:** When using reasoning models with `reasoning.effort`, `temperature` and `top_p` are generally not used — the model's reasoning controls output quality instead.

### Tools

```typescript
tools: [
  { type: "web_search_preview" },       // Built-in web search
  { type: "file_search", ... },          // Search uploaded files / vector stores
  { type: "code_interpreter" },          // Run Python code
  { type: "function", name: "...", ... } // Your custom functions
],
tool_choice: "auto" | "required" | "none" | { type: "function", name: "..." },
parallel_tool_calls: boolean,  // Default: true
max_tool_calls: number,        // Max total built-in tool calls per response
```

### Multi-Turn Conversations

```typescript
previous_response_id: string,  // Chain responses — model sees all prior context
// OR manually pass prior output back:
input: [...previousResponse.output, { role: "user", content: "Follow-up question" }]
```

### Structured Output

Moved from `response_format` to `text.format`:

```typescript
text: {
  format: {
    type: "json_schema",
    name: "my_schema",
    strict: true,
    schema: {
      type: "object",
      properties: { ... },
      required: [...],
      additionalProperties: false
    }
  }
}
```

### Other Notable Parameters

| Parameter | Description |
|---|---|
| `truncation` | `"disabled"` (default) or `"auto"` — auto-truncate if input exceeds context window |
| `metadata` | Up to 16 key-value pairs for tagging/filtering responses |
| `service_tier` | `"auto"`, `"default"`, `"flex"`, or `"priority"` |
| `background` | Run response in background (async). Can be cancelled via `responses.cancel(id)` |
| `prompt_cache_key` | Stable identifier to improve cache hit rates |

---

## Examples

### Basic Text Generation

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-5.2",
  instructions: "You are a helpful assistant.",
  input: "Hello!",
});

console.log(response.output_text);
```

### Streaming

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-5.2",
  instructions: "You are a helpful assistant.",
  input: "Hello!",
  stream: true,
});

for await (const event of response) {
  console.log(event);
}
```

### Reasoning with Effort Control

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-5-mini",
  input: "How much wood would a woodchuck chuck?",
  reasoning: {
    effort: "high"
  }
});

console.log(response.output_text);
```

### Reasoning with Summary

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-5.2",
  input: "What is the capital of France?",
  reasoning: {
    effort: "low",
    summary: "auto",
  },
});

// response.output includes both a reasoning summary item and the message
console.log(response.output);
```

### Web Search

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-5.2",
  tools: [{ type: "web_search_preview" }],
  input: "What was a positive news story from today?",
});

console.log(response.output_text);
```

### Structured Output (JSON Schema)

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-5.2",
  input: "Jane, 54 years old",
  text: {
    format: {
      type: "json_schema",
      name: "person",
      strict: true,
      schema: {
        type: "object",
        properties: {
          name: { type: "string" },
          age: { type: "number" },
        },
        required: ["name", "age"],
        additionalProperties: false,
      },
    },
  },
});

console.log(response.output_text); // '{"name":"Jane","age":54}'
```

### Function Calling

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const response = await openai.responses.create({
  model: "gpt-5.2",
  input: "What's the weather in San Francisco?",
  tools: [
    {
      type: "function",
      name: "get_weather",
      description: "Get current weather for a location",
      parameters: {
        type: "object",
        properties: {
          location: { type: "string" },
        },
        required: ["location"],
        additionalProperties: false,
      },
    },
  ],
});

// Check output for function_call items and handle them
console.log(response.output);
```

### Multi-Turn with `previous_response_id`

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

const res1 = await openai.responses.create({
  model: "gpt-5-nano",
  input: "What is the capital of France?",
  store: true,
});

const res2 = await openai.responses.create({
  model: "gpt-5-nano",
  input: "And what's its population?",
  previous_response_id: res1.id,
  store: true,
});

console.log(res2.output_text);
```

### Multi-Turn — Manual Context

```typescript
import OpenAI from "openai";
const openai = new OpenAI();

let context: any[] = [{ role: "user", content: "What is the capital of France?" }];

const res1 = await openai.responses.create({
  model: "gpt-5-nano",
  input: context,
});

// Append model output + next user message
context = [...context, ...res1.output, { role: "user", content: "And its population?" }];

const res2 = await openai.responses.create({
  model: "gpt-5-nano",
  input: context,
});

console.log(res2.output_text);
```

---

## Response Object — Key Fields

```jsonc
{
  "id": "resp_...",
  "object": "response",
  "status": "completed",       // "completed" | "incomplete" | "cancelled" | "failed"
  "model": "gpt-5.2-...",
  "output": [                  // Array of output items
    {
      "type": "reasoning",     // Only present for reasoning models
      "summary": [...]
    },
    {
      "type": "message",
      "role": "assistant",
      "content": [
        { "type": "output_text", "text": "..." }
      ]
    }
  ],
  "usage": {
    "input_tokens": 36,
    "output_tokens": 87,
    "output_tokens_details": {
      "reasoning_tokens": 0    // How many tokens were spent on reasoning
    },
    "total_tokens": 123
  },
  "incomplete_details": null   // If status is "incomplete", explains why
}
```

Helper: `response.output_text` gives you the text content directly without digging into the output array.

---

## Managing Stored Responses

```typescript
// Retrieve a stored response
const response = await openai.responses.retrieve("resp_...");

// Delete a stored response
await openai.responses.delete("resp_...");

// Cancel a background response
await openai.responses.cancel("resp_...");
```

---

## Tips

- **Responses are stored by default.** Set `store: false` if you don't want persistence.
- **Reasoning models work better with high-level instructions.** Don't over-specify — let the model figure out the approach.
- **`max_output_tokens` covers reasoning + visible output.** Reserve plenty of headroom (25k+ tokens recommended to start).
- **For multi-turn with reasoning models**, pass back reasoning items (or use `previous_response_id`) so the model can continue its chain of thought efficiently.
- **Functions are strict by default** in the Responses API (unlike Chat Completions where they're non-strict by default).
