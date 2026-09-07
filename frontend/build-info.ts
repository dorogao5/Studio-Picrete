import { execFileSync } from "node:child_process";
import type { Plugin } from "vite";

export function buildInfo(): Plugin {
  return {
    name: "release-build-info",
    generateBundle() {
      const revision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
      this.emitFile({ type: "asset", fileName: "build-info.json", source: JSON.stringify({ revision, studio_snapshot_schema: 1 }) + "\n" });
    },
  };
}
