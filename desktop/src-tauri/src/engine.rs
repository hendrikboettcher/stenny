//! The bridge to Stenny's engine: one `stenny app-server` process (src/stenny/app_server.py), started on the
//! first request and restarted if it ever exits. Requests and replies are JSON lines; replies carry the id of
//! their request, so several windows can ask at once.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
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
}

/// `stenny` as installed by `uv tool install` (~/.local/bin), unless $STENNY_CLI points at another one
/// (a development checkout's `.venv/bin/stenny`). Later the app bundles its own.
fn stenny_command() -> PathBuf {
    if let Ok(path) = std::env::var("STENNY_CLI") {
        return PathBuf::from(path);
    }
    let home = std::env::var("HOME").unwrap_or_default();
    let installed = PathBuf::from(home).join(".local/bin/stenny");
    if installed.exists() { installed } else { PathBuf::from("stenny") }
}

fn start() -> Result<Running, String> {
    let exe = stenny_command();
    let mut child = Command::new(&exe)
        .arg("app-server")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("Stenny's engine couldn't be started ({}): {e}", exe.display()))?;
    let stdin = child.stdin.take().ok_or("no stdin")?;
    let stdout = child.stdout.take().ok_or("no stdout")?;
    let pending: Pending = Arc::default();
    let replies = pending.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
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
            if !alive {
                *running = Some(start()?);
            }
            let r = running.as_mut().unwrap();
            r.pending.lock().unwrap().insert(id, tx);
            let line = json!({"id": id, "method": method, "params": params}).to_string() + "\n";
            if let Err(e) = r.stdin.write_all(line.as_bytes()).and_then(|_| r.stdin.flush()) {
                *running = None;
                return Err(format!("Stenny's engine stopped: {e}"));
            }
        }
        let reply = rx
            .recv_timeout(Duration::from_secs(120))
            .map_err(|_| "Stenny's engine didn't answer.".to_string())?;
        match reply.get("error").and_then(Value::as_str) {
            Some(message) => Err(message.to_string()),
            None => Ok(reply.get("result").cloned().unwrap_or(Value::Null)),
        }
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        if let Some(mut r) = self.running.lock().unwrap().take() {
            let _ = r.child.kill();
        }
    }
}
