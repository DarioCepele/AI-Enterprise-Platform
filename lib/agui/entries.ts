export type Artifact =
  | {
      component: "ui-table";
      id: string;
      title: string;
      columns: string[];
      rows: string[][];
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
      stato: "in corso" | "concluso" | "errore";
      errore?: string;
    }
  | { kind: "artifact"; id: string; artifact: Artifact };

export function parseReasoningDelta(encryptedValue: string): string {
  let fragments: unknown;
  try {
    fragments = JSON.parse(encryptedValue);
  } catch {
    throw new Error(
      `payload di ragionamento non interpretabile: ${encryptedValue.slice(0, 80)}`,
    );
  }
  if (!Array.isArray(fragments)) {
    throw new Error("payload di ragionamento: attesa una lista di frammenti");
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

  return { component: "unknown", id, raw: payload };
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}
