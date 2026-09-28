import { readFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

const DEFAULT_LOCATION = resolve(process.cwd(), "..", "demo-infra", "contracts");
// The name the Python services read too; the second is the older one.
const LOCATION_VARIABLES = ["CONTRACTS_DIR", "AGUI_LAB_CONTRACTS"] as const;

export interface Contract {
  contract: string;
  version: number;
  produced_by: string[];
  consumed_by: string[];
  description: string;
  sample: unknown;
}

function contractsDir(): string {
  const configured = LOCATION_VARIABLES.map((name) => process.env[name]).find(Boolean);
  const directory = configured ?? DEFAULT_LOCATION;
  if (!existsSync(directory)) {
    throw new Error(
      `shared contracts not found in ${directory}. In the repository they sit in ` +
        `demo-infra/contracts; elsewhere, set CONTRACTS_DIR to that directory. These tests do ` +
        `not skip themselves: a contract test that goes quiet when the other side is missing ` +
        `is the silence the contracts exist to remove.`,
    );
  }
  return directory;
}

export function loadContract(name: string): Contract {
  return JSON.parse(readFileSync(join(contractsDir(), `${name}.json`), "utf-8"));
}

export function contractSample<T = Record<string, unknown>>(name: string): T {
  return loadContract(name).sample as T;
}

/** Compare the keys, which are the contract; values only make the sample readable. */
export function assertShape(name: string, payload: unknown): void {
  const contract = loadContract(name);
  compare(contract, contract.sample, payload, "");
}

function compare(contract: Contract, expected: unknown, actual: unknown, at: string): void {
  const where = at || "the payload";

  if (Array.isArray(expected)) {
    if (!Array.isArray(actual)) throw broken(contract, `${where} is not a list`);
    if (expected.length > 0 && actual.length > 0) {
      compare(contract, expected[0], actual[0], `${where}[0]`);
    }
    return;
  }

  if (expected !== null && typeof expected === "object") {
    if (actual === null || typeof actual !== "object" || Array.isArray(actual)) {
      throw broken(contract, `${where} is not an object`);
    }
    const expectedKeys = Object.keys(expected as Record<string, unknown>);
    const actualKeys = Object.keys(actual as Record<string, unknown>);
    const missing = expectedKeys.filter((key) => !actualKeys.includes(key));
    const unexpected = actualKeys.filter((key) => !expectedKeys.includes(key));
    if (missing.length > 0 || unexpected.length > 0) {
      throw broken(
        contract,
        `${where}: missing [${missing.join(", ")}], unexpected [${unexpected.join(", ")}]`,
      );
    }
    for (const key of expectedKeys) {
      compare(
        contract,
        (expected as Record<string, unknown>)[key],
        (actual as Record<string, unknown>)[key],
        at ? `${where}.${key}` : key,
      );
    }
  }
}

function broken(contract: Contract, detail: string): Error {
  const consumers = contract.consumed_by.join(", ") || "nobody declared";
  return new Error(
    `contract '${contract.contract}' v${contract.version} broken: ${detail}.\n` +
      `Consumed by: ${consumers}. Update the sample in demo-infra/contracts, raise its ` +
      `version, and fix every side in the same run.`,
  );
}
