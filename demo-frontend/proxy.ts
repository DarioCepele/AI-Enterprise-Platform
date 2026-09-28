import { NextResponse, type NextRequest } from "next/server";
import { configFromEnvironment } from "@/lib/runtime-config";
import { securityHeaders } from "@/lib/security-headers";

/**
 * Sets the security headers on every page (see lib/security-headers.ts).
 *
 * The nonce travels on the request too: Next.js reads it from the policy there
 * and puts it on its own scripts while rendering, which is why the pages are
 * rendered per request (the layout already is, for the runtime configuration).
 */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const headers = securityHeaders(configFromEnvironment(), nonce, {
    development: process.env.NODE_ENV === "development",
  });

  const forwarded = new Headers(request.headers);
  forwarded.set("x-nonce", nonce);
  forwarded.set("Content-Security-Policy", headers["Content-Security-Policy"]);

  const response = NextResponse.next({ request: { headers: forwarded } });
  for (const [name, value] of Object.entries(headers)) {
    response.headers.set(name, value);
  }
  return response;
}

export const config = {
  matcher: [
    {
      // Pages, not the static files they load.
      source: "/((?!_next/static|_next/image|favicon.ico|pcm-worklet.js).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
