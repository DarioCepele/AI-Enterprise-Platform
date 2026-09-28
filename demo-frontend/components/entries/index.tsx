import { memo } from "react";
import type { Entry } from "@/lib/agui/entries";
import { UiTable } from "../artifacts/UiTable";
import { ApprovalEntry } from "./ApprovalEntry";
import { AssistantEntry } from "./AssistantEntry";
import { ReasoningEntry } from "./ReasoningEntry";
import { SubagentEntry } from "./SubagentEntry";
import { ToolEntry } from "./ToolEntry";
import { UserEntry } from "./UserEntry";

interface Props {
  entry: Entry;
  onResolve?: (entryId: string, decisions: Record<string, boolean>) => void;
}

export const EntryView = memo(function EntryView({ entry, onResolve }: Props) {
  switch (entry.kind) {
    case "user":
      return <UserEntry text={entry.text} />;
    case "assistant":
      return <AssistantEntry text={entry.text} />;
    case "reasoning":
      return <ReasoningEntry text={entry.text} done={entry.done} />;
    case "tool":
      return <ToolEntry name={entry.name} args={entry.args} done={entry.done} />;
    case "subagent":
      return (
        <SubagentEntry
          name={entry.name}
          description={entry.description}
          status={entry.status}
          error={entry.error}
        />
      );
    case "artifact":
      return <UiTable artifact={entry.artifact} />;
    case "approval":
      return <ApprovalEntry entry={entry} onResolve={onResolve} />;
  }
  const unreachable: never = entry;
  throw new Error(`Unsupported entry variant: ${JSON.stringify(unreachable)}`);
});
