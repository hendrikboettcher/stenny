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

function candidates() {
  const out = [];
  const chosen = (process.env.STENNY_COMMAND || "").trim();
  if (chosen && !chosen.startsWith("${")) out.push(chosen); // "${user_config…}": the field was left empty
  out.push(path.join(os.homedir(), ".local", "bin", exe)); // `uv tool install` (Stenny's setup)
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (dir) out.push(path.join(dir, exe));
  }
  return [...new Set(out)];
}

function hasConnector(command) {
  // older Stennys have no `mcp` command; asking for the config entry is quick and changes nothing
  const done = spawnSync(command, ["mcp", "--desktop-config"], { encoding: "utf8", timeout: 30000 });
  return done.status === 0;
}

const found = candidates().filter((c) => fs.existsSync(c));
const command = found.find(hasConnector);
if (!command) {
  process.stderr.write(
    found.length
      ? `Stenny (${found.join(", ")}) is too old for the Claude connector: update Stenny (stenny update).\n`
      : "Stenny isn't installed on this computer (no `stenny` command found). Install Stenny first: https://stenny.eu\n"
  );
  process.exit(1);
}

const child = spawn(command, ["mcp"], { stdio: "inherit" });
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => child.kill(signal));
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
child.on("error", (e) => {
  process.stderr.write(`Couldn't start ${command}: ${e.message}\n`);
  process.exit(1);
});
