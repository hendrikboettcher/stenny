//! The bridge to Stenny's engine: one `stenny app-server` process (src/stenny/app_server.py), started on the
//! first request and restarted if it ever exits. Requests and replies are JSON lines; replies carry the id of
//! their request, so several windows can ask at once.

use crate::i18n::{self, tr};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

type Pending = Arc<Mutex<HashMap<u64, mpsc::Sender<Value>>>>;

struct Running {
    child: Child,
    stdin: ChildStdin,
    pending: Pending,
}

#[derive(Default)]
pub struct Engine {
    running: Mutex<Option<Running>>,
    next_id: AtomicU64,
    /// An update is replacing the engine's files (src/updater.rs): no new engine until it's done or failed.
    closed: AtomicBool,
}

/// The engine inside this app (#92, desktop/scripts/bundle-engine.sh): its own Python, Contents/Resources/engine on
/// macOS, engine\ next to Stenny.exe on Windows. None for a build without it (a developer's `tauri dev`).
fn bundled_python() -> Option<PathBuf> {
    bundled_python_next_to(std::env::current_exe().ok()?.parent()?)
}

/// The engine's Python for the app program in `dir` (Contents/MacOS, or the folder with Stenny.exe).
fn bundled_python_next_to(dir: &std::path::Path) -> Option<PathBuf> {
    // Contents/Resources, not MacOS/../Resources: other programs get this path to start the engine with (paths.py)
    let engine = if cfg!(windows) { dir.join("engine") } else { dir.parent()?.join("Resources/engine") };
    let python = if cfg!(windows) { engine.join("python.exe") } else { engine.join("bin").join("python3") };
    // stenny-engine.json says it's a whole engine, not the placeholder CI compiles with
    (python.is_file() && engine.join("stenny-engine.json").is_file()).then_some(python)
}

/// How to start the engine, first found wins: $STENNY_CLI (a development checkout's `.venv/bin/stenny`), the engine
/// inside this app (`python -m stenny`), else `stenny` as installed by `uv tool install` (~/.local/bin; on Windows
/// %USERPROFILE%\.local\bin\stenny.exe) for installs from before the engine came with the app.
fn stenny_command() -> (PathBuf, Vec<&'static str>) {
    if let Ok(path) = std::env::var("STENNY_CLI") {
        return (PathBuf::from(path), vec![]);
    }
    if let Some(python) = bundled_python() {
        return (python, vec!["-m", "stenny"]);
    }
    // Windows has no $HOME as a rule; uv's own tool folder is under the user's profile there
    let (home, program) = if cfg!(windows) { ("USERPROFILE", "stenny.exe") } else { ("HOME", "stenny") };
    let installed = PathBuf::from(std::env::var_os(home).unwrap_or_default()).join(".local").join("bin").join(program);
    (if installed.exists() { installed } else { PathBuf::from("stenny") }, vec![])
}

fn start() -> Result<Running, String> {
    let (exe, args) = stenny_command();
    let mut command = Command::new(&exe);
    command.args(&args);
    if !args.is_empty() {
        // the app's own Python: none of another Python's settings, and never a .pyc written into the app (its files
        // come with theirs, and writing there would break the app's signature)
        command.env_remove("PYTHONHOME").env_remove("PYTHONPATH").env("PYTHONDONTWRITEBYTECODE", "1");
    }
    // the recorder bundled with this app (Contents/MacOS; next to Stenny.exe on Windows: stenny-rec.exe, WASAPI),
    // not another Stenny's; `stenny live` reads $STENNY_REC
    let recorder = if cfg!(windows) { "stenny-rec.exe" } else { "stenny-rec" };
    if let Some(rec) = std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.join(recorder))) {
        if rec.exists() && std::env::var_os("STENNY_REC").is_none() {
            command.env("STENNY_REC", rec);
        }
    }
    // the connector bundled with this app (Contents/Resources on macOS, next to the program on Windows), so
    // Settings ▸ Use with Claude can install it again whatever folder Stenny's code is in (claude_setup.py)
    if let Some(dir) = std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.to_path_buf())) {
        let bundled = [dir.join("../Resources/Stenny.mcpb"), dir.join("Stenny.mcpb")];
        if let Some(file) = bundled.into_iter().find(|f| f.exists()) {
            command.env("STENNY_CONNECTOR", file);
        }
    }
    // "System default" in Settings: the language the system prefers, as Rust sees it (src/stenny/i18n.py)
    command.env("STENNY_SYSTEM_LANGUAGE", i18n::system_language());
    // UTF-8 for the engine's pipes and files on every system: Python on Windows would use the ANSI code page
    // (cp1252), and the replies below are read as UTF-8 (app_server.serve sets its pipes to UTF-8 too)
    command.env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8");
    // Windows: the engine is a console program; started from Stenny it would open a console window of its own
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command
        .arg("app-server")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| tr!("error.engine", path = exe.display(), error = e))?;
    let stdin = child.stdin.take().ok_or("no stdin")?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let pending: Pending = Arc::default();
    let replies = pending.clone();
    std::thread::spawn(move || {
        // byte lines, read leniently: one reply that isn't valid UTF-8 mustn't end the reading (`lines()` would stop
        // there for good while the engine runs on, and every request after it waited in vain)
        let mut reader = BufReader::new(stdout);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match reader.read_until(b'\n', &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let line = String::from_utf8_lossy(&buf);
            let Ok(reply) = serde_json::from_str::<Value>(&line) else { continue };
            let Some(id) = reply.get("id").and_then(Value::as_u64) else { continue };
            if let Some(tx) = replies.lock().unwrap().remove(&id) {
                let _ = tx.send(reply);
            }
        }
        replies.lock().unwrap().clear(); // the engine stopped: whoever still waits gets an error
    });
    Ok(Running { child, stdin, pending })
}

impl Engine {
    /// Sends one request and waits for its reply: the result, or the engine's message as the error.
    pub fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed) + 1;
        let (tx, rx) = mpsc::channel();
        {
            let mut running = self.running.lock().unwrap();
            let alive = matches!(running.as_mut().map(|r| r.child.try_wait()), Some(Ok(None)));
            if !alive && self.closed.load(Ordering::SeqCst) {
                return Err(tr!("updates.installing"));
            }
            if !alive {
                *running = Some(start()?);
            }
            let r = running.as_mut().unwrap();
            r.pending.lock().unwrap().insert(id, tx);
            let line = json!({"id": id, "method": method, "params": params}).to_string() + "\n";
            if let Err(e) = r.stdin.write_all(line.as_bytes()).and_then(|_| r.stdin.flush()) {
                *running = None;
                return Err(tr!("error.engineStopped", error = e));
            }
        }
        let reply = rx
            .recv_timeout(Duration::from_secs(120))
            .map_err(|_| tr!("error.engineSilent"))?;
        match reply.get("error").and_then(Value::as_str) {
            Some(message) => Err(message.to_string()),
            None => Ok(reply.get("result").cloned().unwrap_or(Value::Null)),
        }
    }
}

impl Engine {
    /// Ends the engine process now and starts no new one (the updater, before the files it runs from are
    /// replaced): requests get "installing the update" until `reopen`.
    pub fn stop(&self) {
        self.closed.store(true, Ordering::SeqCst);
        if let Some(mut r) = self.running.lock().unwrap().take() {
            let _ = r.child.kill();
            let _ = r.child.wait();
        }
    }

    /// The update didn't install: the next request starts the engine again.
    pub fn reopen(&self) {
        self.closed.store(false, Ordering::SeqCst);
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        if let Some(mut r) = self.running.lock().unwrap().take() {
            let _ = r.child.kill();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_engine_inside_the_app_is_found_but_not_a_placeholder() {
        let app = std::env::temp_dir().join(format!("stenny-engine-test-{}", std::process::id()));
        let (program, engine) = if cfg!(windows) {
            (app.clone(), app.join("engine"))
        } else {
            (app.join("Contents/MacOS"), app.join("Contents/Resources/engine"))
        };
        let python = if cfg!(windows) { engine.join("python.exe") } else { engine.join("bin/python3") };
        std::fs::create_dir_all(python.parent().unwrap()).unwrap();
        std::fs::create_dir_all(&program).unwrap();
        std::fs::write(engine.join("placeholder.txt"), "").unwrap();
        assert_eq!(bundled_python_next_to(&program), None); // CI's placeholder: no engine
        std::fs::write(&python, "").unwrap();
        assert_eq!(bundled_python_next_to(&program), None); // no stenny-engine.json: not a whole engine
        std::fs::write(engine.join("stenny-engine.json"), "{}").unwrap();
        let found = bundled_python_next_to(&program).unwrap();
        assert_eq!(found.canonicalize().unwrap(), python.canonicalize().unwrap());
        let _ = std::fs::remove_dir_all(&app);
    }
}
