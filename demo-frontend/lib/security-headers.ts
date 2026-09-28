import type { RuntimeConfig } from "./runtime-config";

/**
 * The headers every page goes out with: a Content Security Policy built per
 * request, and the usual hardening around it.
 *
 * Per request, because two of its inputs only exist then: the nonce that lets
 * the framework's own inline scripts run (and nobody else's), and the origins
 * of the services the page talks to, which come from the environment at
 * runtime like the rest of the configuration.
 *
 * Where it departs from the Next.js guide, and why:
 * - `style-src 'unsafe-inline'`: React and the Markdown renderer write `style`
 *   attributes, which a nonce cannot cover. Injected styles are a far smaller
 *   risk than injected scripts, which stay nonce-only.
 * - no `'strict-dynamic'`: it would ignore `'self'`, and the microphone's audio
 *   worklet (`/pcm-worklet.js`) is loaded by URL, not by a trusted script.
 * - no `upgrade-insecure-requests`: it would rewrite the services' `http://`
 *   addresses in local development. HTTPS is enforced where TLS ends (HSTS at
 *   the ingress), not guessed by the page.
 */
export function securityHeaders(
  config: Pick<RuntimeConfig, "aguiUrl" | "processUrl" | "voiceUrl">,
  nonce: string,
  { development = false }: { development?: boolean } = {},
): Record<string, string> {
  const services = [config.aguiUrl, config.processUrl, config.voiceUrl]
    .map(originOf)
    .filter((origin): origin is string => origin !== null);
  const connect = ["'self'", ...new Set(services)].join(" ");
  // React's development build evaluates code to rebuild server error stacks;
  // production needs no eval at all.
  const evaluation = development ? " 'unsafe-eval'" : "";

  const policy = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}'${evaluation}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "media-src 'self' blob:",
    `connect-src ${connect}`,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  return {
    "Content-Security-Policy": policy,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    // The microphone is the one device the page asks for.
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
  };
}

function originOf(url: string): string | null {
  if (!url) return null;
  try {
    const origin = new URL(url).origin;
    return origin === "null" ? null : origin;
  } catch {
    return null;
  }
}
