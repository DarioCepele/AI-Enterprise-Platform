import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { runAgent } from "@/lib/agui/client";
import { VoiceSession, voiceConfigured } from "@/lib/voice/client";
import { Lab } from "./Lab";

vi.mock("@/lib/agui/client", () => ({ runAgent: vi.fn() }));
vi.mock("@/lib/agui/logs", () => ({ fetchLogs: vi.fn().mockResolvedValue({ entries: [], cursor: 0, dropped: 0 }) }));

type VoiceHandlers = {
  onUserTranscript?: (text: string) => void;
  onAssistantTextChunk?: (text: string) => void;
  onTurnCancelled?: () => void;
  onError?: (message: string) => void;
  onClose?: () => void;
};

vi.mock("@/lib/voice/client", () => {
  class FakeVoiceSession {
    static instances: FakeVoiceSession[] = [];
    handlers: VoiceHandlers;
    start = vi.fn(async () => {});
    stop = vi.fn();
    constructor(handlers: VoiceHandlers) {
      this.handlers = handlers;
      FakeVoiceSession.instances.push(this);
    }
  }
  return { VoiceSession: FakeVoiceSession, voiceConfigured: vi.fn(() => false) };
});

const runMock = vi.mocked(runAgent);
const voiceConfiguredMock = vi.mocked(voiceConfigured);
const FakeVoiceSession = VoiceSession as unknown as {
  instances: { handlers: VoiceHandlers; start: () => Promise<void>; stop: () => void }[];
};

beforeEach(() => {
  runMock.mockReset();
  voiceConfiguredMock.mockReturnValue(false);
  FakeVoiceSession.instances.length = 0;
});

describe("Lab", () => {
  it("wires sending, timeline, plan and the end of the run", async () => {
    let finish!: () => void;
    runMock.mockImplementation(async (_input, onEvent) => {
      onEvent({ type: "RUN_STARTED", threadId: "t", runId: "r" });
      onEvent({ type: "STATE_SNAPSHOT", snapshot: { plan: {
        status: "completed", steps: [{ id: 1, title: "Compare", detail: "Prepare a table", source: "ui_table", status: "completed", note: null }],
      } } });
      onEvent({ type: "TEXT_MESSAGE_START", messageId: "a", role: "assistant" });
      onEvent({ type: "TEXT_MESSAGE_CONTENT", messageId: "a", delta: "Ecco il confronto." });
      onEvent({ type: "RUN_FINISHED", threadId: "t", runId: "r" });
      await new Promise<void>((resolve) => { finish = resolve; });
    });
    render(<Lab />);
    const input = screen.getByRole("textbox", { name: "Message" });
    fireEvent.change(input, { target: { value: "Confronta Python e TypeScript" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));
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

  it("blocks sends before the first event and restores the form after an error", async () => {
    let reject!: (error: Error) => void;
    runMock.mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
    const { container } = render(<Lab />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "richiesta" } });
    fireEvent.submit(container.querySelector("form")!);
    fireEvent.submit(container.querySelector("form")!);
    expect(runMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox")).toBeDisabled();
    await act(async () => { reject(new Error("server unavailable")); });
    expect(screen.getByRole("alert")).toHaveTextContent("server unavailable");
    expect(screen.getByRole("textbox")).toBeEnabled();
  });

  it("a voice turn lands in the same timeline as a typed one, and stopping re-enables typing", async () => {
    voiceConfiguredMock.mockReturnValue(true);
    render(<Lab />);

    const micButton = screen.getByRole("button", { name: /talk to the agent/i });
    await act(async () => {
      fireEvent.click(micButton);
    });
    expect(FakeVoiceSession.instances).toHaveLength(1);
    expect(screen.getByRole("textbox", { name: "Message" })).toBeDisabled();

    const { handlers } = FakeVoiceSession.instances[0];
    act(() => handlers.onUserTranscript?.("che tempo fa"));
    act(() => handlers.onAssistantTextChunk?.("Non ho accesso al meteo. "));
    act(() => handlers.onAssistantTextChunk?.("Posso aiutarti con altro."));

    expect(screen.getByText("che tempo fa")).toBeInTheDocument();
    expect(
      screen.getByText("Non ho accesso al meteo. Posso aiutarti con altro."),
    ).toBeInTheDocument();

    const stopButton = screen.getByRole("button", { name: /stop talking/i });
    fireEvent.click(stopButton);
    expect(FakeVoiceSession.instances[0].stop).toHaveBeenCalled();
    expect(screen.getByRole("textbox", { name: "Message" })).toBeEnabled();
  });

  it("an action that needs approval waits for the person, and their yes resumes the run", async () => {
    runMock.mockImplementationOnce(async (input, onEvent) => {
      onEvent({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId });
      onEvent({ type: "TOOL_CALL_START", toolCallId: "call_1", toolCallName: "start_process" });
      onEvent({ type: "TOOL_CALL_ARGS", toolCallId: "call_1", delta: '{"process_id":"example-approval"}' });
      onEvent({ type: "TOOL_CALL_END", toolCallId: "call_1" });
      onEvent({
        type: "RUN_FINISHED",
        threadId: input.threadId,
        runId: input.runId,
        outcome: {
          type: "interrupt",
          interrupts: [{ id: "i-1", reason: "tool_call", message: "Approve running start_process?", toolCallId: "call_1" }],
        },
      });
    });
    runMock.mockImplementationOnce(async (input, onEvent) => {
      onEvent({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId });
      onEvent({ type: "TOOL_CALL_RESULT", toolCallId: "call_1", content: "I started it." });
      onEvent({ type: "TEXT_MESSAGE_START", messageId: "a", role: "assistant" });
      onEvent({ type: "TEXT_MESSAGE_CONTENT", messageId: "a", delta: "Started." });
      onEvent({ type: "TEXT_MESSAGE_END", messageId: "a" });
      onEvent({ type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId });
    });
    render(<Lab />);
    const input = screen.getByRole("textbox", { name: "Message" });

    fireEvent.change(input, { target: { value: "start the approval process" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));
    await act(async () => {});

    expect(screen.getByRole("region", { name: "Approval needed" })).toHaveTextContent("start_process");
    expect(input).toBeDisabled();
    expect(input).toHaveAttribute("placeholder", "Answer the approval above to continue…");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    });

    expect(runMock).toHaveBeenCalledTimes(2);
    const [first, resume] = runMock.mock.calls.map((call) => call[0]);
    expect(resume.threadId).toBe(first.threadId);
    expect(resume.messages).toEqual([]);
    expect(resume.resume).toEqual([{ interruptId: "i-1", status: "resolved", payload: { approved: true } }]);
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getByText("Started.")).toBeInTheDocument();
    expect(input).toBeEnabled();
  });

  it("hides the mic entirely when no voice service is configured", () => {
    voiceConfiguredMock.mockReturnValue(false);
    render(<Lab />);
    expect(screen.queryByRole("button", { name: /talk to the agent/i })).not.toBeInTheDocument();
  });
});
