import type { Metadata } from "next";
import { connection } from "next/server";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { CONFIG_ELEMENT_ID, configFromEnvironment } from "@/lib/runtime-config";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
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
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
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
