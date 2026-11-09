//! stenny-rec.exe: Stenny's microphone recorder on Windows. Records a microphone to a file as 16 kHz mono 16-bit
//! PCM (s16le), appending, through WASAPI. The Windows twin of the Swift recorder (app/Sources/stenny-rec), with the
//! same command line, the same file and the same messages, so the engine (src/stenny/live/sources.py) runs either.
//!
//!   stenny-rec --out <file.pcm> [--device "<name>"]   stops on `stop` on stdin (or Ctrl+C)
//!   stenny-rec --list                                  prints input device names
//!   stenny-rec --list-json                             … with default / built-in / virtual, for the engine
//!
//! Exit codes, as the Swift one: 0 stopped, 1 couldn't start, 3 the device is gone (the engine reconnects),
//! 4 writing failed (disk full), 5 the engine is gone and nobody took over.
#![cfg_attr(not(windows), allow(dead_code))]

mod args;
mod control;
mod convert;
mod devices;
#[cfg(windows)]
mod wasapi;

pub fn log(message: &str) {
    eprintln!("stenny-rec: {message}");
}

pub fn fail(message: &str) -> ! {
    eprintln!("stenny-rec: error: {message}");
    std::process::exit(1)
}

fn main() {
    let command = args::parse(std::env::args().skip(1)).unwrap_or_else(|e| fail(&e));
    run(command)
}

#[cfg(windows)]
fn run(command: args::Command) {
    wasapi::run(command)
}

#[cfg(not(windows))]
fn run(_: args::Command) {
    fail("this is the Windows recorder; on macOS Stenny records with app/Sources/stenny-rec")
}
