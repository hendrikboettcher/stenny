// The app in English and German. The texts live in locales/en.json and locales/de.json, shared with the Rust
// shell (src-tauri/src/i18n.rs); the engine (src/stenny/i18n.py) and stenny-agent mirror the few they show.
// Terms are kept the same everywhere: see "Language" in desktop/README.md.
//
// The language comes from Settings ▸ General (`[app] language` in config.toml: "system", "en" or "de"); the engine
// works out "system" (`ui_language`). A change reaches every window at once (the "language" event from Rust).
import en from "./locales/en.json";
import de from "./locales/de.json";

export type Lang = "en" | "de";
export type Key = keyof typeof en;

const tables: Record<Lang, Record<string, string>> = { en, de };

// Stenny runs on macOS and Windows: a key with a ".windows" twin (what the file manager and the bin are called)
// uses that twin on Windows
const onWindows = typeof navigator !== "undefined" && /Windows/.test(navigator.userAgent);

let current = $state<Lang>("en");

export const language = () => current;

export function setLanguage(lang: string | null | undefined) {
  current = lang === "de" ? "de" : "en";
  if (typeof document !== "undefined") document.documentElement.lang = current;
}

/** The text for `key` in the current language, with {name} filled in from `vars`. */
export function t(key: Key, vars: Record<string, string | number> = {}): string {
  const table = tables[current];
  const platformKey = `${key}.windows`;
  const text = (onWindows && (table[platformKey] ?? en[platformKey as Key])) || table[key] || en[key] || key;
  return text.replace(/\{(\w+)\}/g, (all, name) => (name in vars ? String(vars[name]) : all));
}

/** `base`.one or `base`.other, with {n}. */
export function tn(base: string, n: number, vars: Record<string, string | number> = {}): string {
  return t(`${base}.${n === 1 ? "one" : "other"}` as Key, { n, ...vars });
}

/** For dates and language names: "en-GB" (day before month, as Stenny always showed them) or "de-DE". */
export const locale = () => (current === "de" ? "de-DE" : "en-GB");

/** A language code as a name in the current language ("de" → "German" / "Deutsch"). */
export function languageName(code: string | null): string | null {
  if (!code) return null;
  if (`lang.${code}` in en) return t(`lang.${code}` as Key);
  try { return new Intl.DisplayNames([locale()], { type: "language" }).of(code) ?? code; } catch { return code; }
}

// a few labels that read better as functions in the markup
export const trashName = () => t("common.trash");
export const revealLabel = () => t("common.revealIn");
