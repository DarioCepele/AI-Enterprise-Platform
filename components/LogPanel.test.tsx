import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchLogs, type LogPage } from "@/lib/agui/logs";
import { LogPanel } from "./LogPanel";

vi.mock("@/lib/agui/logs", () => ({ fetchLogs: vi.fn() }));
const fetchMock = vi.mocked(fetchLogs);
const page = (seq: number, message = `line ${seq}`, dropped = 0): LogPage => ({
  cursor: String(seq), dropped,
  entries: [{ seq, ts: "2026-09-07T09:00:00Z", level: "INFO", source: "tools.plan_tools", message }],
});
async function settle() { await act(async () => {}); }

beforeEach(() => {
  vi.useFakeTimers();
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({ entries: [], cursor: "", dropped: 0 });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("LogPanel", () => {
  it("does not poll the server at rest", async () => {
    render(<LogPanel running={false} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText("nessun log")).toBeInTheDocument();
  });

  it("advances the cursor, collects the tail and then stops", async () => {
    fetchMock.mockResolvedValueOnce(page(1)).mockResolvedValueOnce(page(2)).mockResolvedValueOnce(page(3));
    const { rerender } = render(<LogPanel running />);
    await settle();
    expect(fetchMock.mock.calls[0][0]).toBe("");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock.mock.calls[1][0]).toBe("1");
    rerender(<LogPanel running={false} />);
    await settle();
    expect(fetchMock.mock.calls[2][0]).toBe("2");
    expect(screen.getByText("line 3")).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    rerender(<LogPanel running />);
    await settle();
    expect(fetchMock.mock.calls[3][0]).toBe("3");
  });

  it("waits for a slow answer before scheduling the next poll", async () => {
    let resolve!: (value: LogPage) => void;
    fetchMock.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    render(<LogPanel running />);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { resolve(page(1)); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("1");
  });

  it("cancels and ignores stale answers across run changes and unmount", async () => {
    let resolve!: (value: LogPage) => void;
    fetchMock.mockReturnValueOnce(new Promise((done) => { resolve = done; })).mockResolvedValueOnce(page(2, "coda"));
    const { rerender, unmount } = render(<LogPanel running />);
    const signal = fetchMock.mock.calls[0][1];
    rerender(<LogPanel running={false} />);
    await settle();
    expect(signal?.aborted).toBe(true);
    await act(async () => { resolve(page(1, "obsoleto")); });
    expect(screen.queryByText("obsoleto")).not.toBeInTheDocument();
    expect(screen.getByText("coda")).toBeInTheDocument();
    const tailSignal = fetchMock.mock.calls[1][1];
    unmount();
    expect(tailSignal?.aborted).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows an error and recovers on the next poll without losing lines", async () => {
    fetchMock.mockRejectedValueOnce(new Error("503")).mockResolvedValueOnce(page(1));
    render(<LogPanel running />);
    await settle();
    expect(screen.getByRole("alert")).toHaveTextContent("503");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("line 1")).toBeInTheDocument();
  });

  it("reports the lines the buffer dropped", async () => {
    fetchMock.mockResolvedValueOnce(page(8, "disponibile", 7));
    render(<LogPanel running />);
    await settle();
    expect(screen.getByRole("status")).toHaveTextContent("7 log lines no longer available");
    expect(screen.getByText("disponibile")).toBeInTheDocument();
  });
});
