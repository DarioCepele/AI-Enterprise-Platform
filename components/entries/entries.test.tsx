import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Chat } from "../Chat";
import type { Entry } from "@/lib/agui/entries";
import { EntryView } from "./index";

describe("EntryView", () => {
  it("blocca script e URL pericolosi nella risposta del modello", () => {
    const text = '<script>alert(1)</script><a href="javascript:alert(1)">click</a>';
    const { container } = render(<EntryView entry={{ kind: "assistant", id: "a", text }} />);

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).not.toContain("alert(1)");
  });

  it("rende il Markdown della risposta: enfasi, elenco, codice e tabella", () => {
    const text = [
      "Confronto **netto**:",
      "",
      "- Python usa `try/except`",
      "- Go restituisce `err`",
      "",
      "| Tema | Go |",
      "| --- | --- |",
      "| tipi | statici |",
    ].join("\n");
    const { container } = render(<EntryView entry={{ kind: "assistant", id: "a", text }} />);

    expect(container.querySelector('[data-streamdown="strong"]')?.textContent).toBe("netto");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelectorAll("code")[0]?.textContent).toBe("try/except");
    expect(container.querySelector("table")).not.toBeNull();
  });

  it("non mostra la sintassi ancora aperta durante lo streaming", () => {
    const { container } = render(<EntryView entry={{ kind: "assistant", id: "a", text: "Confronto **net" }} />);

    expect(container.textContent).not.toContain("**");
    expect(container.querySelector('[data-streamdown="strong"]')?.textContent).toBe("net");
  });
  it("rende il messaggio dell'utente", () => {
    const entry: Entry = { kind: "user", id: "1", text: "ciao" };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("ciao")).toBeInTheDocument();
  });

  it("rende il ragionamento chiuso, non aperto", () => {
    const entry: Entry = { kind: "reasoning", id: "2", text: "penso", done: true };
    const { container } = render(<EntryView entry={entry} />);

    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("Ragionamento")).toBeInTheDocument();
  });

  it("mostra il nome del tool e tiene chiusi gli argomenti", () => {
    const entry: Entry = {
      kind: "tool",
      id: "3",
      name: "load_skill",
      args: '{"name":"comparison"}',
      done: true,
    };
    const { container } = render(<EntryView entry={entry} />);

    expect(screen.getByText("load_skill")).toBeInTheDocument();
    expect(container.querySelector("details")?.open).toBe(false);
    expect(container.querySelector("pre")?.textContent).toBe(
      JSON.stringify({ name: "comparison" }, null, 2),
    );
  });

  it("mostra gli argomenti grezzi quando non sono JSON completo", () => {
    const entry: Entry = { kind: "tool", id: "3b", name: "ui_table", args: '{"title":"Conf', done: false };
    const { container } = render(<EntryView entry={entry} />);

    expect(container.querySelector("pre")?.textContent).toBe('{"title":"Conf');
  });

  it("dichiara i tool senza argomenti invece di mostrare un riquadro vuoto", () => {
    const entry: Entry = { kind: "tool", id: "3c", name: "list_skills", args: "", done: true };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("nessun argomento")).toBeInTheDocument();
  });

  it("rende una ui-table come tabella vera", () => {
    const entry: Entry = {
      kind: "artifact",
      id: "4",
      artifact: {
        component: "ui-table",
        id: "art_1",
        title: "Confronto",
        columns: ["Tema", "A"],
        rows: [["Tipi", "statici"]],
      },
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Confronto")).toBeInTheDocument();
    expect(screen.getByText("statici")).toBeInTheDocument();
  });

  it("degrada visibilmente su un artefatto sconosciuto", () => {
    const entry: Entry = {
      kind: "artifact",
      id: "5",
      artifact: { component: "unknown", id: "art_9", raw: { component: "ui-chart" } },
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByText(/non so rendere/i)).toBeInTheDocument();
  });
});

describe("Chat", () => {
  it("mantiene l'ordine delle entry e invia il testo azzerando il campo", () => {
    const onSend = vi.fn();
    const entries: Entry[] = [
      { kind: "user", id: "u", text: "domanda" },
      { kind: "assistant", id: "a", text: "risposta" },
    ];
    const { container } = render(<Chat entries={entries} running={false} error={null} onSend={onSend} />);
    expect(container.textContent?.indexOf("domanda")).toBeLessThan(container.textContent!.indexOf("risposta"));
    const input = screen.getByRole("textbox", { name: "Messaggio" });
    fireEvent.change(input, { target: { value: "prossima domanda" } });
    fireEvent.click(screen.getByRole("button", { name: "invia" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("prossima domanda");
    expect(input).toHaveValue("");
  });

  it("ignora testo vuoto e blocca l'invio durante una run", () => {
    const onSend = vi.fn();
    const { container, rerender } = render(<Chat entries={[]} running={false} error={null} onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "Messaggio" });
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(container.querySelector("form")!);
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "non inviare" } });
    rerender(<Chat entries={[]} running={true} error={null} onSend={onSend} />);
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "invia" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Sto lavorando");
    fireEvent.submit(container.querySelector("form")!);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("permette di interrompere la run mentre e' in corso", () => {
    const onStop = vi.fn();
    const { rerender } = render(
      <Chat entries={[]} running={false} error={null} onSend={vi.fn()} onStop={onStop} />,
    );
    expect(screen.queryByRole("button", { name: "interrompi" })).toBeNull();
    rerender(<Chat entries={[]} running error={null} onSend={vi.fn()} onStop={onStop} />);
    fireEvent.click(screen.getByRole("button", { name: "interrompi" }));

    expect(onStop).toHaveBeenCalledOnce();
  });

  it("segue lo stream in fondo, ma non se si sta rileggendo piu' su", () => {
    const entry = (i: number): Entry => ({ kind: "assistant", id: `a${i}`, text: `riga ${i}` });
    const { container, rerender } = render(
      <Chat entries={[entry(1)]} running error={null} onSend={vi.fn()} />,
    );
    const scroller = container.querySelector(".overflow-y-auto") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { value: 200, configurable: true });

    rerender(<Chat entries={[entry(1), entry(2)]} running error={null} onSend={vi.fn()} />);
    expect(scroller.scrollTop).toBe(1000);

    scroller.scrollTop = 100;
    fireEvent.scroll(scroller);
    rerender(<Chat entries={[entry(1), entry(2), entry(3)]} running error={null} onSend={vi.fn()} />);
    expect(scroller.scrollTop).toBe(100);
  });

  it("espone l'errore della run", () => {
    render(<Chat entries={[]} running={false} error="connessione interrotta" onSend={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("connessione interrotta");
  });
});

describe("sottoagenti in timeline", () => {
  it("mostra nome, stato e domanda del sottoagente", () => {
    const entry: Entry = {
      kind: "subagent",
      id: "s1",
      name: "knowledge",
      description: "Come tipizza Rust?",
      stato: "in corso",
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("knowledge")).toBeInTheDocument();
    expect(screen.getByText(/in corso/)).toBeInTheDocument();
    expect(screen.getByText("Come tipizza Rust?")).toBeInTheDocument();
  });

  it("distingue concluso da in corso senza affidarsi al colore", () => {
    const { container, rerender } = render(
      <EntryView
        entry={{ kind: "subagent", id: "s1", name: "knowledge", description: "", stato: "in corso" }}
      />,
    );
    expect(container.querySelector('[data-stato="in corso"]')).not.toBeNull();

    rerender(
      <EntryView
        entry={{ kind: "subagent", id: "s1", name: "knowledge", description: "", stato: "concluso" }}
      />,
    );
    expect(container.querySelector('[data-stato="concluso"]')).not.toBeNull();
  });

  it("un sottoagente fallito dice cosa e' andato storto", () => {
    render(
      <EntryView
        entry={{
          kind: "subagent",
          id: "s1",
          name: "knowledge",
          description: "",
          stato: "errore",
          errore: "knowledge agent giu'",
        }}
      />,
    );

    expect(screen.getByText("knowledge agent giu'")).toBeInTheDocument();
  });
});

describe("scheda in timeline", () => {
  it("mostra estratto, agente e fonti", () => {
    const entry: Entry = {
      kind: "artifact",
      id: "a1",
      artifact: {
        component: "scheda",
        id: "kb_1",
        agente: "knowledge",
        domanda: "Come tipizza Go?",
        documenti: ["go", "rust"],
        estratto: "Statica, verificata dal compilatore.",
      },
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("Statica, verificata dal compilatore.")).toBeInTheDocument();
    expect(screen.getByText("knowledge")).toBeInTheDocument();
    expect(screen.getByText(/fonti: go, rust/)).toBeInTheDocument();
  });
});
