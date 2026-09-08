import { afterEach, describe, expect, it, vi } from "vitest";
import { runAgent } from "./client";
import type { AGUIEvent, RunInput } from "./types";

const input: RunInput = {
  threadId: "t1",
  runId: "r1",
  messages: [{ id: "m1", role: "user", content: "ciao" }],
  state: {},
  tools: [],
  context: [],
  forwardedProps: {},
};
const encoder = new TextEncoder();

function mockStream(chunks: Uint8Array[]) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  const fetchMock = vi.fn().mockResolvedValue(new Response(body));
  vi.stubGlobal("fetch", fetchMock);
  return { body, fetchMock };
}

afterEach(() => vi.unstubAllGlobals());

describe("runAgent", () => {
  it("sends a single POST and delivers events in order without altering payloads", async () => {
    const expected: AGUIEvent[] = [
      { type: "RUN_STARTED", threadId: "t1", runId: "r1" },
      { type: "TOOL_CALL_RESULT", toolCallId: "c1", content: '{"component":"ui-table"}' },
      { type: "EVENTO_FUTURO", valore: 42 } as unknown as AGUIEvent,
      { type: "RUN_FINISHED", threadId: "t1", runId: "r1" },
    ];
    const { body, fetchMock } = mockStream([
      encoder.encode(expected.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")),
    ]);
    const events: AGUIEvent[] = [];

    await runAgent(input, (event) => events.push(event));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      process.env.NEXT_PUBLIC_AGUI_URL ?? "http://127.0.0.1:8000/agui",
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(input),
        signal: undefined,
      },
    );
    expect(events).toEqual(expected);
    expect(body.locked).toBe(false);
  });

  it("passes the abort signal to the request", async () => {
    const { fetchMock } = mockStream([encoder.encode("")]);
    const controller = new AbortController();

    await runAgent(input, () => {}, controller.signal);

    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });

  it("propagates aborting a run already under way", async () => {
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(encoder.encode('data: {"type":"RUN_STARTED","threadId":"t1","runId":"r1"}\n\n'));
      },
      cancel() {},
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const events: AGUIEvent[] = [];

    const run = runAgent(
      input,
      (event) => {
        events.push(event);
        controller.abort();
        throw new DOMException("interrotta", "AbortError");
      },
      controller.signal,
    );

    await expect(run).rejects.toThrow("interrotta");
    expect(events).toHaveLength(1);
  });

  it.each(["\n", "\r\n", "\r"])("handles %j terminators and UTF-8 split byte by byte", async (eol) => {
    const expected = { type: "TEXT_MESSAGE_CONTENT", messageId: "m2", delta: "caffè ☕" };
    const bytes = encoder.encode(`\uFEFFdata:${JSON.stringify(expected)}${eol}${eol}`);
    mockStream(Array.from(bytes, (byte) => Uint8Array.of(byte)));
    const events: AGUIEvent[] = [];

    await runAgent(input, (event) => events.push(event));

    expect(events).toEqual([expected]);
  });

  it("joins data lines and ignores SSE comments and metadata", async () => {
    mockStream([encoder.encode(
      ': keepalive\r\n\r\nid: 1\nevent: message\nretry: 1000\n' +
      'data: {"type":"RUN_STARTED",\n' +
      'data:\n' +
      'data: "threadId":"t1","runId":"r1"}\n\n',
    )]);
    const events: AGUIEvent[] = [];

    await runAgent(input, (event) => events.push(event));

    expect(events).toEqual([{ type: "RUN_STARTED", threadId: "t1", runId: "r1" }]);
  });

  it("delivers events before the connection closes", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    let received!: (event: AGUIEvent) => void;
    const firstEvent = new Promise<AGUIEvent>((resolve) => { received = resolve; });
    const run = runAgent(input, received);

    controller.enqueue(encoder.encode('data: {"type":"RUN_STARTED","threadId":"t1","runId":"r1"}\n\n'));
    expect(await firstEvent).toEqual({ type: "RUN_STARTED", threadId: "t1", runId: "r1" });
    controller.close();
    await run;
  });

  it("does not deliver an event without its final blank line", async () => {
    mockStream([encoder.encode('data: {"type":"RUN_ERROR","message":"incompleto"}\n')]);
    const onEvent = vi.fn();

    await runAgent(input, onEvent);

    expect(onEvent).not.toHaveBeenCalled();
  });

  it.each([503, 204])("reports HTTP response %i with no valid stream", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));

    await expect(runAgent(input, vi.fn())).rejects.toThrow(`AG-UI ha risposto ${status}`);
  });

  it.each(["json", "callback"])("cancels the stream and releases the reader when %s fails", async (failure) => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(failure === "json"
          ? "data: JSON non valido\n\n"
          : 'data: {"type":"RUN_ERROR","message":"errore"}\n\n'));
      },
      cancel,
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const onEvent = () => { throw new Error("callback fallita"); };

    await expect(runAgent(input, onEvent)).rejects.toThrow();

    expect(cancel).toHaveBeenCalledTimes(1);
    expect(body.locked).toBe(false);
  });
});
