/**
 * What the product is called, in one place.
 *
 * Every string here is what someone forking this repository changes first. They
 * are read from the environment so that changing them is configuration, not a
 * commit in a component.
 */
export const product = {
  name: process.env.NEXT_PUBLIC_PRODUCT_NAME ?? "AG-UI Lab",
  tagline: process.env.NEXT_PUBLIC_PRODUCT_TAGLINE ?? "an agent at work",
  description:
    process.env.NEXT_PUBLIC_PRODUCT_DESCRIPTION ??
    "Chat, work plan, events and logs of a running agent.",
  disclaimer:
    process.env.NEXT_PUBLIC_PRODUCT_DISCLAIMER ??
    "The agent can be wrong. Follow the plan and inspect the events.",
  locale: process.env.NEXT_PUBLIC_PRODUCT_LOCALE ?? "en",
  monogram: process.env.NEXT_PUBLIC_PRODUCT_MONOGRAM ?? "a/",
  badges: (process.env.NEXT_PUBLIC_PRODUCT_BADGES ?? "AG-UI,MAF 1.17,Next.js")
    .split(",")
    .map((badge) => badge.trim())
    .filter(Boolean),
} as const;

export const emptyState = {
  eyebrow: process.env.NEXT_PUBLIC_EMPTY_EYEBROW ?? "from the request to the result",
  headline: process.env.NEXT_PUBLIC_EMPTY_HEADLINE ?? "An agent at work.",
  subhead: process.env.NEXT_PUBLIC_EMPTY_SUBHEAD ?? "Every step, visible.",
  body:
    process.env.NEXT_PUBLIC_EMPTY_BODY ??
    "Ask for a comparison: follow the reasoning, the tools and the final table. The plan shows where we are.",
} as const;
