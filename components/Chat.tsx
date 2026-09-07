"use client";

import type { ChatMessage } from "@/lib/agui/reducer";

interface Props {
  messages: ChatMessage[];
  running: boolean;
  error: string | null;
  onSend: (text: string) => void;
}

export function Chat({ messages, running, error, onSend }: Props) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "text-right" : "text-left"}>
            <span className="inline-block max-w-[80%] whitespace-pre-wrap rounded-lg bg-gray-100 px-3 py-2 text-sm">
              {m.content}
            </span>
          </div>
        ))}
        {running && <p className="text-xs text-gray-500">sto lavorando…</p>}
        {error && <p className="text-xs text-red-600">errore: {error}</p>}
      </div>

      <form
        className="flex gap-2 border-t p-3"
        onSubmit={(e) => {
          e.preventDefault();
          const input = e.currentTarget.elements.namedItem("q") as HTMLInputElement;
          if (!input.value.trim()) return;
          onSend(input.value);
          input.value = "";
        }}
      >
        <input
          name="q"
          disabled={running}
          placeholder="Scrivi un messaggio…"
          className="flex-1 rounded-full border px-4 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={running}
          className="rounded-full bg-black px-4 py-2 text-sm text-white disabled:opacity-40"
        >
          invia
        </button>
      </form>
    </div>
  );
}
