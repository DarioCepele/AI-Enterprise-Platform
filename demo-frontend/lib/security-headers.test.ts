import { describe, expect, it } from "vitest";
import { securityHeaders } from "./security-headers";

const services = {
  aguiUrl: "https://agent.example.com/agui",
  processUrl: "https://processes.example.com",
  voiceUrl: "wss://voice.example.com/ws/voice",
};

function directives(policy: string): Record<string, string> {
  return Object.fromEntries(
    policy.split(";").map((part) => {
      const [name, ...values] = part.trim().split(/\s+/);
      return [name, values.join(" ")];
    }),
  );
}

describe("security headers", () => {
  it("lets the page reach exactly its own services", () => {
    const csp = directives(securityHeaders(services, "n0nce")["Content-Security-Policy"]);

    expect(csp["connect-src"]).toBe(
      "'self' https://agent.example.com https://processes.example.com wss://voice.example.com",
    );
  });

  it("runs only scripts from this origin or carrying the request's nonce", () => {
    const csp = directives(securityHeaders(services, "n0nce")["Content-Security-Policy"]);

    expect(csp["script-src"]).toBe("'self' 'nonce-n0nce'");
    expect(csp["object-src"]).toBe("'none'");
    expect(csp["frame-ancestors"]).toBe("'none'");
    expect(csp["base-uri"]).toBe("'self'");
  });

  it("allows eval only in development, where React needs it", () => {
    const dev = securityHeaders(services, "n", { development: true });
    const prod = securityHeaders(services, "n");

    expect(dev["Content-Security-Policy"]).toContain("'unsafe-eval'");
    expect(prod["Content-Security-Policy"]).not.toContain("unsafe-eval");
  });

  it("leaves out services this deployment does not have, and unreadable addresses", () => {
    const csp = directives(
      securityHeaders({ ...services, processUrl: "", voiceUrl: "not a url" }, "n")[
        "Content-Security-Policy"
      ],
    );

    expect(csp["connect-src"]).toBe("'self' https://agent.example.com");
  });

  it("names each origin once", () => {
    const csp = directives(
      securityHeaders(
        { aguiUrl: "http://127.0.0.1:8000/agui", processUrl: "http://127.0.0.1:8000", voiceUrl: "" },
        "n",
      )["Content-Security-Policy"],
    );

    expect(csp["connect-src"]).toBe("'self' http://127.0.0.1:8000");
  });

  it("goes out with the usual hardening, and the microphone for this page only", () => {
    const headers = securityHeaders(services, "n");

    expect(headers["X-Content-Type-Options"]).toBe("nosniff");
    expect(headers["X-Frame-Options"]).toBe("DENY");
    expect(headers["Referrer-Policy"]).toBe("strict-origin-when-cross-origin");
    expect(headers["Permissions-Policy"]).toContain("microphone=(self)");
  });
});
