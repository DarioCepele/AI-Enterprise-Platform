import { describe, expect, it } from "vitest";
import { contractSample, loadContract } from "@/lib/contracts";
import { INSTANCE_LABEL, STEP_LABEL, waitingStep, type Instance } from "./types";

describe("contracts with the process service", () => {
  it("reads every field of an instance the contract declares", () => {
    const sample = contractSample<Record<string, unknown>>("process/instance");
    const instance = sample as unknown as Instance;

    // Field by field: a rename on the other side has to fail here, not show up
    // as an empty panel in front of somebody.
    expect(instance.id).toBeTypeOf("string");
    expect(instance.process_id).toBeTypeOf("string");
    expect(instance.process_version).toBeTypeOf("number");
    expect(instance.status).toBeTypeOf("string");
    expect(instance.steps.length).toBeGreaterThan(0);

    const [step] = instance.steps;
    for (const field of [
      "step_id",
      "status",
      "owner",
      "task_id",
      "question",
      "output",
      "note",
      "started_at",
      "ended_at",
    ]) {
      expect(Object.keys(step), field).toContain(field);
    }
  });

  it("has a word for the state the contract's sample is in", () => {
    const instance = contractSample<Instance>("process/instance");

    expect(INSTANCE_LABEL[instance.status]).toBeDefined();
    for (const step of instance.steps) {
      expect(STEP_LABEL[step.status], step.step_id).toBeDefined();
    }
  });

  it("finds the step somebody has to act on", () => {
    const instance = contractSample<Instance>("process/instance");

    expect(waitingStep(instance)?.step_id).toBe("approval");
  });

  it("names this repository among the consumers", () => {
    // If this fails the contract moved, and its failure message would send
    // whoever broke it to the wrong repository.
    expect(loadContract("process/instance").consumed_by).toContain("demo-frontend");
  });
});
