//! Check for Updates… through Tauri's updater (#92): the newest release's `latest.json` on GitHub
//! (tauri.conf.json, `plugins.updater`), its download checked against Stenny's updater key, then installed, and
//! Stenny opens again by itself. App and engine are one now (the engine is inside the app), so this updates both.
//!
//! - **Never in the middle of a recording or a final pass** (the engine's `update_blocked`): they run from the
//!   files an update replaces. Asked before the download and again after it; if one started meanwhile, the update
//!   waits until it's done.
//! - **macOS: in place.** The plugin would swap the whole Stenny.app for the new one, which leaves the Dock's tile
//!   pointing at a bundle that's gone (two Stenny icons, see desktop/scripts/install-mac.sh). So the download is
//!   unpacked next to the app, checked (`codesign --verify`), and copied into the installed bundle with rsync, as
//!   install-mac.sh does; then the app restarts through LaunchServices.
//! - **Windows: the setup program**, as the plugin runs it (passive: a progress window, no questions), after the
//!   engine is stopped (Windows won't replace a running python.exe); it opens Stenny again when it's done.
//! - **Until every release has a `latest.json`** (the first releases with the updater still come through the
//!   install line), anything the updater can't do falls back to the way before it: the engine's version check
//!   and the install line in a window of its own (menubar::check_updates).

use crate::engine::Engine;
use crate::i18n::tr;
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::{Update, UpdaterExt};

/// An update is being downloaded or waits to be installed: a second Check for Updates… only says so.
static UPDATING: AtomicBool = AtomicBool::new(false);

fn engine(app: &AppHandle) -> Arc<Engine> {
    app.state::<Arc<Engine>>().inner().clone()
}

fn alert(app: &AppHandle, title: &str, message: &str, kind: MessageDialogKind) {
    app.dialog().message(message).title(title).kind(kind).blocking_show();
}

/// "Check for Updates…" in the library window's footer (lib.rs, `check_updates`), on a thread that may wait.
pub fn check_updates(app: &AppHandle) {
    if UPDATING.load(Ordering::SeqCst) {
        alert(app, &tr!("updates.downloading.title"), &tr!("updates.alreadyDownloading"), MessageDialogKind::Info);
        return;
    }
    let update = match check(app) {
        Ok(update) => update,
        Err(e) => {
            // no latest.json yet (a release from before the updater, or its Windows part still being built), no
            // internet: the engine asks GitHub the old way, and says so if that fails too
            eprintln!("updater: {e}; checking the way before the updater");
            return crate::menubar::check_updates(app);
        }
    };
    let current = app.package_info().version.to_string();
    let Some(update) = update else {
        #[cfg(target_os = "macos")]
        crate::macos::centered_alert(app, tr!("updates.upToDate.title"), tr!("updates.upToDate", version = current));
        #[cfg(not(target_os = "macos"))]
        alert(app, &tr!("updates.upToDate.title"), &tr!("updates.upToDate", version = current), MessageDialogKind::Info);
        return;
    };
    let go = app
        .dialog()
        .message(tr!("updates.available", version = current))
        .title(tr!("updates.available.title", version = update.version))
        .buttons(MessageDialogButtons::OkCancelCustom(tr!("updates.updateNow"), tr!("updates.notNow")))
        .blocking_show();
    if !go {
        return;
    }
    if let Some(reason) = blocked(app) {
        alert(app, &tr!("updates.available.title", version = update.version), &reason, MessageDialogKind::Info);
        return;
    }
    if UPDATING.swap(true, Ordering::SeqCst) {
        return;
    }
    // what happens next, without waiting for an answer: the download takes a few minutes
    app.dialog()
        .message(tr!("updates.downloading", version = update.version))
        .title(tr!("updates.downloading.title"))
        .show(|_| {});
    let app = app.clone();
    std::thread::spawn(move || {
        if let Err(e) = download_and_install(&app, &update) {
            UPDATING.store(false, Ordering::SeqCst);
            eprintln!("updater: {e}");
            alert(&app, &tr!("updates.failed"), &tr!("updates.installFailed", error = e), MessageDialogKind::Warning);
            // the install line still works, whatever went wrong here
            if let Err(e) = crate::menubar::open_installer() {
                eprintln!("updater: the install line didn't open either: {e}");
            }
        }
    });
}

fn check(app: &AppHandle) -> Result<Option<Update>, String> {
    let engine = engine(app);
    let updater = app
        .updater_builder()
        // Windows: the plugin ends Stenny right after starting the setup program; the engine must not outlive it
        .on_before_exit(move || engine.stop())
        .build()
        .map_err(|e| e.to_string())?;
    tauri::async_runtime::block_on(updater.check()).map_err(|e| e.to_string())
}

/// Why an update has to wait now, if it does: a recording or a final pass. When the engine can't say, it waits
/// too: replacing what a recording runs from would cost the lecture.
fn blocked(app: &AppHandle) -> Option<String> {
    match engine(app).call("update_blocked", json!({})) {
        Ok(v) if v.get("recording").and_then(Value::as_bool) == Some(true) => Some(tr!("updates.recording")),
        Ok(v) if v.get("final_pass").and_then(Value::as_bool) == Some(true) => Some(tr!("updates.finalPass")),
        Ok(_) => None,
        Err(e) => Some(e),
    }
}

fn download_and_install(app: &AppHandle, update: &Update) -> Result<(), String> {
    // the plugin checks the download against Stenny's updater key before handing it over
    let bytes = tauri::async_runtime::block_on(update.download(|_, _| {}, || {})).map_err(|e| e.to_string())?;
    // a recording (or its final pass) may have started during the download: install once it's done
    while blocked(app).is_some() {
        std::thread::sleep(Duration::from_secs(30));
    }
    install(app, update, &bytes)
}

/// Runs a command to its end; its error output as the error.
#[cfg(target_os = "macos")]
fn run(program: &str, args: &[&std::ffi::OsStr]) -> Result<(), String> {
    let out = std::process::Command::new(program).args(args).output().map_err(|e| format!("{program}: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!("{program}: {}", String::from_utf8_lossy(&out.stderr).trim()))
    }
}

/// macOS: unpacks the update next to the installed Stenny.app, checks its signature, copies it into the installed
/// bundle in place (the Dock's tile keeps pointing at it) and restarts.
#[cfg(target_os = "macos")]
fn install(app: &AppHandle, _update: &Update, bytes: &[u8]) -> Result<(), String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    // …/Stenny.app/Contents/MacOS/stenny-desktop
    let bundle = exe
        .parent()
        .and_then(|d| d.parent())
        .and_then(|d| d.parent())
        .filter(|b| b.extension() == Some(std::ffi::OsStr::new("app")))
        .ok_or_else(|| format!("{} isn't inside an app", exe.display()))?
        .to_path_buf();
    // nothing may run from the old files while they're replaced
    if let Err(e) = install_in_place(&bundle, bytes, || engine(app).stop()) {
        engine(app).reopen();
        return Err(e);
    }
    app.restart()
}

/// The update (`bytes`: Stenny.app.tar.gz, already checked against the updater key) unpacked next to `bundle`,
/// its code signature checked, then copied into `bundle` with rsync: the same folder, so the Dock's tile, Login
/// Items and Launch Services keep knowing it. `before_copy` runs once everything is checked, before the first file
/// is replaced.
#[cfg(target_os = "macos")]
fn install_in_place(bundle: &std::path::Path, bytes: &[u8], before_copy: impl FnOnce()) -> Result<(), String> {
    use std::ffi::OsStr;
    let staging = bundle
        .parent()
        .ok_or("no folder around the app")?
        .join(format!(".Stenny-update-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir(&staging).map_err(|e| format!("{}: {e}", staging.display()))?;
    let result = (|| {
        let archive = staging.join("update.tar.gz");
        std::fs::write(&archive, bytes).map_err(|e| e.to_string())?;
        run("/usr/bin/tar", &[OsStr::new("-xzf"), archive.as_os_str(), OsStr::new("-C"), staging.as_os_str()])?;
        let new = staging.join("Stenny.app");
        if !new.join("Contents/Info.plist").is_file() {
            return Err("the download has no Stenny.app".to_string());
        }
        // whole and signed as released (app/release.sh, scripts/sign-mac.sh), before anything is replaced
        run("/usr/bin/codesign", &[OsStr::new("--verify"), OsStr::new("--deep"), OsStr::new("--strict"), new.as_os_str()])?;
        before_copy();
        // --checksum: by content, not size and time (a re-signed program can keep both and still be another)
        let (from, to) = (format!("{}/", new.display()), format!("{}/", bundle.display()));
        let args = [OsStr::new("-a"), OsStr::new("--checksum"), OsStr::new("--delete"), OsStr::new(&from), OsStr::new(&to)];
        run("/usr/bin/rsync", &args)?;
        let _ = run("/usr/bin/touch", &[bundle.as_os_str()]);
        let lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister";
        let _ = run(lsregister, &[OsStr::new("-f"), bundle.as_os_str()]);
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&staging);
    result
}

/// Windows: the setup program, run by the plugin without questions; it ends Stenny (the engine first, see check)
/// and opens it again when it's done.
#[cfg(windows)]
fn install(app: &AppHandle, update: &Update, bytes: &[u8]) -> Result<(), String> {
    engine(app).stop();
    // on success the plugin ends Stenny here; only a setup program that didn't start comes back
    update.install(bytes).map_err(|e| {
        engine(app).reopen();
        e.to_string()
    })
}

#[cfg(not(any(target_os = "macos", windows)))]
fn install(_app: &AppHandle, _update: &Update, _bytes: &[u8]) -> Result<(), String> {
    Err("no updates for this system".into())
}

#[cfg(test)]
mod key_tests {
    use base64::Engine as _;

    /// The public key in tauri.conf.json must belong to the private key releases are signed with (app/release.sh,
    /// CI's secret), or no installed Stenny could ever update: updater-key-check.txt was signed with that key.
    #[test]
    fn the_public_key_matches_the_release_key() {
        let conf: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let updater = &conf["plugins"]["updater"];
        assert_eq!(updater["requireSignedVersion"], true);
        let b64 = base64::engine::general_purpose::STANDARD;
        let decode = |text: &str| String::from_utf8(b64.decode(text.trim()).unwrap()).unwrap();
        let key = minisign_verify::PublicKey::decode(&decode(updater["pubkey"].as_str().unwrap())).unwrap();
        let sig = minisign_verify::Signature::decode(&decode(include_str!("../updater-key-check.txt.sig"))).unwrap();
        key.verify(include_bytes!("../updater-key-check.txt"), &sig, true).expect("tauri.conf.json's pubkey");
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;
    use std::os::unix::fs::MetadataExt;
    use std::path::Path;
    use std::process::Command;

    /// A tiny app: Info.plist, a program (a copy of /usr/bin/true) and a file, signed ad hoc like a dev build.
    fn make_app(at: &Path, version: &str, extra: &str) {
        std::fs::create_dir_all(at.join("Contents/MacOS")).unwrap();
        std::fs::write(
            at.join("Contents/Info.plist"),
            format!("<?xml version=\"1.0\"?><plist version=\"1.0\"><dict><key>CFBundleExecutable</key><string>stenny-desktop</string><key>CFBundleIdentifier</key><string>eu.stenny.test</string><key>CFBundleShortVersionString</key><string>{version}</string></dict></plist>"),
        )
        .unwrap();
        std::fs::copy("/usr/bin/true", at.join("Contents/MacOS/stenny-desktop")).unwrap();
        std::fs::create_dir_all(at.join("Contents/Resources")).unwrap();
        std::fs::write(at.join("Contents/Resources").join(extra), version).unwrap();
        let ok = Command::new("/usr/bin/codesign").args(["--force", "--deep", "--sign", "-"]).arg(at).status().unwrap();
        assert!(ok.success());
    }

    #[test]
    fn the_update_goes_into_the_same_bundle_folder() {
        let root = std::env::temp_dir().join(format!("stenny-updater-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let (installed, built) = (root.join("Applications/Stenny.app"), root.join("build/Stenny.app"));
        make_app(&installed, "0.5.11", "old.txt");
        make_app(&built, "0.5.12", "new.txt");
        let tar = root.join("Stenny.app.tar.gz");
        let ok = Command::new("/usr/bin/tar")
            .env("COPYFILE_DISABLE", "1")
            .arg("-czf")
            .arg(&tar)
            .arg("-C")
            .arg(root.join("build"))
            .arg("Stenny.app")
            .status()
            .unwrap();
        assert!(ok.success());
        let inode = std::fs::metadata(&installed).unwrap().ino();
        let mut stopped = false;
        install_in_place(&installed, &std::fs::read(&tar).unwrap(), || stopped = true).unwrap();
        assert!(stopped);
        assert_eq!(std::fs::metadata(&installed).unwrap().ino(), inode, "the bundle folder must stay the same one");
        assert_eq!(std::fs::read_to_string(installed.join("Contents/Resources/new.txt")).unwrap(), "0.5.12");
        assert!(!installed.join("Contents/Resources/old.txt").exists(), "files the new version lacks go");
        let verify = Command::new("/usr/bin/codesign").args(["--verify", "--deep", "--strict"]).arg(&installed).status().unwrap();
        assert!(verify.success(), "the installed app is signed as released");
        let leftovers: Vec<_> = std::fs::read_dir(root.join("Applications")).unwrap().flatten().map(|e| e.file_name()).collect();
        assert_eq!(leftovers, vec![std::ffi::OsString::from("Stenny.app")], "no staging folder left behind");

        // a download that isn't a whole signed app changes nothing
        std::fs::write(built.join("Contents/Resources/new.txt"), "tampered").unwrap(); // breaks the seal
        let bad = root.join("bad.tar.gz");
        Command::new("/usr/bin/tar").arg("-czf").arg(&bad).arg("-C").arg(root.join("build")).arg("Stenny.app").status().unwrap();
        let mut stopped = false;
        assert!(install_in_place(&installed, &std::fs::read(&bad).unwrap(), || stopped = true).is_err());
        assert!(!stopped);
        assert_eq!(std::fs::read_to_string(installed.join("Contents/Resources/new.txt")).unwrap(), "0.5.12");
        let _ = std::fs::remove_dir_all(&root);
    }
}
