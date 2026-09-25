import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { AGUIEvent } from "../types";

export function loadFixture(name: string): AGUIEvent[] {
  const path = join(__dirname, `${name}.txt`);
  return readFileSync(path, "utf-8")
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => JSON.parse(line.slice("data: ".length)) as AGUIEvent);
}
