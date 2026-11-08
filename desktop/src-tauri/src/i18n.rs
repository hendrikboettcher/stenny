//! Stenny's own language: English or German, for the menu bar, the app menu, dialogs, notifications and window
//! titles. The texts are the windows' own (desktop/src/lib/locales/*.json, compiled in), so a word is the same in
//! the menu and in the window. The engine works out which language: `[app] language` in config.toml ("system",
//! "en" or "de"), with "system" as the first of the user's preferred languages Stenny has (`system_language`, passed
//! to the engine as $STENNY_SYSTEM_LANGUAGE). It comes back as "language" in the engine's `settings`.

use std::collections::HashMap;
use std::sync::{LazyLock, RwLock};

static EN: LazyLock<HashMap<String, String>> =
    LazyLock::new(|| serde_json::from_str(include_str!("../../src/lib/locales/en.json")).expect("locales/en.json"));
static DE: LazyLock<HashMap<String, String>> =
    LazyLock::new(|| serde_json::from_str(include_str!("../../src/lib/locales/de.json")).expect("locales/de.json"));

static LANGUAGE: RwLock<&'static str> = RwLock::new("en");

/// "en" or "de".
pub fn language() -> &'static str {
    *LANGUAGE.read().unwrap()
}

/// Sets the language ("de", anything else is English); true if it changed.
pub fn set_language(lang: &str) -> bool {
    let lang = if lang == "de" { "de" } else { "en" };
    let mut current = LANGUAGE.write().unwrap();
    let changed = *current != lang;
    *current = lang;
    changed
}

/// The first of the user's preferred languages (macOS: System Settings ▸ General ▸ Language & Region; Windows:
/// the display language) that Stenny has, else English.
pub fn system_language() -> &'static str {
    for tag in sys_locale::get_locales() {
        match tag.split(['-', '_']).next().unwrap_or("").to_lowercase().as_str() {
            "de" => return "de",
            "en" => return "en",
            _ => {}
        }
    }
    "en"
}

/// The text for `key` in the current language with its {placeholders} filled in; on Windows a key's ".windows"
/// twin, if it has one ("Show in Explorer"). Use `tr!`.
pub fn text(key: &str, values: &[(&str, String)]) -> String {
    let table: &HashMap<String, String> = if language() == "de" { &DE } else { &EN };
    let windows_key = format!("{key}.windows");
    let found = (cfg!(windows).then(|| table.get(&windows_key)).flatten())
        .or_else(|| table.get(key))
        .or_else(|| EN.get(key));
    let mut text = found.cloned().unwrap_or_else(|| key.to_string());
    for (name, value) in values {
        text = text.replace(&format!("{{{name}}}"), value);
    }
    text
}

/// `tr!("menu.status.recording", course = name)`: the text in the current language.
macro_rules! tr {
    ($key:expr) => { $crate::i18n::text($key, &[]) };
    ($key:expr, $($name:ident = $value:expr),+ $(,)?) => {
        $crate::i18n::text($key, &[$((stringify!($name), ($value).to_string())),+])
    };
}
pub(crate) use tr;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_languages_have_every_key() {
        let (en, de) = (&*EN, &*DE);
        let mut missing: Vec<_> = en.keys().filter(|k| !de.contains_key(*k)).collect();
        missing.extend(de.keys().filter(|k| !en.contains_key(*k)));
        assert!(missing.is_empty(), "{missing:?}");
    }

    #[test]
    fn fills_in_and_switches() {
        set_language("de");
        assert_eq!(tr!("menu.status.recording", course = "Statistik"), "Aufnahme · Statistik");
        assert_eq!(tr!("menu.pause"), "Pausieren");
        set_language("en");
        assert_eq!(tr!("menu.status.recordingWords", course = "Stats", words = 12), "Recording · Stats · 12 words");
        assert!(!set_language("fr")); // not one Stenny has: English, as before
        assert_eq!(language(), "en");
    }
}
