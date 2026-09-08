import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Chat } from "../Chat";
import type { Entry } from "@/lib/agui/entries";
import { EntryView } from "./index";

describe("EntryView", () => {
  it("blocks scripts and dangerous URLs in the model's answer", () => {
    const text = '<script>alert(1)</script><a href="javascript:alert(1)">click</a>';
    const { container } = render(<EntryView entry={{ kind: "assistant", id: "a", text }} />);

    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).not.toContain("alert(1)");
  });

  it("renders the answer's Markdown: emphasis, list, code and table", () => {
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

  it("does not show still-open syntax while streaming", () => {
    const { container } = render(<EntryView entry={{ kind: "assistant", id: "a", text: "Confronto **net" }} />);

    expect(container.textContent).not.toContain("**");
    expect(container.querySelector('[data-streamdown="strong"]')?.textContent).toBe("net");
  });
  it("renders the user's message", () => {
    const entry: Entry = { kind: "user", id: "1", text: "ciao" };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("ciao")).toBeInTheDocument();
  });

  it("renders reasoning collapsed, not expanded", () => {
    const entry: Entry = { kind: "reasoning", id: "2", text: "penso", done: true };
    const { container } = render(<EntryView entry={entry} />);

    const details = container.querySelector("details");
    expect(details?.open).toBe(false);
    expect(screen.getByText("Ragionamento")).toBeInTheDocument();
  });

  it("shows the tool name and keeps the arguments collapsed", () => {
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

  it("shows raw arguments when they are not complete JSON", () => {
    const entry: Entry = { kind: "tool", id: "3b", name: "ui_table", args: '{"title":"Conf', done: false };
    const { container } = render(<EntryView entry={entry} />);

    expect(container.querySelector("pre")?.textContent).toBe('{"title":"Conf');
  });

  it("declares argument-less tools instead of showing an empty box", () => {
    const entry: Entry = { kind: "tool", id: "3c", name: "list_skills", args: "", done: true };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("nessun argomento")).toBeInTheDocument();
  });

  it("renders a ui-table as a real table", () => {
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

  it("degrades visibly on an unknown artifact", () => {
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
  it("keeps the entry order and sends the text, clearing the field", () => {
    const onSend = vi.fn();
    const entries: Entry[] = [
      { kind: "user", id: "u", text: "question" },
      { kind: "assistant", id: "a", text: "answer" },
    ];
    const { container } = render(<Chat entries={entries} running={false} error={null} onSend={onSend} />);
    expect(container.textContent?.indexOf("question")).toBeLessThan(container.textContent!.indexOf("answer"));
    const input = screen.getByRole("textbox", { name: "Messaggio" });
    fireEvent.change(input, { target: { value: "next question" } });
    fireEvent.click(screen.getByRole("button", { name: "invia" }));
    expect(onSend).toHaveBeenCalledExactlyOnceWith("next question");
    expect(input).toHaveValue("");
  });

  it("ignores empty text and blocks sending during a run", () => {
    const onSend = vi.fn();
    const { container, rerender } = render(<Chat entries={[]} running={false} error={null} onSend={onSend} />);
    const input = screen.getByRole("textbox", { name: "Messaggio" });
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(container.querySelector("form")!);
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "do not send" } });
    rerender(<Chat entries={[]} running={true} error={null} onSend={onSend} />);
    expect(input).toBeDisabled();
    expect(screen.getByRole("button", { name: "invia" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Sto lavorando");
    fireEvent.submit(container.querySelector("form")!);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("allows stopping the run while it is going", () => {
    const onStop = vi.fn();
    const { rerender } = render(
      <Chat entries={[]} running={false} error={null} onSend={vi.fn()} onStop={onStop} />,
    );
    expect(screen.queryByRole("button", { name: "interrompi" })).toBeNull();
    rerender(<Chat entries={[]} running error={null} onSend={vi.fn()} onStop={onStop} />);
    fireEvent.click(screen.getByRole("button", { name: "interrompi" }));

    expect(onStop).toHaveBeenCalledOnce();
  });

  it("follows the stream at the bottom, but not while reading further up", () => {
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

  it("surfaces the run's error", () => {
    render(<Chat entries={[]} running={false} error="connessione interrotta" onSend={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("connessione interrotta");
  });
});

describe("subagents in the timeline", () => {
  it("shows the subagent's name, status and question", () => {
    const entry: Entry = {
      kind: "subagent",
      id: "s1",
      name: "knowledge",
      description: "Come tipizza Rust?",
      status: "running",
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("knowledge")).toBeInTheDocument();
    expect(screen.getByText(/in corso/)).toBeInTheDocument();
    expect(screen.getByText("Come tipizza Rust?")).toBeInTheDocument();
  });

  it("tells done from running without relying on colour", () => {
    const { container, rerender } = render(
      <EntryView
        entry={{ kind: "subagent", id: "s1", name: "knowledge", description: "", status: "running" }}
      />,
    );
    expect(container.querySelector('[data-status="running"]')).not.toBeNull();

    rerender(
      <EntryView
        entry={{ kind: "subagent", id: "s1", name: "knowledge", description: "", status: "done" }}
      />,
    );
    expect(container.querySelector('[data-status="done"]')).not.toBeNull();
  });

  it("a failed subagent says what went wrong", () => {
    render(
      <EntryView
        entry={{
          kind: "subagent",
          id: "s1",
          name: "knowledge",
          description: "",
          status: "failed",
          error: "knowledge agent down",
        }}
      />,
    );

    expect(screen.getByText("knowledge agent down")).toBeInTheDocument();
  });
});

describe("briefing in the timeline", () => {
  it("shows summary, agent and sources", () => {
    const entry: Entry = {
      kind: "artifact",
      id: "a1",
      artifact: {
        component: "briefing",
        id: "kb_1",
        agent: "knowledge",
        question: "Come tipizza Go?",
        documents: ["go", "rust"],
        summary: "Statica, verificata dal compilatore.",
      },
    };
    render(<EntryView entry={entry} />);

    expect(screen.getByText("Statica, verificata dal compilatore.")).toBeInTheDocument();
    expect(screen.getByText("knowledge")).toBeInTheDocument();
    expect(screen.getByText(/fonti: go, rust/)).toBeInTheDocument();
  });
});
