import { apiCredentials, runtimeConfig } from "../runtime-config";
import type { Instance, ProcessSummary } from "./types";

/** Where the process service answers, read at runtime like everything else. */
export function processUrl(): string {
  return runtimeConfig().processUrl;
}

/** Empty means "this deployment has no process service", not "it is broken". */
export function processesConfigured(): boolean {
  return processUrl() !== "";
}

async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${processUrl()}${path}`, {
    signal,
    cache: "no-store",
    credentials: apiCredentials(),
  });
  if (!response.ok) {
    throw new Error(`${path} answered ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchInstances(
  status: string,
  signal?: AbortSignal,
): Promise<Instance[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const page = await read<{ instances: Instance[] }>(`/instances${query}`, signal);
  return page.instances;
}

export async function fetchInstance(id: string, signal?: AbortSignal): Promise<Instance> {
  return read<Instance>(`/instances/${id}`, signal);
}

export async function fetchProcesses(signal?: AbortSignal): Promise<ProcessSummary[]> {
  const page = await read<{ processes: ProcessSummary[] }>("/processes", signal);
  return page.processes;
}

async function send(path: string, body: unknown): Promise<void> {
  const response = await fetch(`${processUrl()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
    credentials: apiCredentials(),
  });
  if (!response.ok) {
    // The service says why in `detail`: a step that is no longer waiting, or
    // somebody who cannot decide this one. Passing it on beats "400".
    const said = await response.json().catch(() => null);
    const detail = said && typeof said.detail === "string" ? said.detail : response.statusText;
    throw new Error(detail);
  }
}

export async function answerStep(id: string, stepId: string, text: string): Promise<void> {
  await send(`/instances/${id}/steps/${stepId}/answer`, { text });
}

export async function decideStep(
  id: string,
  stepId: string,
  by: string,
  decision: "approved" | "rejected",
  note?: string,
): Promise<void> {
  await send(`/instances/${id}/steps/${stepId}/decision`, { by, decision, note: note ?? null });
}
