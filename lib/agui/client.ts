import type { AGUIEvent, RunInput } from "./types";

const ENDPOINT = process.env.NEXT_PUBLIC_AGUI_URL ?? "http://127.0.0.1:8000/agui";

export async function runAgent(
  input: RunInput,
  onEvent: (event: AGUIEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(input),
    signal,
  });

  if (!response.ok || !response.body) {
    throw new Error(`AG-UI ha risposto ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let line = "";
  let data: string[] = [];
  let skipLF = false;
  let completed = false;

  function processLine() {
    if (line === "") {
      if (data.length > 0) {
        const payload = data.join("\n");
        data = [];
        onEvent(JSON.parse(payload) as AGUIEvent);
      }
      return;
    }

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    if (field !== "data") return;
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    data.push(value);
  }

  function consume(text: string) {
    for (const char of text) {
      if (skipLF) {
        skipLF = false;
        if (char === "\n") continue;
      }
      if (char === "\r" || char === "\n") {
        processLine();
        line = "";
        skipLF = char === "\r";
      } else {
        line += char;
      }
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        consume(decoder.decode());
        completed = true;
        return;
      }
      consume(decoder.decode(value, { stream: true }));
    }
  } finally {
    if (!completed) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
