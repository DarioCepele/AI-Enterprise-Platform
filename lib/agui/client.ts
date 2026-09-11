import { runtimeConfig } from "../runtime-config";
import type { AGUIEvent, RunInput } from "./types";

/** Read per call: the endpoint arrives with the page, not with the bundle. */
export function aguiUrl(): string {
  return runtimeConfig().aguiUrl;
}

/** Derived from the AG-UI endpoint, which is read at runtime (same pattern as logsUrl). */
export function uploadsUrl(): string {
  return aguiUrl().replace(/\/agui$/, "/uploads");
}

/**
 * Uploads a file as a raw request body (not multipart/form-data — a known
 * deviation of this endpoint) and returns the URL the server stored it at.
 */
export async function uploadVideo(file: Blob, signal?: AbortSignal): Promise<string> {
  const response = await fetch(uploadsUrl(), {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
    signal,
  });

  if (!response.ok) {
    throw new Error(`/uploads ha risposto ${response.status}`);
  }

  const data = (await response.json().catch(() => null)) as { url?: unknown } | null;
  if (!data || typeof data.url !== "string" || data.url === "") {
    throw new Error("/uploads non ha restituito un url valido");
  }
  return data.url;
}

export async function runAgent(
  input: RunInput,
  onEvent: (event: AGUIEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(aguiUrl(), {
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
