import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { AGUIEvent } from "../types";

/**
 * Legge uno stream SSE registrato e restituisce i suoi eventi.
 *
 * Solo per i test: usa node:fs, quindi non deve mai finire in un componente.
 * Le fixture hanno un evento per riga data:, mentre il parser di rete in
 * lib/agui/client.ts gestisce anche i casi di frammentazione dello stream.
 */
export function loadFixture(name: string): AGUIEvent[] {
  const path = join(__dirname, `${name}.txt`);
  return readFileSync(path, "utf-8")
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice("data: ".length)) as AGUIEvent);
}
