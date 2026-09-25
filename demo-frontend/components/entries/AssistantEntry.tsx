import { Streamdown } from "streamdown";

export function AssistantEntry({ text }: { text: string }) {
  return (
    <div className="markdown px-1 text-sm leading-relaxed">
      <Streamdown parseIncompleteMarkdown>{text}</Streamdown>
    </div>
  );
}
