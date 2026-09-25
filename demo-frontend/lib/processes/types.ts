/**
 * What the process service serves. The shape is the `process/instance`
 * contract in `demo-infra/contracts`, and the test next door checks it.
 *
 * `output` and `input` stay `unknown`: they belong to whoever wrote the step,
 * and an interface that pretended to know their shape would break the first
 * time somebody wrote their own tool.
 */
export interface InstanceStep {
  step_id: string;
  status: string;
  owner: string | null;
  task_id: string | null;
  question: string | null;
  output: Record<string, unknown> | null;
  note: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface Instance {
  id: string;
  scope: string;
  process_id: string;
  process_version: number;
  status: string;
  note: string | null;
  input: Record<string, unknown>;
  context: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
  steps: InstanceStep[];
}

export interface ProcessSummary {
  id: string;
  version: number;
  name: string;
  steps: number;
}

/** The states an instance stops in, and what they mean to a person. */
export const INSTANCE_LABEL: Record<string, string> = {
  pending: "Da avviare",
  running: "In corso",
  waiting: "In attesa",
  waiting_human: "Attende una risposta",
  waiting_approval: "Attende una decisione",
  completed: "Completata",
  failed: "Fallita",
  rejected: "Rifiutata",
  escalated: "Passata avanti",
  compensated: "Disfatta",
};

export const STEP_LABEL: Record<string, string> = {
  ...INSTANCE_LABEL,
  compensation_failed: "Non si e' potuta disfare",
};

/** Whether somebody has to do something about it. */
export function needsSomebody(status: string): boolean {
  return status === "waiting_human" || status === "waiting_approval";
}

export function isOver(status: string): boolean {
  return ["completed", "failed", "rejected", "compensated"].includes(status);
}

export function waitingStep(instance: Instance): InstanceStep | undefined {
  return instance.steps.find((step) => needsSomebody(step.status));
}
