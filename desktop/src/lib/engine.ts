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
}

export interface Library {
  entries: Entry[];
  courses: { name: string; hidden: boolean }[];
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
// progress: the text so far of a recording whose transcript isn't done yet
export const lecture = (e: Entry) =>
  engine<{ entry: Entry; summary: string | null; progress: string | null }>("lecture", where(e));
export const makeCopy = (e: Entry, format: string, withSummary: boolean) =>
  engine<{ path: string }>("make_copy", { ...where(e), format, with_summary: withSummary });
// A PDF from an HTML copy (stenny-agent, see make_pdf in src-tauri/src/lib.rs); in a browser the HTML stays
export const makePdf = (html: string) => (inApp ? invoke<string>("make_pdf", { html }) : Promise.resolve(html));
export const finishNow = (e: Entry) => engine<{ started: boolean }>("finish_now", where(e));
export const openTranscript = (path: string) => (inApp ? invoke("open_transcript", { path }) : console.log("open", path));
export const reveal = (path: string) => (inApp ? invoke("reveal", { path }) : console.log("reveal", path));
// "Did the lecturer agree?", asked once per course before sharing (in a browser during development: yes)
export const lecturerAgreed = (e: Entry, use: "share" | "record") =>
  inApp ? invoke<boolean>("lecturer_agreed", { purpose: use, course: e.course, event: e.kind === "event" }) : Promise.resolve(true);

// Renaming, deleting (to the Trash) and the course list (src/stenny/manage.py, courses.py)
export const renameLecture = (e: Entry, title: string) =>
  engine<{ title: string | null }>("rename_lecture", { ...where(e), title });
export const deleteLecture = (e: Entry) => engine<{ message: string }>("delete_lecture", where(e));
export const renameCourse = (course: string, event: boolean, name: string) =>
  engine<{ name: string }>("rename_course", { course, event, name });
export const addCourse = (name: string, language: string, alias: string) =>
  engine<{ message: string }>("add_course", { name, language, alias });
export const removeCourse = (course: string) => engine<{ message: string }>("remove_course", { course });
// a removed course back: `add` restores it with its earlier settings (the language counts only for a new one)
export const bringBack = (course: string) => engine<{ message: string }>("add_course", { name: course, language: "auto" });
