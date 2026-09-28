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

import { apiCredentials } from "../runtime-config";
import { aguiUrl } from "./client";

/** Derived from the AG-UI endpoint, which is read at runtime. */
export function logsUrl(): string {
  return aguiUrl().replace(/\/agui$/, "/logs");
}

/** The cursor is opaque: it comes from the server and goes back untouched. */
export async function fetchLogs(cursor: string, signal?: AbortSignal): Promise<LogPage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  const response = await fetch(`${logsUrl()}${query}`, {
    signal,
    cache: "no-store",
    credentials: apiCredentials(),
  });
  if (!response.ok) {
    throw new Error(`/logs answered ${response.status}`);
  }
  return (await response.json()) as LogPage;
}
