// Starts `stenny mcp` (the connector, see src/stenny/mcp_server.py) for Claude Desktop and hands it stdin/stdout.
// The connector lives in the Stenny installed on this computer, so it reads the same lectures and config as the
// app. This file only finds that Stenny; it runs on the Node.js built into Claude Desktop (macOS and Windows).
//
// Which Stenny, first that works:
//   1. `[connector] stenny = "…"` in Stenny's config: only for developers (a development checkout); users never set it
//   2. the engine inside the Stenny app (#92): its own Python, run as `python -m stenny`, the same engine the app uses
//      (Stenny.app/Contents/Resources/engine on a Mac, engine\ next to Stenny.exe on Windows)
//   3. a `stenny` command: what older setups installed with `uv tool install` (~/.local/bin), or one on the PATH
// `node index.js --candidates` prints them (for tests and troubleshooting) without starting any.
"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const windows = process.platform === "win32";
const ENGINE_INFO = "stenny-engine.json"; // marks the app's engine (src/stenny/paths.py, ENGINE_INFO)

// A way to start Stenny: `program` with `args` before Stenny's own arguments ("mcp", "where" …); `app` for the
// engine inside the app.
const command = (program, args = [], app = false) => ({ program, args, app });

// The engine inside the app needs none of another Python's settings, and must never write a .pyc into the app (its
// files come with theirs, and writing there would break the app's signature): as the app starts it (engine.rs).
function env(c) {
  const out = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
  if (c.app) {
    delete out.PYTHONHOME;
    delete out.PYTHONPATH;
    out.PYTHONDONTWRITEBYTECODE = "1";
  }
  return out;
}

function runSync(c, args) {
  return spawnSync(c.program, [...c.args, ...args], { encoding: "utf8", timeout: 30000, env: env(c), windowsHide: true });
}

// Windows: where the app may be installed, most likely first, like paths.app_dirs_windows(): the folder its
// installer recorded (Software\stenny\Stenny: the setup program writes the default value, the .msi "InstallDir"),
// then the setup program's default for one user and the .msi's for everyone.
function windowsAppDirs() {
  const out = [];
  for (const root of ["HKCU", "HKLM"]) {
    for (const value of [["/ve"], ["/v", "InstallDir"]]) {
      const done = spawnSync("reg", ["query", `${root}\\Software\\stenny\\Stenny`, ...value],
                             { encoding: "utf8", timeout: 10000, windowsHide: true });
      const m = done.status === 0 && (done.stdout || "").match(/REG_(?:EXPAND_)?SZ[ \t]+([^\r\n]+)/);
      if (m && m[1].trim()) out.push(m[1].trim().replace(/^"|"$/g, ""));
    }
  }
  out.push(path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "Stenny"));
  out.push(path.join(process.env.ProgramFiles || "C:\\Program Files", "Stenny"));
  return out;
}

function appEngines() {
  const engines = windows
    ? windowsAppDirs().map((d) => path.join(d, "engine"))
    : [path.join(os.homedir(), "Applications"), "/Applications"].map((apps) =>
        path.join(apps, "Stenny.app", "Contents", "Resources", "engine"));
  return engines
    .filter((e) => fs.existsSync(path.join(e, ENGINE_INFO)))
    .map((e) => command(windows ? path.join(e, "python.exe") : path.join(e, "bin", "python3"), ["-m", "stenny"], true))
    .filter((c) => fs.existsSync(c.program));
}

// `stenny` commands: ~/.local/bin (`uv tool install`, older setups; on a Mac also the launcher the app writes, which
// runs the app's engine), then the PATH. On Windows only stenny.exe: the app's launcher there is stenny.cmd, which
// can't be started without a shell, and the app's engine is found above anyway.
function installed() {
  const exe = windows ? "stenny.exe" : "stenny";
  const out = [path.join(os.homedir(), ".local", "bin", exe)];
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (dir) out.push(path.join(dir, exe));
  }
  return [...new Set(out)].filter((p) => fs.existsSync(p)).map((p) => command(p));
}

// Stenny's config, for `[connector] stenny`. Where it is, only Stenny knows (`stenny where --json`, see
// src/stenny/paths.py); an older Stenny without `where` has no such setting to read.
function configured(c) {
  let file = process.env.STENNY_CONFIG;
  if (!file) {
    const done = runSync(c, ["where", "--json"]);
    if (done.status !== 0) return null;
    try { file = JSON.parse(done.stdout).config; } catch { return null; }
  }
  let text;
  try { text = fs.readFileSync(file, "utf8"); } catch { return null; }
  let inSection = false;
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (t.startsWith("[")) inSection = t === "[connector]";
    const m = inSection && t.match(/^stenny\s*=\s*"([^"]+)"/);
    if (m) return command(m[1].startsWith("~") ? path.join(os.homedir(), m[1].slice(1)) : m[1]);
  }
  return null;
}

function candidates() {
  const found = [...appEngines(), ...installed()];
  const chosen = found.length ? configured(found[0]) : null;
  const all = chosen && fs.existsSync(chosen.program) ? [chosen, ...found] : found;
  const seen = new Set();
  return all.filter((c) => !seen.has(c.program) && seen.add(c.program));
}

function hasConnector(c) {
  // older Stennys have no `mcp` command; asking for the config entry is quick and changes nothing
  return runSync(c, ["mcp", "--desktop-config"]).status === 0;
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

function run(c) {
  // Pipes, not "inherit": Claude Desktop runs this file in its built-in Node, whose stdin/stdout aren't plain
  // file descriptors a child process could take over.
  const child = spawn(c.program, [...c.args, "mcp"], { stdio: ["pipe", "pipe", "pipe"], env: env(c), windowsHide: true });
  process.stdin.pipe(child.stdin);
  child.stdout.pipe(process.stdout);
  child.stderr.pipe(process.stderr);
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
  child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
  child.on("error", (e) => {
    process.stderr.write(`Couldn't start ${c.program}: ${e.message}\n`);
    process.exit(1);
  });
}

if (process.argv.includes("--candidates")) {
  process.stdout.write(JSON.stringify(candidates()) + "\n");
} else {
  const found = candidates();
  const usable = found.find(hasConnector);
  if (usable) {
    run(usable);
  } else {
    explain(
      found.length
        ? `The Stenny installed here (${found[0].program}) is older than the Claude connector. Update Stenny (Check ` +
            "for Updates… in Stenny's window), then turn the Stenny extension off and on in Claude's Settings."
        : "Stenny isn't installed on this computer. Install it first: https://stenny.eu"
    );
  }
}
