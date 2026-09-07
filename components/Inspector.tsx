"use client";

import { useState } from "react";
import type { AGUIEvent } from "@/lib/agui/types";

const FILTERS = {
  tutti: () => true,
  testo: (e: AGUIEvent) => e.type.startsWith("TEXT_MESSAGE"),
  tool: (e: AGUIEvent) => e.type.startsWith("TOOL_CALL"),
  stato: (e: AGUIEvent) => e.type.startsWith("STATE") || e.type.startsWith("RUN"),
} as const;

export function Inspector({ events }: { events: AGUIEvent[] }) {
  const [filter, setFilter] = useState<keyof typeof FILTERS>("tutti");
  const shown = events.filter(FILTERS[filter]);

  return (
    <div className="flex min-h-0 flex-1 flex-col p-4">
      <h2 className="mb-2 font-mono text-xs uppercase tracking-wide text-gray-500">
        Event inspector {events.length}
      </h2>

      <div className="mb-2 flex gap-1">
        {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((name) => (
          <button
            key={name}
            onClick={() => setFilter(name)}
            className={`rounded-full px-2 py-0.5 text-xs ${
              filter === name ? "bg-black text-white" : "bg-gray-100"
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
        {shown.map((e, i) => (
          <details key={i} className="border-b py-1">
            <summary className="cursor-pointer text-amber-700">{e.type}</summary>
            <pre className="overflow-x-auto pt-1 text-gray-600">
              {JSON.stringify(e, null, 2)}
            </pre>
          </details>
        ))}
      </div>
    </div>
  );
}
