//! The command line, the same as the Swift stenny-rec's (app/Sources/stenny-rec/main.swift):
//!
//!   stenny-rec --out <file.pcm> [--device "<name>"]   records until stopped
//!   stenny-rec --list                                  prints input device names, one per line
//!
//! plus `--list-json`, which only this one has: what the engine needs to know about each microphone on Windows
//! (default, built in, virtual), where macOS answers with system_profiler.

#[derive(Debug, PartialEq)]
pub enum Command {
    List,
    ListJson,
    /// `device` None or "default": the system's default input, followed when it changes
    Record { out: String, device: Option<String> },
}

pub fn parse(args: impl IntoIterator<Item = String>) -> Result<Command, String> {
    let mut out = None;
    let mut device = None;
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            // like the Swift one: listing ends the parsing, whatever follows
            "--list" => return Ok(Command::List),
            "--list-json" => return Ok(Command::ListJson),
            "--out" => out = args.next(),
            "--device" => device = args.next(),
            _ => return Err(format!("unknown argument {arg}")),
        }
    }
    let out = out.ok_or("--out <file> is required")?;
    Ok(Command::Record { out, device: device.filter(|d| d != "default") })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(args: &[&str]) -> Result<Command, String> {
        parse(args.iter().map(|a| a.to_string()))
    }

    #[test]
    fn records_to_a_file_from_a_named_device() {
        assert_eq!(
            p(&["--out", r"C:\Users\a\x.live.pcm", "--device", "Microphone (USB Audio)"]),
            Ok(Command::Record { out: r"C:\Users\a\x.live.pcm".into(), device: Some("Microphone (USB Audio)".into()) })
        );
    }

    #[test]
    fn default_means_the_system_default() {
        assert_eq!(p(&["--out", "x.pcm", "--device", "default"]), Ok(Command::Record { out: "x.pcm".into(), device: None }));
        assert_eq!(p(&["--out", "x.pcm"]), Ok(Command::Record { out: "x.pcm".into(), device: None }));
    }

    #[test]
    fn listing() {
        assert_eq!(p(&["--list"]), Ok(Command::List));
        assert_eq!(p(&["--list", "--whatever"]), Ok(Command::List));
        assert_eq!(p(&["--list-json"]), Ok(Command::ListJson));
    }

    #[test]
    fn errors_say_what_is_wrong() {
        assert_eq!(p(&[]), Err("--out <file> is required".into()));
        assert_eq!(p(&["--out"]), Err("--out <file> is required".into()));
        assert_eq!(p(&["--out", "x.pcm", "--loud"]), Err("unknown argument --loud".into()));
    }
}
