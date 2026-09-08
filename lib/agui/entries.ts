export type Artifact =
  | {
      component: "ui-table";
      id: string;
      title: string;
      columns: string[];
      rows: string[][];
    }
  | {
      component: "briefing";
      id: string;
      agent: string;
      question: string;
      documents: string[];
      summary: string;
    }
  | { component: "unknown"; id: string; raw: unknown };

export type Entry =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string }
  | { kind: "reasoning"; id: string; text: string; done: boolean }
  | { kind: "tool"; id: string; name: string; args: string; done: boolean }
  | {
      kind: "subagent";
      id: string;
      name: string;
      description: string;
      status: "running" | "done" | "failed";
      error?: string;
    }
  | { kind: "artifact"; id: string; artifact: Artifact };

export function parseReasoningDelta(encryptedValue: string): string {
  let fragments: unknown;
  try {
    fragments = JSON.parse(encryptedValue);
  } catch {
    throw new Error(
      `unreadable reasoning payload: ${encryptedValue.slice(0, 80)}`,
    );
  }
  if (!Array.isArray(fragments)) {
    throw new Error("reasoning payload: expected a list of fragments");
  }
  return fragments
    .filter(
      (f): f is { type: string; text: string } =>
        typeof f === "object" &&
        f !== null &&
        (f as { type?: unknown }).type === "reasoning.text" &&
        typeof (f as { text?: unknown }).text === "string",
    )
    .map((f) => f.text)
    .join("");
}

export function parseArtifact(content: unknown): Artifact | null {
  if (typeof content !== "string") return null;

  let payload: unknown;
  try {
    payload = JSON.parse(content);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null) return null;

  const shape = payload as Record<string, unknown>;
  if (typeof shape.component !== "string") return null;
  if (shape.component === "plan") return null;

  const id = typeof shape.id === "string" ? shape.id : "art_?";

  if (
    shape.component === "ui-table" &&
    typeof shape.title === "string" &&
    isStringArray(shape.columns) &&
    Array.isArray(shape.rows) &&
    shape.rows.every(isStringArray)
  ) {
    return {
      component: "ui-table",
      id,
      title: shape.title,
      columns: shape.columns,
      rows: shape.rows,
    };
  }

  if (
    shape.component === "briefing" &&
    typeof shape.summary === "string" &&
    isStringArray(shape.documents)
  ) {
    return {
      component: "briefing",
      id,
      agent: typeof shape.agent === "string" ? shape.agent : "subagent",
      question: typeof shape.question === "string" ? shape.question : "",
      documents: shape.documents,
      summary: shape.summary,
    };
  }

  return { component: "unknown", id, raw: payload };
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
