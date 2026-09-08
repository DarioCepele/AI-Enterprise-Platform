import type { AGUIEvent } from "./types";

/**
 * Un gruppo di eventi consecutivi dello stesso tipo.
 *
 * Un giro di Qwen emette oltre duemila REASONING_ENCRYPTED_VALUE: una riga
 * per evento rende l'inspector illeggibile e il DOM enorme. I consecutivi
 * dello stesso tipo diventano una riga sola che espone l'array dei payload.
 */
export interface EventGroup {
  /** Tipo condiviso dagli eventi del gruppo. */
  type: string;
  /** Indice del primo evento nella lista non raggruppata: chiave stabile. */
  index: number;
  events: AGUIEvent[];
}

export function groupEvents(events: AGUIEvent[]): EventGroup[] {
  const groups: EventGroup[] = [];
  events.forEach((event, index) => {
    const last = groups[groups.length - 1];
    if (last && last.type === event.type) {
      last.events.push(event);
      return;
    }
    groups.push({ type: event.type, index, events: [event] });
  });
  return groups;
}
