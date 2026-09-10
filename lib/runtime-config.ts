/**
 * The configuration the page reads when it runs, not when it was built.
 *
 * `NEXT_PUBLIC_*` variables are inlined into the bundle by `next build`, so an
 * image built against staging keeps pointing at staging forever: promoting the
 * same artifact to production would mean rebuilding it, which is not promoting
 * it. The server reads the environment during dynamic rendering and hands the
 * values to the browser in the page; the build-time variables stay as the
 * fallback for `next dev`.
 */

export interface RuntimeConfig {
  aguiUrl: string;
  /** Empty when this deployment has no process service: the panel says so. */
  processUrl: string;
  product: {
    name: string;
    tagline: string;
    description: string;
    disclaimer: string;
    locale: string;
    monogram: string;
    badges: string[];
  };
  emptyState: {
    eyebrow: string;
    headline: string;
    subhead: string;
    body: string;
  };
}

export const CONFIG_ELEMENT_ID = "lab-runtime-config";

const DEFAULTS: RuntimeConfig = {
  aguiUrl: "http://127.0.0.1:8000/agui",
  processUrl: "",
  product: {
    name: "AG-UI Lab",
    tagline: "an agent at work",
    description: "Chat, work plan, events and logs of a running agent.",
    disclaimer: "The agent can be wrong. Follow the plan and inspect the events.",
    locale: "en",
    monogram: "a/",
    badges: ["AG-UI", "MAF 1.17", "Next.js"],
  },
  emptyState: {
    eyebrow: "from the request to the result",
    headline: "An agent at work.",
    subhead: "Every step, visible.",
    body: "Ask for a comparison: follow the reasoning, the tools and the final table. The plan shows where we are.",
  },
};

function pick(...values: (string | undefined)[]): string | undefined {
  return values.find((value) => value !== undefined && value !== "");
}

/** Read on the server at request time, so the same image serves any environment. */
export function configFromEnvironment(
  env: Record<string, string | undefined> = process.env,
): RuntimeConfig {
  const badges = pick(env.PRODUCT_BADGES, env.NEXT_PUBLIC_PRODUCT_BADGES);
  return {
    aguiUrl: pick(env.AGUI_URL, env.NEXT_PUBLIC_AGUI_URL) ?? DEFAULTS.aguiUrl,
    processUrl: (
      pick(env.PROCESS_URL, env.NEXT_PUBLIC_PROCESS_URL) ?? DEFAULTS.processUrl
    ).replace(/\/$/, ""),
    product: {
      name: pick(env.PRODUCT_NAME, env.NEXT_PUBLIC_PRODUCT_NAME) ?? DEFAULTS.product.name,
      tagline:
        pick(env.PRODUCT_TAGLINE, env.NEXT_PUBLIC_PRODUCT_TAGLINE) ?? DEFAULTS.product.tagline,
      description:
        pick(env.PRODUCT_DESCRIPTION, env.NEXT_PUBLIC_PRODUCT_DESCRIPTION) ??
        DEFAULTS.product.description,
      disclaimer:
        pick(env.PRODUCT_DISCLAIMER, env.NEXT_PUBLIC_PRODUCT_DISCLAIMER) ??
        DEFAULTS.product.disclaimer,
      locale: pick(env.PRODUCT_LOCALE, env.NEXT_PUBLIC_PRODUCT_LOCALE) ?? DEFAULTS.product.locale,
      monogram:
        pick(env.PRODUCT_MONOGRAM, env.NEXT_PUBLIC_PRODUCT_MONOGRAM) ?? DEFAULTS.product.monogram,
      badges: badges
        ? badges.split(",").map((badge) => badge.trim()).filter(Boolean)
        : DEFAULTS.product.badges,
    },
    emptyState: {
      eyebrow: pick(env.EMPTY_EYEBROW, env.NEXT_PUBLIC_EMPTY_EYEBROW) ?? DEFAULTS.emptyState.eyebrow,
      headline:
        pick(env.EMPTY_HEADLINE, env.NEXT_PUBLIC_EMPTY_HEADLINE) ?? DEFAULTS.emptyState.headline,
      subhead: pick(env.EMPTY_SUBHEAD, env.NEXT_PUBLIC_EMPTY_SUBHEAD) ?? DEFAULTS.emptyState.subhead,
      body: pick(env.EMPTY_BODY, env.NEXT_PUBLIC_EMPTY_BODY) ?? DEFAULTS.emptyState.body,
    },
  };
}

let cached: RuntimeConfig | null = null;

/** What the browser reads: the injected values, or the ones it was built with. */
export function runtimeConfig(): RuntimeConfig {
  if (typeof window === "undefined") return configFromEnvironment();
  if (cached) return cached;

  const element = document.getElementById(CONFIG_ELEMENT_ID);
  if (element?.textContent) {
    try {
      cached = JSON.parse(element.textContent) as RuntimeConfig;
      return cached;
    } catch {
      // A malformed payload is not a reason to render nothing: the page falls
      // back to what it was built with, and says so once.
      console.warn("runtime configuration unreadable: using the build-time one");
    }
  }
  cached = configFromEnvironment();
  return cached;
}

export function forgetRuntimeConfig(): void {
  cached = null;
}
