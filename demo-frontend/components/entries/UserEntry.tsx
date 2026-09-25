export function UserEntry({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-[var(--surface-accent)] px-4 py-3 text-sm">
        {text}
      </div>
    </div>
  );
}
