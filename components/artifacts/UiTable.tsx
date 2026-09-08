import type { Artifact } from "@/lib/agui/entries";

export function UiTable({ artifact }: { artifact: Artifact }) {
  if (artifact.component !== "ui-table") {
    return (
      <div className="rounded-lg border border-dashed border-[var(--border)] p-3 text-xs text-[var(--muted)]">
        Non so rendere questo artefatto ({artifact.id}).
      </div>
    );
  }

  return (
    <figure className="overflow-x-auto rounded-xl border border-[var(--border)]">
      <figcaption className="border-b border-[var(--border)] px-4 py-3 text-sm font-medium">
        {artifact.title}
      </figcaption>
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {artifact.columns.map((column, columnIndex) => (
              <th
                key={columnIndex}
                scope="col"
                className="border-b border-[var(--border)] px-4 py-2 text-left font-medium text-[var(--muted)]"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {artifact.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className="border-b border-[var(--border)] px-4 py-2 align-top"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
