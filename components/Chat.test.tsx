import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Chat } from "./Chat";

afterEach(() => vi.unstubAllGlobals());

function attach(file: File) {
  const input = screen.getByLabelText("Attach a video") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("Chat — attaching a video", () => {
  it("uploads the file with a raw POST body, then sends a message with a video part referencing the returned url", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ url: "https://cdn.example/v/abc.mp4" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSend = vi.fn();
    render(
      <Chat
        entries={[]}
        running={false}
        error={null}
        onSend={onSend}
        voiceAvailable={false}
        voiceActive={false}
        voiceError={null}
        onToggleVoice={vi.fn()}
      />,
    );

    const file = new File(["fake-bytes"], "clip.mp4", { type: "video/mp4" });
    attach(file);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "guarda questo" } });
    fireEvent.submit(screen.getByLabelText("Message").closest("form")!);

    await act(async () => {});

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/uploads$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(file);
    expect(init.body instanceof FormData).toBe(false);

    expect(onSend).toHaveBeenCalledTimes(1);
    const [content] = onSend.mock.calls[0];
    expect(content).toEqual([
      { type: "text", text: "guarda questo" },
      { type: "video", source: { type: "url", value: "https://cdn.example/v/abc.mp4" } },
    ]);
  });

  it("shows a visible error and does not send anything when the upload fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 500 })));
    const onSend = vi.fn();
    render(
      <Chat
        entries={[]}
        running={false}
        error={null}
        onSend={onSend}
        voiceAvailable={false}
        voiceActive={false}
        voiceError={null}
        onToggleVoice={vi.fn()}
      />,
    );

    attach(new File(["fake-bytes"], "clip.mp4", { type: "video/mp4" }));
    fireEvent.submit(screen.getByLabelText("Message").closest("form")!);

    await act(async () => {});

    expect(screen.getByRole("alert")).toHaveTextContent(/upload error/i);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("still sends a plain text message with no attachment", () => {
    const onSend = vi.fn();
    render(
      <Chat
        entries={[]}
        running={false}
        error={null}
        onSend={onSend}
        voiceAvailable={false}
        voiceActive={false}
        voiceError={null}
        onToggleVoice={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "ciao" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    expect(onSend).toHaveBeenCalledWith("ciao");
  });
});

describe("Chat — talking to the agent", () => {
  it("has no mic when no voice service is configured", () => {
    render(
      <Chat
        entries={[]}
        running={false}
        error={null}
        onSend={vi.fn()}
        voiceAvailable={false}
        voiceActive={false}
        voiceError={null}
        onToggleVoice={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /talk to the agent/i })).not.toBeInTheDocument();
  });

  it("calls onToggleVoice from the composer bar, and disables typing while listening", () => {
    const onToggleVoice = vi.fn();
    const { rerender } = render(
      <Chat
        entries={[]}
        running={false}
        error={null}
        onSend={vi.fn()}
        voiceAvailable={true}
        voiceActive={false}
        voiceError={null}
        onToggleVoice={onToggleVoice}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /talk to the agent/i }));
    expect(onToggleVoice).toHaveBeenCalledTimes(1);

    rerender(
      <Chat
        entries={[]}
        running={false}
        error={null}
        onSend={vi.fn()}
        voiceAvailable={true}
        voiceActive={true}
        voiceError={null}
        onToggleVoice={onToggleVoice}
      />,
    );
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByPlaceholderText("Listening…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /stop talking/i })).toBeInTheDocument();
  });

  it("shows a visible error when the voice session fails", () => {
    render(
      <Chat
        entries={[]}
        running={false}
        error={null}
        onSend={vi.fn()}
        voiceAvailable={true}
        voiceActive={false}
        voiceError="il microfono non è raggiungibile"
        onToggleVoice={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("il microfono non è raggiungibile");
  });
});
