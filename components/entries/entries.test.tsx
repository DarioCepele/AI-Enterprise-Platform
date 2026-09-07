import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Chat } from "../Chat";
import type { Entry } from "@/lib/agui/entries";
import { EntryView } from "./index";

describe("EntryView", () => {
  it("rende la risposta dell'assistente come testo, senza interpretare HTML", () => {
    const { container } = render(<EntryView entry={{ kind: "assistant", id: "a", text: "<b>risposta</b>" }} />);
    expect(screen.getByText("<b>risposta</b>")).toBeInTheDocument();
    expect(container.querySelector("b")).toBeNull();
  });
  it("rende il messaggio dell'utente", () => {
    const entry: Entry = { kind: "user", id: "1", text: "ciao" };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("ciao")).toBeInTheDocument();
  });

  it("rende il ragionamento chiuso, non aperto", () => {
    const entry: Entry = { kind: "reasoning", id: "2", text: "penso", done: true };
    const { container } = render(<EntryView entry={entry} />);

    // Il ragionamento e' contesto, non risposta: arriva collassato.
    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("Ragionamento")).toBeInTheDocument();
  });

  it("mostra il nome del tool, non i suoi argomenti grezzi", () => {
    const entry: Entry = {
      kind: "tool",
      id: "3",
      name: "load_skill",
      args: '{"name":"comparison"}',
      done: true,
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("load_skill")).toBeInTheDocument();
    expect(screen.queryByText(/"name":"comparison"/)).toBeNull();
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

    // Meglio un riquadro che dice "non so renderlo" di un artefatto sparito.
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

  it("espone l'errore della run", () => {
    render(<Chat entries={[]} running={false} error="connessione interrotta" onSend={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("connessione interrotta");
  });
});
