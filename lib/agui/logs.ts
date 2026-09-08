export interface LogEntry {
  seq: number;
  ts: string;
  level: string;
  source: string;
  message: string;
}

export interface LogPage {
  entries: LogEntry[];
  cursor: number;
  dropped: number;
}

const AGUI_URL = process.env.NEXT_PUBLIC_AGUI_URL ?? "http://127.0.0.1:8000/agui";

/**
 * I log stanno su un endpoint proprio, non sullo stream AG-UI: gli eventi
 * CUSTOM del protocollo sono riservati al framework e il codice applicativo
 * non puo' emetterne. L'URL si deriva da quello di AG-UI invece di essere una
 * seconda variabile d'ambiente, che potrebbe divergere.
 */
export const LOGS_URL = AGUI_URL.replace(/\/agui$/, "/logs");

export async function fetchLogs(cursor: number, signal?: AbortSignal): Promise<LogPage> {
  const response = await fetch(`${LOGS_URL}?cursor=${cursor}`, { signal, cache: "no-store" });
  if (!response.ok) {
    throw new Error(`/logs ha risposto ${response.status}`);
  }
  return (await response.json()) as LogPage;
}
