export interface LogEntry {
  seq: number;
  ts: string;
  level: string;
  source: string;
  message: string;
}

export interface LogPage {
  entries: LogEntry[];
  cursor: string;
  dropped: number;
}

const AGUI_URL = process.env.NEXT_PUBLIC_AGUI_URL ?? "http://127.0.0.1:8000/agui";

export const LOGS_URL = AGUI_URL.replace(/\/agui$/, "/logs");

/** The cursor is opaque: it comes from the server and goes back untouched. */
export async function fetchLogs(cursor: string, signal?: AbortSignal): Promise<LogPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  const response = await fetch(`${LOGS_URL}${query}`, { signal, cache: "no-store" });
  if (!response.ok) {
    throw new Error(`/logs answered ${response.status}`);
  }
  return (await response.json()) as LogPage;
}
