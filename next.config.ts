import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Produce un bundle autosufficiente: immagine finale senza node_modules.
  output: "standalone",
};

export default nextConfig;
