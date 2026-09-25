import type { Metadata } from "next";
import { connection } from "next/server";
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { CONFIG_ELEMENT_ID, configFromEnvironment } from "@/lib/runtime-config";

// A console for watching an agent's own instrumentation, not a chat-bot skin:
// Plex's engineering lineage fits that better than a default UI grotesk, and
// pairing its own sans with its own mono keeps the two families related
// instead of arbitrary.
const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  weight: ["400", "500"],
  subsets: ["latin"],
});

export async function generateMetadata(): Promise<Metadata> {
  await connection();
  const { product } = configFromEnvironment();
  return { title: product.name, description: product.description };
}

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // `connection()` opts this render into request time, which is what makes the
  // environment readable now instead of at build time: without it, one image
  // could not be promoted from staging to production without being rebuilt.
  await connection();
  const config = configFromEnvironment();

  return (
    <html
      lang={config.product.locale}
      className={`${plexSans.variable} ${plexMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <script
          id={CONFIG_ELEMENT_ID}
          type="application/json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify(config).replace(/</g, "\\u003c"),
          }}
        />
        {children}
      </body>
    </html>
  );
}
