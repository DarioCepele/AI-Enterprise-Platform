"use client";

interface Props {
  shared: Record<string, unknown>;
}

/**
 * Mostra lo stato condiviso grezzo. In tappa 2 questo pannello diventa
 * il "Piano di lavoro" e legge shared.plan.
 */
export function StatePanel({ shared }: Props) {
  const empty = Object.keys(shared).length === 0;

  return (
    <div className="border-b p-4">
      <h2 className="mb-2 font-mono text-xs uppercase tracking-wide text-gray-500">
        Stato condiviso
      </h2>
      {empty ? (
        <p className="text-xs text-gray-400">nessuno stato</p>
      ) : (
        <pre className="overflow-x-auto text-xs">{JSON.stringify(shared, null, 2)}</pre>
      )}
    </div>
  );
}
