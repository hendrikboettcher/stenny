//! stenny-rec.exe's version details on Windows: product name "Stenny" and the app's version (desktop/src-tauri/
//! tauri.conf.json), the same as Stenny.exe's. Windows shows them in the file's properties, and code signing checks
//! them: SignPath only signs files whose product name and version are the project's (desktop/README.md, "Code
//! signing"). Nothing to do when building for macOS, where this crate is a stub for its tests.

use std::{env, fs, path::Path};

fn main() {
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let conf = Path::new(&env::var("CARGO_MANIFEST_DIR").unwrap()).join("../src-tauri/tauri.conf.json");
    println!("cargo:rerun-if-changed={}", conf.display());
    let text = fs::read_to_string(&conf).expect("desktop/src-tauri/tauri.conf.json");
    let version = app_version(&text).expect("a \"version\": \"x.y.z\" in tauri.conf.json");

    let mut numbers = version.split('.').map(|n| n.parse::<u64>().unwrap_or(0));
    let packed = (0..3).fold(0, |v, i| v | numbers.next().unwrap_or(0) << (48 - 16 * i));

    let mut res = winresource::WindowsResource::new();
    res.set("ProductName", "Stenny")
        .set("FileDescription", "Stenny microphone recorder")
        .set("OriginalFilename", "stenny-rec.exe")
        .set("ProductVersion", &version)
        .set("FileVersion", &version)
        .set_version_info(winresource::VersionInfo::PRODUCTVERSION, packed)
        .set_version_info(winresource::VersionInfo::FILEVERSION, packed);
    match res.compile() {
        Ok(()) => {}
        // `cargo check --target x86_64-pc-windows-msvc` on a Mac has no resource compiler: check without the details
        Err(e) if !env::var("HOST").unwrap_or_default().contains("windows") => {
            println!("cargo:warning=no version details in stenny-rec.exe (no resource compiler here: {e})")
        }
        Err(e) => panic!("the Windows version resource (rc.exe from the Windows SDK): {e}"),
    }
}

/// The first `"version": "…"` in tauri.conf.json, the app's own (it comes before any plugin's settings).
fn app_version(conf: &str) -> Option<String> {
    let rest = &conf[conf.find("\"version\"")? + "\"version\"".len()..];
    let rest = &rest[rest.find('"')? + 1..];
    Some(rest[..rest.find('"')?].to_string())
}
