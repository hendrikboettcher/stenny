// @ts-nocheck — a Node script for Vite's dev server, not part of the app
// Development only: lets the interface run in a normal browser (`npm run dev`, http://127.0.0.1:1420) against
// the real engine. POST /__engine {method, params} goes to one `stenny app-server`, like the app's engine.rs.
// Never part of a build: Vite only uses it in its dev server.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { homedir } from "node:os";
import { join } from "node:path";

export function engineBridge() {
  return {
    name: "stenny-engine-bridge",
    apply: "serve",
    configureServer(server) {
      const exe = process.env.STENNY_CLI || join(homedir(), ".local/bin/stenny");
      const engine = spawn(exe, ["app-server"], { stdio: ["pipe", "pipe", "inherit"] });
      const waiting = new Map();
      let next = 0;
      createInterface({ input: engine.stdout }).on("line", (line) => {
        try {
          const reply = JSON.parse(line);
          waiting.get(reply.id)?.(reply);
          waiting.delete(reply.id);
        } catch {}
      });
      server.httpServer?.on("close", () => engine.kill());
      server.middlewares.use("/__engine", (req, res) => {
        let body = "";
        req.on("data", (chunk) => (body += chunk));
        req.on("end", () => {
          const { method, params } = JSON.parse(body || "{}");
          const id = ++next;
          waiting.set(id, (reply) => {
            res.setHeader("Content-Type", "application/json");
            res.end(JSON.stringify(reply));
          });
          engine.stdin.write(JSON.stringify({ id, method, params }) + "\n");
        });
      });
    },
  };
}
