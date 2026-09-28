import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { InstancePanel } from "./InstancePanel";
import { forgetRuntimeConfig } from "@/lib/runtime-config";
import { contractSample } from "@/lib/contracts";
import type { Instance } from "@/lib/processes/types";

const SAMPLE = contractSample<Instance>("process/instance");

function instance(over: Partial<Instance> = {}): Instance {
  return { ...SAMPLE, ...over };
}

function configured(url = "http://processes.test") {
  document.body.innerHTML = `<script id="lab-runtime-config" type="application/json">${JSON.stringify(
    { ...JSON.parse("{}"), processUrl: url },
  )}</script>`;
  forgetRuntimeConfig();
}

function answers(routes: Record<string, unknown>) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = Object.keys(routes).find((path) => url.includes(path));
    if (key === undefined) throw new Error(`unexpected call: ${url} (${init?.method ?? "GET"})`);
    return new Response(JSON.stringify(routes[key]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
}

beforeEach(() => {
  configured();
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = "";
  forgetRuntimeConfig();
});

describe("InstancePanel", () => {
  it("says so when no process service is configured, instead of showing an error", () => {
    configured("");
    render(<InstancePanel running={false} />);

    expect(screen.getByText(/no process service/i)).toBeInTheDocument();
  });

  it("lists the instances with the state a person reads", async () => {
    vi.stubGlobal("fetch", answers({ "/instances": { instances: [instance()] } }));
    render(<InstancePanel running={false} />);

    expect(await screen.findByText("example-approval")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for a decision/)).toBeInTheDocument();
  });

  it("puts what is waiting for somebody above the list", async () => {
    vi.stubGlobal(
      "fetch",
      answers({
        "/instances": {
          instances: [instance(), instance({ id: "b", status: "completed" })],
        },
      }),
    );
    render(<InstancePanel running={false} />);

    // One of the two is stopped in front of a person: that is the number worth
    // seeing without opening anything.
    expect(await screen.findByText(/1 waiting for someone/)).toBeInTheDocument();
  });

  it("shows the steps of the instance that is opened", async () => {
    vi.stubGlobal("fetch", answers({ "/instances/": instance(), "/instances": { instances: [instance()] } }));
    render(<InstancePanel running={false} />);
    fireEvent.click(await screen.findByText("example-approval"));

    expect(await screen.findByText("approval")).toBeInTheDocument();
    expect(screen.getByText(/escalated to 'by_hand'/)).toBeInTheDocument();
  });

  it("sends a decision to the step that is waiting for one", async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST") {
          calls.push({ url, body: JSON.parse(String(init.body)) });
          return new Response("{}", { status: 200 });
        }
        const body = url.includes("/instances/") ? instance() : { instances: [instance()] };
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );

    render(<InstancePanel running={false} />);
    fireEvent.click(await screen.findByText("example-approval"));
    fireEvent.change(await screen.findByLabelText("Who decides"), { target: { value: "reviewer" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toContain("/steps/approval/decision");
    expect(calls[0].body).toMatchObject({ by: "reviewer", decision: "approved" });
  });

  it("passes on what the service refused, instead of a status code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST") {
          return new Response(
            JSON.stringify({ detail: "step 'approval' is completed, it is not waiting" }),
            { status: 409 },
          );
        }
        const body = url.includes("/instances/") ? instance() : { instances: [instance()] };
        return new Response(JSON.stringify(body), { status: 200 });
      }),
    );

    render(<InstancePanel running={false} />);
    fireEvent.click(await screen.findByText("example-approval"));
    fireEvent.change(await screen.findByLabelText("Who decides"), { target: { value: "reviewer" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    // "409" would send whoever reads it to the logs; the reason is already here.
    expect(await screen.findByRole("alert")).toHaveTextContent("it is not waiting");
  });

  it("says that the processes are unreachable instead of showing an empty list", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 502 })));
    render(<InstancePanel running={false} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/unreachable/);
  });
});
