// Claude from TypeScript with the Anthropic SDK. Streams token deltas over /runs/stream.
import Anthropic from "@anthropic-ai/sdk";

const MODEL = process.env.MODEL ?? "claude-sonnet-5-5";

export async function* agent(payload: { message?: string }) {
  if (!process.env.ANTHROPIC_API_KEY) throw new Error("ANTHROPIC_API_KEY is not set — add it under Variables and redeploy");
  const client = new Anthropic();
  const stream = client.messages.stream({
    model: MODEL,
    max_tokens: 1024,
    messages: [{ role: "user", content: payload.message ?? "Say hello." }],
  });
  for await (const ev of stream) {
    if (ev.type === "content_block_delta" && ev.delta.type === "text_delta") yield ev.delta.text;
  }
}
