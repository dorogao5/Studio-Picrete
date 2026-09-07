import { buildInfo } from "./build-info";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), buildInfo()],
  server: {
    port: 5174,
    proxy: {
      "/api": "http://localhost:8100",
    },
  },
});
