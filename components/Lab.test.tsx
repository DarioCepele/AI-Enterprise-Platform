import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { runAgent } from "@/lib/agui/client";
import { Lab } from "./Lab";

vi.mock("@/lib/agui/client", () => ({ runAgent: vi.fn() }));
vi.mock("@/lib/agui/logs", () => ({ fetchLogs: vi.fn().mockResolvedValue({ entries: [], cursor: 0, dropped: 0 }) }));
const runMock = vi.mocked(runAgent);
beforeEach(() => { runMock.mockReset(); });

describe("Lab", () => {
  it("collega invio, timeline, piano e conclusione della run", async () => {
    let finish!: () => void;
    runMock.mockImplementation(async (_input, onEvent) => {
      onEvent({ type: "RUN_STARTED", threadId: "t", runId: "r" });
      onEvent({ type: "STATE_SNAPSHOT", snapshot: { plan: {
        status: "completed", steps: [{ id: 1, title: "Confronta", detail: "Prepara una tabella", source: "ui_table", status: "completed", note: null }],
      } } });
      onEvent({ type: "TEXT_MESSAGE_START", messageId: "a", role: "assistant" });
      onEvent({ type: "TEXT_MESSAGE_CONTENT", messageId: "a", delta: "Ecco il confronto." });
      onEvent({ type: "RUN_FINISHED", threadId: "t", runId: "r" });
      await new Promise<void>((resolve) => { finish = resolve; });
    });
    render(<Lab />);
    const input = screen.getByRole("textbox", { name: "Messaggio" });
    fireEvent.change(input, { target: { value: "Confronta Python e TypeScript" } });
    fireEvent.click(screen.getByRole("button", { name: "invia" }));
    await act(async () => {});
    expect(runMock).toHaveBeenCalledTimes(1);
    expect(runMock.mock.calls[0][0].messages[0]).toMatchObject({ role: "user", content: "Confronta Python e TypeScript" });
    expect(screen.getByText("Confronta Python e TypeScript")).toBeInTheDocument();
    expect(screen.getByText("Ecco il confronto.")).toBeInTheDocument();
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(input).toBeDisabled();
    await act(async () => { finish(); });
    expect(input).toBeEnabled();
    expect(screen.queryByText(/Sto lavorando/)).not.toBeInTheDocument();
  });

  it("blocca invii prima del primo evento e ripristina il modulo dopo un errore", async () => {
    let reject!: (error: Error) => void;
    runMock.mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
    const { container } = render(<Lab />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "richiesta" } });
    fireEvent.submit(container.querySelector("form")!);
    fireEvent.submit(container.querySelector("form")!);
    expect(runMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox")).toBeDisabled();
    await act(async () => { reject(new Error("server non disponibile")); });
    expect(screen.getByRole("alert")).toHaveTextContent("server non disponibile");
    expect(screen.getByRole("textbox")).toBeEnabled();
  });
});
