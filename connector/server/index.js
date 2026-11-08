// Starts `stenny mcp` (the connector, see src/stenny/mcp_server.py) for Claude Desktop and hands it stdin/stdout.
// The connector lives in the Stenny installed on this computer, so it reads the same lectures and config as the
// app. This file only finds that `stenny`; it runs on the Node.js built into Claude Desktop (macOS and Windows).
"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const windows = process.platform === "win32";
const exe = windows ? "stenny.exe" : "stenny";

// Stenny's config: only for developers, `[connector] stenny = "…"` runs another copy of Stenny, e.g. a development
// checkout, instead of the installed one. Users never set anything. Where the config is, only Stenny knows
// (`stenny where --json`, see src/stenny/paths.py); an older Stenny without `where` has no such setting to read.
function configFile(command) {
  if (process.env.STENNY_CONFIG) return process.env.STENNY_CONFIG;
  const done = spawnSync(command, ["where", "--json"], { encoding: "utf8", timeout: 30000 });
  if (done.status !== 0) return null;
  try { return JSON.parse(done.stdout).config || null; } catch { return null; }
}

function configured(command) {
  const file = configFile(command);
  let text;
  try { text = fs.readFileSync(file, "utf8"); } catch { return null; }
  let inSection = false;
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (t.startsWith("[")) inSection = t === "[connector]";
    const m = inSection && t.match(/^stenny\s*=\s*"([^"]+)"/);
    if (m) return m[1].startsWith("~") ? path.join(os.homedir(), m[1].slice(1)) : m[1];
  }
  return null;
}

function candidates() {
  const out = [path.join(os.homedir(), ".local", "bin", exe)]; // `uv tool install` (Stenny's setup)
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (dir) out.push(path.join(dir, exe));
  }
  const installed = [...new Set(out)].filter((c) => fs.existsSync(c));
  // the copy the config names, if any, goes first
  const chosen = installed.length ? configured(installed[0]) : null;
  return chosen ? [...new Set([chosen, ...installed])] : installed;
}

function hasConnector(command) {
  // older Stennys have no `mcp` command; asking for the config entry is quick and changes nothing
  const done = spawnSync(command, ["mcp", "--desktop-config"], { encoding: "utf8", timeout: 30000 });
  return done.status === 0;
}

// Without a usable Stenny, Claude Desktop would only say "Unable to connect". Instead, answer as a tiny MCP
// server with one tool that explains what to do, so Claude can tell the student.
function explain(problem) {
  process.stderr.write(problem + "\n");
  const tool = {
    name: "stenny_setup_problem",
    description: "Why the Stenny tools are missing. Call it when asked about lectures, and tell the student.",
    inputSchema: { type: "object", properties: {} },
  };
  const reply = (id, result, error) =>
    process.stdout.write(JSON.stringify(error ? { jsonrpc: "2.0", id, error } : { jsonrpc: "2.0", id, result }) + "\n");
  let buffer = "";
  process.stdin.setEncoding("utf8");
  process.stdin.on("data", (chunk) => {
    buffer += chunk;
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, nl).trim();
      buffer = buffer.slice(nl + 1);
      if (!line) continue;
      let msg;
      try { msg = JSON.parse(line); } catch { continue; }
      if (msg.id === undefined) continue; // notifications
      if (msg.method === "initialize") {
        reply(msg.id, {
          protocolVersion: (msg.params && msg.params.protocolVersion) || "2025-06-18",
          capabilities: { tools: {} },
          serverInfo: { name: "Stenny", version: "0" },
          instructions: `The Stenny connector can't start: ${problem}`,
        });
      } else if (msg.method === "tools/list") {
        reply(msg.id, { tools: [tool] });
      } else if (msg.method === "tools/call") {
        reply(msg.id, { content: [{ type: "text", text: problem }], isError: true });
      } else if (msg.method === "ping") {
        reply(msg.id, {});
      } else {
        reply(msg.id, null, { code: -32601, message: `Method not found: ${msg.method}` });
      }
    }
  });
  process.stdin.on("end", () => process.exit(0));
}

const found = candidates().filter((c) => fs.existsSync(c));
const command = found.find(hasConnector);
if (!command) {
  explain(
    found.length
      ? `The Stenny installed here (${found[0]}) is older than the Claude connector. Update Stenny (in Stenny's ` +
          "window, or `stenny update` in Terminal), then turn the Stenny extension off and on in Claude's Settings."
      : "Stenny isn't installed on this computer (no `stenny` command found). Install it first: https://stenny.eu"
  );
} else {
  run(command);
}

function run(command) {
  // Pipes, not "inherit": Claude Desktop runs this file in its built-in Node, whose stdin/stdout aren't plain
  // file descriptors a child process could take over.
  const child = spawn(command, ["mcp"], { stdio: ["pipe", "pipe", "pipe"] });
  process.stdin.pipe(child.stdin);
  child.stdout.pipe(process.stdout);
  child.stderr.pipe(process.stderr);
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
  child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
  child.on("error", (e) => {
    process.stderr.write(`Couldn't start ${command}: ${e.message}\n`);
    process.exit(1);
  });
}
