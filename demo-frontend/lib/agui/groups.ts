import type { AGUIEvent } from "./types";

export interface EventGroup {
  type: string;
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
