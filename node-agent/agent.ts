// A TypeScript agent: export `agent`. Return a value for /runs, or make it an async generator and
// /runs/stream emits each yielded string as a token delta (and /runs returns the joined text). No API key needed.
export async function* agent(payload: { message?: string }) {
  const words = `You said: ${payload.message ?? ""}`.split(" ");
  for (const w of words) {
    await new Promise((r) => setTimeout(r, 120));
    yield w + " ";
  }
}
