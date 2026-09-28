import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { configFromEnvironment } from "./runtime-config";
import environment from "./runtime-env.json";

// runtime-env.json documents the page's configuration: the infra README's
// table is generated from it. These tests keep it honest -- every variable
// it lists is read, with the default it states, and none is missing.

function valueAt(config: unknown, field: string): string {
  const value = field
    .split(".")
    .reduce<unknown>((node, key) => (node as Record<string, unknown>)[key], config);
  return Array.isArray(value) ? value.join(",") : String(value);
}

function probeFor(variable: string): string {
  if (variable === "API_CREDENTIALS") return "include";
  if (variable.endsWith("_URL")) return "http://probe.example/path";
  return "probe value";
}

describe("runtime-env.json", () => {
  it.each(environment)("$variable defaults to what the table says", ({ field, default: stated }) => {
    expect(valueAt(configFromEnvironment({}), field)).toBe(stated);
  });

  it.each(environment)("$variable is actually read", ({ variable, field, default: stated }) => {
    const configured = configFromEnvironment({ [variable]: probeFor(variable) });
    expect(valueAt(configured, field)).not.toBe(stated);
  });

  it("lists every variable the page reads", () => {
    const source = readFileSync(join(__dirname, "runtime-config.ts"), "utf8");
    const read = new Set(
      [...source.matchAll(/env\.([A-Z][A-Z0-9_]*)/g)]
        .map((match) => match[1])
        .filter((name) => !name.startsWith("NEXT_PUBLIC_")),
    );

    expect(new Set(environment.map((entry) => entry.variable))).toEqual(read);
  });
});
