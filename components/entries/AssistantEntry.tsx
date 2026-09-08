import { Streamdown } from "streamdown";

/**
 * La risposta del modello e' Markdown, e arriva un token alla volta.
 *
 * Streamdown e' un renderer Markdown pensato per lo streaming: completa da solo
 * la sintassi ancora aperta (grassetto, link, blocchi di codice a meta') invece
 * di mostrare gli asterischi finche' il token di chiusura non arriva.
 * L'HTML grezzo nel Markdown non viene interpretato: il testo del modello non
 * puo' iniettare markup nella pagina.
 */
export function AssistantEntry({ text }: { text: string }) {
  return (
    <div className="markdown px-1 text-sm leading-relaxed">
      <Streamdown parseIncompleteMarkdown>{text}</Streamdown>
    </div>
  );
}
