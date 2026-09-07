import type { Entry } from "@/lib/agui/entries";
import { UiTable } from "../artifacts/UiTable";
import { AssistantEntry } from "./AssistantEntry";
import { ReasoningEntry } from "./ReasoningEntry";
import { ToolEntry } from "./ToolEntry";
import { UserEntry } from "./UserEntry";

/**
 * Dispatch su `entry.kind`.
 *
 * Lo switch e' esaustivo: aggiungere una variante a `Entry` senza aggiungerla
 * qui e' un errore di compilazione, non una entry che sparisce a runtime.
 */
export function EntryView({ entry }: { entry: Entry }) {
  switch (entry.kind) {
    case "user":
      return <UserEntry text={entry.text} />;
    case "assistant":
      return <AssistantEntry text={entry.text} />;
    case "reasoning":
      return <ReasoningEntry text={entry.text} done={entry.done} />;
    case "tool":
      return <ToolEntry name={entry.name} done={entry.done} />;
    case "artifact":
      return <UiTable artifact={entry.artifact} />;
  }
  const unreachable: never = entry;
  throw new Error(`Variante di entry non supportata: ${JSON.stringify(unreachable)}`);
}
