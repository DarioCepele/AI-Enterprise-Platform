import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  CONFIG_ELEMENT_ID,
  configFromEnvironment,
  forgetRuntimeConfig,
  runtimeConfig,
} from "./runtime-config";

function inject(payload: unknown): void {
  document.body.innerHTML = "";
  const element = document.createElement("script");
  element.id = CONFIG_ELEMENT_ID;
  element.type = "application/json";
  element.textContent = typeof payload === "string" ? payload : JSON.stringify(payload);
  document.body.appendChild(element);
  forgetRuntimeConfig();
}

describe("runtime configuration", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    forgetRuntimeConfig();
  });

  it("reads the environment without needing the NEXT_PUBLIC prefix", () => {
    const config = configFromEnvironment({
      AGUI_URL: "http://agent.example/agui",
      PRODUCT_NAME: "Acme Copilot",
      PRODUCT_BADGES: "A, B ,C",
    });

    // Server-side variables need no prefix: the prefix exists to say "inline
    // this into the bundle", which is exactly what we stopped doing.
    expect(config.aguiUrl).toBe("http://agent.example/agui");
    expect(config.product.name).toBe("Acme Copilot");
    expect(config.product.badges).toEqual(["A", "B", "C"]);
  });

  it("falls back to the build-time variable, then to the default", () => {
    const built = configFromEnvironment({
      NEXT_PUBLIC_AGUI_URL: "http://built-in/agui",
    });
    const bare = configFromEnvironment({});

    expect(built.aguiUrl).toBe("http://built-in/agui");
    expect(bare.aguiUrl).toBe("http://127.0.0.1:8000/agui");
    expect(bare.product.name).toBe("AG-UI Lab");
  });

  it("the browser reads what the page carried, not what the bundle knew", () => {
    inject({
      aguiUrl: "http://from-the-page/agui",
      product: { name: "From the page", badges: [] },
      emptyState: {},
    });

    expect(runtimeConfig().aguiUrl).toBe("http://from-the-page/agui");
    expect(runtimeConfig().product.name).toBe("From the page");
  });

  it("a malformed payload degrades to the build-time configuration", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    inject("{ not json");

    expect(runtimeConfig().aguiUrl).toBe("http://127.0.0.1:8000/agui");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("is read once and remembered", () => {
    inject({ aguiUrl: "http://first/agui", product: { badges: [] }, emptyState: {} });
    const first = runtimeConfig();

    document.body.innerHTML = "";

    expect(runtimeConfig()).toBe(first);
  });
});
