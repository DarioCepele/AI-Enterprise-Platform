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
      json: async () => ({ entries: [], cursor: "17-0|7", dropped: 0 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const page = await fetchLogs("9-0|3");

    expect(fetchMock.mock.calls[0][0]).toContain("cursor=9-0%7C3");
    expect(page.cursor).toBe("17-0|7");
  });

  it("raises when the server answers badly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));

    await expect(fetchLogs("")).rejects.toThrow(/503/);
  });
});
