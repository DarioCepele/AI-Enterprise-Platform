import { memo } from "react";
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
 *
 * Memoizzato: un token del testo finale cambia una sola entry, ma il reducer
 * ricostruisce la lista a ogni evento. Senza memo si rirenderizzano anche i
 * blocchi di ragionamento e le tabelle gia' chiuse.
 */
export const EntryView = memo(function EntryView({ entry }: { entry: Entry }) {
  switch (entry.kind) {
    case "user":
      return <UserEntry text={entry.text} />;
    case "assistant":
      return <AssistantEntry text={entry.text} />;
    case "reasoning":
      return <ReasoningEntry text={entry.text} done={entry.done} />;
    case "tool":
      return <ToolEntry name={entry.name} args={entry.args} done={entry.done} />;
    case "artifact":
      return <UiTable artifact={entry.artifact} />;
  }
  const unreachable: never = entry;
  throw new Error(`Variante di entry non supportata: ${JSON.stringify(unreachable)}`);
});
