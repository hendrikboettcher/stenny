// The app's only door to Stenny's engine (`stenny app-server`, see src-tauri/src/engine.rs).
import { invoke } from "@tauri-apps/api/core";

export type Status = "final" | "draft" | "live" | "queued" | "finalizing";

export interface Entry {
  kind: "lecture" | "event";
  course: string;
  folder: string;           // "2026-10-20" or "2026-10-20 (2)"
  number: number;           // 0 for talks
  title: string | null;     // its own title, given in this window ("Decision trees")
  date: string;
  start: string | null;
  duration: string | null;
  language: string | null;
  status: Status;
  draft_note: string | null; // a live draft: where its final transcript is
  transcript: string;
  summary: string | null;
  final_pass?: FinalPass | null; // "finalizing": how far it is (null before it reports anything)
}

// How far a final pass is (src/stenny/progress.py): the banner, the list's row and the activity bar read the same
export interface FinalPass {
  step: "transcribing" | "tidying" | "saving";
  fraction: number;       // 0…1 of the whole pass
  at: number;             // lecture seconds transcribed so far
  of: number;             // the lecture's length in seconds
  left_s: number | null;  // seconds of transcribing left, once the speed is known
}

export interface Library {
  entries: Entry[];
  courses: { name: string; hidden: boolean }[];
}

// Whether the chosen option of the AI step works right now (src/stenny/claude_setup.py, no tokens spent)
export interface AiStatus {
  backend: "claude-code" | "api" | "none";
  state: "ready" | "missing" | "signed_out" | "unknown" | "no_key" | "bad_key" | "unchecked" | "off";
  account?: string | null;
  source?: "keychain" | "environment";
  hint?: string;
  detail?: string;
}
// The Stenny connector in Claude Desktop (and Claude Code)
export interface ConnectorStatus {
  desktop: boolean;
  installed: boolean;
  enabled: boolean;
  version: string | null;
  file: string | null;
  code: { available: boolean; added: boolean };
}
// The library window's "Finish setting up Stenny" card (app_server.setup_status): show is false once it's gone
// for good, and then nothing else is checked
export interface SetupStatus {
  show: boolean;
  courses?: number;
  ai?: AiStatus;
  connector?: ConnectorStatus;
}

const inApp = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

// In the app: through Rust (engine.rs). In a browser during development: through dev/engine-bridge.js.
export async function engine<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  if (inApp) return invoke<T>("engine", { method, params });
  const reply = await (await fetch("/__engine", { method: "POST", body: JSON.stringify({ method, params }) })).json();
  if (reply.error) throw reply.error;
  return reply.result as T;
}

const where = (e: Entry) => ({ course: e.course, folder: e.folder, event: e.kind === "event" });

export const library = () => engine<Library>("library");
export const setupStatus = () => engine<SetupStatus>("setup_status");
// progress: the text so far of a recording whose transcript isn't done yet
export const lecture = (e: Entry) =>
  engine<{ entry: Entry; summary: string | null; progress: string | null }>("lecture", where(e));
export const makeCopy = (e: Entry, format: string, withSummary: boolean) =>
  engine<{ path: string }>("make_copy", { ...where(e), format, with_summary: withSummary });
// A PDF from an HTML copy (stenny-agent on a Mac, WebView2 on Windows: make_pdf in src-tauri/src/lib.rs); in a browser the HTML stays
export const makePdf = (html: string) => (inApp ? invoke<string>("make_pdf", { html }) : Promise.resolve(html));
export const finishNow = (e: Entry) => engine<{ started: boolean }>("finish_now", where(e));
export const openTranscript = (path: string) => (inApp ? invoke("open_transcript", { path }) : console.log("open", path));
export const reveal = (path: string) => (inApp ? invoke("reveal", { path }) : console.log("reveal", path));
// Stenny runs on macOS and Windows; what the file manager and the bin are called there: lib/i18n.svelte.ts
export const onWindows = typeof navigator !== "undefined" && /Windows/.test(navigator.userAgent);
// the system's share menu for these files: on macOS below `anchor` (the Share button), on Windows the Share panel
export const shareFiles = (paths: string[], anchor: DOMRect) =>
  inApp
    ? invoke<void>("share_files", { paths, x: anchor.left, y: anchor.top, width: anchor.width, height: anchor.height })
    : Promise.resolve(console.log("share", paths));
// "Did the lecturer agree?", asked once per course before sharing (in a browser during development: yes)
export const lecturerAgreed = (e: Entry, use: "share" | "record") =>
  inApp ? invoke<boolean>("lecturer_agreed", { purpose: use, course: e.course, event: e.kind === "event" }) : Promise.resolve(true);

// After the course list changed: the menu bar's Start Lecture menu reads it again now, not at its next minute
async function coursesChanged<T>(done: Promise<T>): Promise<T> {
  const result = await done;
  if (inApp) invoke("courses_changed").catch(() => {});
  return result;
}

// Renaming, deleting (to the Trash or Recycle Bin) and the course list (src/stenny/manage.py, courses.py)
export const renameLecture = (e: Entry, title: string) =>
  engine<{ title: string | null }>("rename_lecture", { ...where(e), title });
export const deleteLecture = (e: Entry) => engine<{ message: string }>("delete_lecture", where(e));
export const renameCourse = (course: string, event: boolean, name: string) =>
  coursesChanged(engine<{ name: string }>("rename_course", { course, event, name }));
export const addCourse = (name: string, language: string, alias: string) =>
  coursesChanged(engine<{ message: string }>("add_course", { name, language, alias }));
// to the clipboard (in a browser during development: the browser's)
export const copyText = (text: string) => (inApp ? invoke<void>("copy_text", { text }) : navigator.clipboard.writeText(text));
// a whole course or talk and all its recordings to the Trash (Recycle Bin on Windows)
export const deleteCourse = (course: string, event: boolean) =>
  coursesChanged(engine<{ message: string }>("delete_course", { course, event }));
export const removeCourse = (course: string) => coursesChanged(engine<{ message: string }>("remove_course", { course }));
// a removed course back: `add` restores it with its earlier settings (the language counts only for a new one)
export const bringBack = (course: string) =>
  coursesChanged(engine<{ message: string }>("add_course", { name: course, language: "auto" }));
