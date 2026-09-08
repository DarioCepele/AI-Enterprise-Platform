import { afterEach, describe, expect, it, vi } from "vitest";
import { LOGS_URL, fetchLogs } from "./logs";

afterEach(() => vi.unstubAllGlobals());

describe("fetchLogs", () => {
  it("derives the logs URL from the AG-UI one", () => {
    expect(LOGS_URL.endsWith("/logs")).toBe(true);
    expect(LOGS_URL).not.toContain("/agui");
  });

  it("passes the cursor and returns the page", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ entries: [], cursor: 7, dropped: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchLogs(3);

    expect(fetchMock.mock.calls[0][0]).toContain("cursor=3");
    expect(page.cursor).toBe(7);
  });

  it("raises when the server answers badly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));

    await expect(fetchLogs(0)).rejects.toThrow(/503/);
  });
});
