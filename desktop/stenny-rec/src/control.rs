//! Stopping, the Windows way. The Swift recorder stops on SIGTERM; Windows has no signal a process can catch from
//! another one (Python's terminate() is TerminateProcess, which ends it mid-write). So the engine starts this one
//! with a pipe on stdin and writes `stop` to it: the capture stops, everything is written, then it exits.
//!
//! The pipe also tells when the process that started us is gone (it closes without a `stop`): the Swift recorder
//! sees that as its parent turning into launchd. Like there, the recording goes on for a while, so the lecture isn't
//! cut while Stenny (`stenny recover`) takes over, and then stops: the microphone must not stay on unnoticed.

use std::io::BufRead;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const ORPHAN_LIMIT: Duration = Duration::from_secs(300);

#[derive(Default)]
pub struct Control {
    stop: AtomicBool,
    orphaned_since: Mutex<Option<Instant>>,
}

impl Control {
    pub fn request_stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    pub fn stop_requested(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }

    pub fn orphaned(&self, at: Instant) {
        self.orphaned_since.lock().unwrap().get_or_insert(at);
    }

    /// Nobody took over for `ORPHAN_LIMIT`: time to stop.
    pub fn orphan_expired(&self, now: Instant) -> bool {
        self.orphaned_since.lock().unwrap().is_some_and(|since| now.duration_since(since) > ORPHAN_LIMIT)
    }
}

/// Reads stdin until `stop` or its end. `eof_means_orphaned`: stdin is a pipe from the engine, so its end means the
/// engine is gone (a console or NUL ends for other reasons).
pub fn watch_input(input: impl BufRead, control: &Control, eof_means_orphaned: bool) {
    for line in input.lines() {
        match line {
            Ok(l) if l.trim() == "stop" => {
                control.request_stop();
                return;
            }
            Ok(_) => continue,
            Err(_) => break,
        }
    }
    if eof_means_orphaned {
        control.orphaned(Instant::now());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn stop_line_stops() {
        let c = Control::default();
        watch_input(Cursor::new("hello\r\nstop\r\n"), &c, true);
        assert!(c.stop_requested());
        assert!(!c.orphan_expired(Instant::now() + ORPHAN_LIMIT * 2));
    }

    #[test]
    fn a_closed_pipe_means_the_engine_is_gone() {
        let c = Control::default();
        watch_input(Cursor::new(""), &c, true);
        assert!(!c.stop_requested());
        let now = Instant::now();
        assert!(!c.orphan_expired(now + Duration::from_secs(10)));
        assert!(c.orphan_expired(now + ORPHAN_LIMIT + Duration::from_secs(1)));
    }

    #[test]
    fn a_console_that_ends_is_not_an_orphan() {
        let c = Control::default();
        watch_input(Cursor::new(""), &c, false);
        assert!(!c.stop_requested() && !c.orphan_expired(Instant::now() + ORPHAN_LIMIT * 2));
    }
}
