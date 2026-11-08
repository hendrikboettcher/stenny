<script lang="ts">
  // "Settings…" in the library window's footer: the window turns into Settings, sections on the left, until
  // "Back to your lectures" (or Escape). Every change is
  // saved at once: config.toml through the engine (src/stenny/settings.py), the microphone and opening at login
  // through the app (src-tauri/src/menubar.rs), like the menu bar's choices.
  import { invoke } from "@tauri-apps/api/core";
  import { onMount } from "svelte";
  import Figure from "$lib/Figure.svelte";
  import { engine, reveal, revealLabel } from "$lib/engine";

  interface Settings {
    keep_audio: boolean;
    live: { draft: boolean; backup: string };
    llm: { backend: "claude-code" | "api" | "none"; summary: boolean };
    processing: { priority: "normal" | "low" | "background"; require_ac_for_queue: boolean; night_only: boolean; night_start: string; night_end: string };
    app: { pause_shortcut: string };
    recordings: { languages: string[]; folder: string };
    paths: { vault: string; lectures: string; data: string; config: string };
    model: string;
    courses: { name: string; language: string; languages: string[] }[];
  }

  const inApp = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const SECTIONS = [
    ["general", "General"], ["recording", "Recording"], ["transcripts", "Transcripts & AI"],
    ["background", "Background work"], ["courses", "Courses"],
  ] as const;
  type Section = (typeof SECTIONS)[number][0];
  // only the languages Stenny is built and tested for (settings.py checks the same)
  const LANGS: [string, string][] = [["en", "English"], ["de", "German"], ["fr", "French"]];
  const langName = (code: string) => LANGS.find(([c]) => c === code)?.[1] ?? code.toUpperCase();

  let { onclose }: { onclose: () => void } = $props();

  let section = $state<Section>("general");
  let s = $state<Settings | null>(null);
  let loadError = $state<string | null>(null);
  let error = $state<string | null>(null);
  let saved = $state(false);
  let savedTimer: ReturnType<typeof setTimeout> | undefined;

  let devices = $state<string[]>([]);
  let defaultDevice = $state<string | null>(null);
  let microphone = $state<string>("");   // "": the system's input device
  let login = $state<string | null>(null); // "on" | "off" | "approval" | null: not available (no agent)
  let recording = $state(false);         // the shortcut field is listening
  let shortcutNote = $state<string | null>(null);

  onMount(async () => {
    try {
      s = await engine<Settings>("preferences");
    } catch (e) {
      loadError = String(e);
    }
    engine<{ devices: string[]; default: string | null }>("devices")
      .then((d) => { devices = d.devices; defaultDevice = d.default; })
      .catch(() => {});
    if (inApp) {
      invoke<string | null>("microphone_pref").then((m) => (microphone = m ?? "")).catch(() => {});
      invoke<string | null>("login_item", { on: null }).then((l) => (login = l)).catch(() => {});
    }
  });

  function done() {
    error = null;
    saved = true;
    clearTimeout(savedTimer);
    savedTimer = setTimeout(() => (saved = false), 1400);
  }

  async function set(key: string, value: unknown) {
    try {
      s = await engine<Settings>("set_preference", { key, value });
      done();
      return true;
    } catch (e) {
      error = String(e);
      s = await engine<Settings>("preferences").catch(() => s); // back to what's really saved
      return false;
    }
  }

  async function setMicrophone(device: string) {
    microphone = device;
    if (!inApp) return done();
    try {
      await invoke("set_microphone_pref", { device: device || null });
      done();
    } catch (e) {
      error = String(e);
    }
  }

  async function setLogin(on: boolean) {
    try {
      login = await invoke<string | null>("login_item", { on });
      done();
    } catch (e) {
      error = String(e);
    }
  }

  async function setCourseLanguage(name: string, language: string, languages: string[] | null) {
    try {
      s = await engine<Settings>("set_course_language", { course: name, language, languages });
      done();
    } catch (e) {
      error = String(e);
    }
  }

  // A folder as the file manager's path bar shows it: "iCloud › Obsidian › My Vault › Lectures" instead of
  // "/Users/…/Library/Mobile Documents/iCloud~md~obsidian/Documents/My Vault/Lectures", and on Windows
  // "Home › OneDrive › Vault › Lectures" instead of "C:\Users\…\OneDrive\Vault\Lectures" (the full path on hover).
  function friendly(path: string) {
    const windows = /^[A-Za-z]:[\\/]|^\\\\/.test(path);
    const parts = path.split(/[\\/]/).filter(Boolean);
    let start: string[] = [];
    let rest = parts;
    const home = windows ? (parts[1]?.toLowerCase() === "users" ? 3 : 0) : parts[0] === "Users" ? 2 : 0;
    if (home) {
      start = ["Home"];
      rest = parts.slice(home);
      // iCloud on a Mac: ~/Library/Mobile Documents/<app container>/Documents/…
      if (!windows && rest[0] === "Library" && rest[1] === "Mobile Documents" && rest[2]) {
        const container = rest[2];
        start = container === "com~apple~CloudDocs" ? ["iCloud Drive"]
          : ["iCloud", container.split("~").pop()!.replace(/^./, (c) => c.toUpperCase())];
        rest = rest.slice(rest[3] === "Documents" ? 4 : 3);
      }
    } else if (windows) {
      start = [parts[0]]; // "D:"
      rest = parts.slice(1);
    }
    return [...start, ...rest].join(" › ");
  }

  function toggleIn(list: string[], code: string) {
    return list.includes(code) ? list.filter((c) => c !== code) : [...list, code];
  }

  // The pause shortcut: press it in the field. Written as config.toml wants it: "cmd+shift+ü".
  const SYMBOLS: Record<string, string> = { cmd: "⌘", ctrl: "⌃", opt: "⌥", shift: "⇧" };
  function pretty(shortcut: string) {
    const parts = shortcut.split("+");
    const key = shortcut.endsWith("++") ? "+" : parts.pop()!;
    const mods = parts.filter(Boolean).map((m) => ({ command: "cmd", control: "ctrl", option: "opt", alt: "opt" })[m] ?? m);
    const order = ["ctrl", "opt", "shift", "cmd"]; // the order macOS shows them in
    mods.sort((a, b) => order.indexOf(a) - order.indexOf(b));
    const k = key === "space" ? "Space" : /^f\d+$/.test(key) ? key.toUpperCase() : key.toUpperCase();
    return mods.map((m) => SYMBOLS[m] ?? m).join("") + k;
  }

  async function capture(e: KeyboardEvent) {
    if (!recording) {
      if (e.key === "Escape" && !e.defaultPrevented) onclose();
      return;
    }
    e.preventDefault();
    if (e.key === "Escape") { recording = false; return; }
    if (["Meta", "Control", "Alt", "Shift", "CapsLock", "Fn"].includes(e.key)) return;
    const mods = [e.metaKey && "cmd", e.ctrlKey && "ctrl", e.altKey && "opt", e.shiftKey && "shift"].filter(Boolean);
    if (!mods.some((m) => m !== "shift")) {
      shortcutNote = "Hold ⌘, ⌃ or ⌥ too, so the shortcut doesn't get in the way of typing.";
      return;
    }
    let key: string;
    if (e.code === "Space") key = "space";
    else if (/^F\d+$/.test(e.code)) key = e.code.toLowerCase();
    else if (/^Digit\d$/.test(e.code)) key = e.code.slice(5);
    else if (e.altKey && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3).toLowerCase(); // ⌥ changes the character
    else if (e.key.length === 1) key = e.key.toLowerCase();
    else return;
    recording = false;
    const value = [...mods, key].join("+");
    if (await set("app.pause_shortcut", value)) {
      shortcutNote = null;
      if (inApp) {
        const problem = await invoke<string | null>("apply_shortcut").catch((err) => String(err));
        if (problem) shortcutNote = `Saved, but it doesn't work yet: ${problem}`;
      }
    }
  }
</script>

<svelte:window onkeydown={capture} />

<div class="window">
  <aside class="sidebar">
    <div class="titlebar" data-tauri-drag-region></div>
    <div class="brand" data-tauri-drag-region>
      <Figure height={24} />
      <span class="serif">Stenny</span>
    </div>
    <nav>
      <button class="back" onclick={onclose}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5" /></svg>
        Back to your lectures
      </button>
      <div class="kicker">Settings</div>
      {#each SECTIONS as [id, name]}
        <button class="item" class:active={section === id} onclick={() => { section = id; error = null; }}>{name}</button>
      {/each}
    </nav>
  </aside>

  <main>
    <div class="titlebar" data-tauri-drag-region>
      <span class="saved" class:show={saved} aria-live="polite">Saved</span>
    </div>
    <div class="content">
      {#if loadError}
        <p class="error selectable">{loadError}</p>
      {:else if !s}
        <p class="muted">Loading…</p>
      {:else}
        <h1 class="serif">{SECTIONS.find(([id]) => id === section)?.[1]}</h1>
        {#if error}<p class="error selectable">{error}</p>{/if}

        {#if section === "general"}
          <div class="group">
            {#if login !== null}
              <div class="row">
                <div class="what">
                  <div class="name">Open Stenny at login</div>
                  <div class="hint">
                    {login === "approval" ? "Allow it in System Settings ▸ General ▸ Login Items." : "So the calendar's lecture reminders work without thinking of it."}
                  </div>
                </div>
                <input type="checkbox" class="switch" checked={login !== "off"} onchange={(e) => setLogin(e.currentTarget.checked)} />
              </div>
            {/if}
            <div class="row">
              <div class="what">
                <div class="name">Pause shortcut</div>
                <div class="hint">Pauses and resumes the recording from any app.</div>
              </div>
              <button class="shortcut" class:listening={recording}
                      onclick={() => { recording = !recording; shortcutNote = null; }}>
                {recording ? "Press the new shortcut…" : pretty(s.app.pause_shortcut)}
              </button>
            </div>
            {#if shortcutNote}<p class="note">{shortcutNote}</p>{/if}
          </div>

          <h2>Where things are</h2>
          <div class="group">
            {#snippet place(name: string, hint: string, path: string)}
              <div class="row">
                <div class="what">
                  <div class="name">{name}</div>
                  <div class="hint path" title={path}>{friendly(path)}</div>
                  <div class="hint">{hint}</div>
                </div>
                <button class="pill" onclick={() => reveal(path)}>{revealLabel}</button>
              </div>
            {/snippet}
            {@render place("Transcripts", "Lectures, summaries and glossaries, one folder per course.", s.paths.lectures)}
            {@render place("Talks & events", "Recordings that aren't part of a course.", s.recordings.folder)}
            {@render place("Stenny's own files", "Audio, the raw transcripts and logs. They never leave this Mac.", s.paths.data)}
          </div>
        {:else if section === "recording"}
          <div class="group">
            <div class="row">
              <div class="what">
                <div class="name">Microphone</div>
                <div class="hint">The one the next recording uses; the menu bar can switch it during a lecture.</div>
              </div>
              <select value={microphone} onchange={(e) => setMicrophone(e.currentTarget.value)}>
                <option value="">System default{defaultDevice ? ` (${defaultDevice})` : ""}</option>
                {#each devices as d}<option value={d}>{d}</option>{/each}
                {#if microphone && !devices.includes(microphone)}<option value={microphone}>{microphone} (not connected)</option>{/if}
              </select>
            </div>
            <div class="row">
              <div class="what">
                <div class="name">Backup microphone</div>
                <div class="hint">Recorded alongside, and used only where the main one sent nothing.</div>
              </div>
              <select value={s.live.backup} onchange={(e) => set("live.backup", e.currentTarget.value)}>
                <option value="auto">This Mac's microphone</option>
                <option value="off">Off</option>
                {#each devices as d}<option value={d}>{d}</option>{/each}
                {#if !["auto", "off", ...devices].includes(s.live.backup)}<option value={s.live.backup}>{s.live.backup}</option>{/if}
              </select>
            </div>
          </div>
          <div class="group">
            <div class="row">
              <div class="what">
                <div class="name">Transcribe while recording</div>
                <div class="hint">A live draft to read along when the Mac is plugged in. Off: record only, transcribe afterwards.</div>
              </div>
              <input type="checkbox" class="switch" checked={s.live.draft} onchange={(e) => set("live.draft", e.currentTarget.checked)} />
            </div>
            <div class="row">
              <div class="what">
                <div class="name">Keep the audio</div>
                <div class="hint">Archived in Stenny's own files, to listen again or transcribe anew.</div>
              </div>
              <input type="checkbox" class="switch" checked={s.keep_audio} onchange={(e) => set("keep_audio", e.currentTarget.checked)} />
            </div>
          </div>
        {:else if section === "transcripts"}
          <div class="group">
            <div class="row stack">
              <div class="what">
                <div class="name">Structuring and summaries</div>
                <div class="hint">Only the text goes out, never the audio. The transcript itself is never rewritten.</div>
              </div>
              <div class="segmented">
                {#each [["claude-code", "Claude Code"], ["api", "Anthropic API"], ["none", "Off"]] as [v, label]}
                  <button class:on={s.llm.backend === v} onclick={() => set("llm.backend", v)}>{label}</button>
                {/each}
              </div>
            </div>
            <div class="row" class:disabled={s.llm.backend === "none"}>
              <div class="what">
                <div class="name">Write a summary</div>
                <div class="hint">An AI summary next to each transcript, clearly labelled as one.</div>
              </div>
              <input type="checkbox" class="switch" checked={s.llm.summary} disabled={s.llm.backend === "none"}
                     onchange={(e) => set("llm.summary", e.currentTarget.checked)} />
            </div>
          </div>
          <div class="group">
            <div class="row stack">
              <div class="what">
                <div class="name">Languages in talks</div>
                <div class="hint">A talk's language is recognised among these, every half minute or so.</div>
              </div>
              <div class="chips">
                {#each LANGS as [code, name]}
                  <button class="chip" class:on={s.recordings.languages.includes(code)}
                          onclick={() => { const next = toggleIn(s!.recordings.languages, code); if (next.length) set("recordings.languages", next); }}>{name}</button>
                {/each}
              </div>
            </div>
          </div>
          <p class="foot">Transcription runs on this Mac with {s.model.split("/").pop()}.</p>
        {:else if section === "background"}
          <p class="lead">Transcribing a lecture takes a while. These keep the Mac usable meanwhile.</p>
          <div class="group">
            <div class="row stack">
              <div class="what">
                <div class="name">Priority</div>
                <div class="hint">
                  {s.processing.priority === "normal" ? "As fast as possible; the Mac may feel busy."
                    : s.processing.priority === "low" ? "Gives way to whatever you're doing."
                    : "Heavily throttled: slow, but you won't notice it."}
                </div>
              </div>
              <div class="segmented">
                {#each [["normal", "Normal"], ["low", "Low"], ["background", "Background"]] as [v, label]}
                  <button class:on={s.processing.priority === v} onclick={() => set("processing.priority", v)}>{label}</button>
                {/each}
              </div>
            </div>
          </div>
          <div class="group">
            <div class="row">
              <div class="what">
                <div class="name">Wait for power</div>
                <div class="hint">Queued transcripts wait until the Mac is plugged in.</div>
              </div>
              <input type="checkbox" class="switch" checked={s.processing.require_ac_for_queue}
                     onchange={(e) => set("processing.require_ac_for_queue", e.currentTarget.checked)} />
            </div>
            <div class="row">
              <div class="what">
                <div class="name">Only at night</div>
                <div class="hint">Queued transcripts run only in these hours.</div>
              </div>
              <input type="checkbox" class="switch" checked={s.processing.night_only}
                     onchange={(e) => set("processing.night_only", e.currentTarget.checked)} />
            </div>
            {#if s.processing.night_only}
              <div class="row">
                <div class="what"><div class="name">Hours</div></div>
                <div class="times">
                  <input type="time" value={s.processing.night_start} onchange={(e) => set("processing.night_start", e.currentTarget.value)} />
                  <span>to</span>
                  <input type="time" value={s.processing.night_end} onchange={(e) => set("processing.night_end", e.currentTarget.value)} />
                </div>
              </div>
            {/if}
          </div>
        {:else if section === "courses"}
          <p class="lead">The language each course is taught in. Add, rename and remove courses in Stenny's window.</p>
          {#if !s.courses.length}
            <p class="muted">No courses yet.</p>
          {:else}
            <div class="group">
              {#each s.courses as c (c.name)}
                {@const multi = c.language === "mixed" || c.language === "auto"}
                <div class="row" class:stack={multi}>
                  <div class="what">
                    <div class="name serif course">{c.name}</div>
                    <div class="hint">
                      {c.language === "mixed" ? "Can switch language mid-sentence; checked every few seconds among:"
                        : c.language === "auto" ? "One language per lecture, recognised among:"
                        : `Always in ${langName(c.language)}.`}
                    </div>
                  </div>
                  <select value={c.language} onchange={(e) => {
                    const v = e.currentTarget.value;
                    const m = v === "mixed" || v === "auto";
                    setCourseLanguage(c.name, v, m ? (c.languages.length > 1 ? c.languages : ["en", "de", "fr"]) : null);
                  }}>
                    {#each LANGS as [code, name]}<option value={code}>{name}</option>{/each}
                    {#if !LANGS.some(([code]) => code === c.language) && !multi}<option value={c.language}>{c.language}</option>{/if}
                    <option value="auto">One per lecture</option>
                    <option value="mixed">Mixed</option>
                  </select>
                  {#if multi}
                    <div class="chips">
                      {#each LANGS as [code, name]}
                        <button class="chip" class:on={c.languages.includes(code)}
                                onclick={() => { const next = toggleIn(c.languages, code); if (next.length) setCourseLanguage(c.name, c.language, next); }}>{name}</button>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        {/if}
      {/if}
    </div>
  </main>
</div>

<style>
  .window { display: grid; grid-template-columns: 220px minmax(0, 1fr); height: 100vh; }
  .titlebar { height: var(--titlebar); flex: none; }

  .sidebar { background: var(--bar); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
  .brand { display: flex; align-items: center; gap: 9px; padding: 6px 20px 22px; }
  .brand span { font-size: 22px; font-weight: 460; letter-spacing: -0.01em; }
  .back {
    display: flex; align-items: center; gap: 4px; border: 0; background: none; text-align: left; padding: 4px 10px 4px 6px;
    color: var(--muted); border-radius: 6px; margin-bottom: 14px;
  }
  .back:hover { color: var(--ink); }
  .back svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .kicker { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 600; padding: 0 10px 6px; }
  nav { padding: 0 10px; display: flex; flex-direction: column; gap: 1px; }
  .item {
    border: 0; background: none; text-align: left; padding: 6px 10px; border-radius: 7px;
    font-family: var(--serif); font-size: 15px; color: var(--ink-2);
  }
  .item:hover { color: var(--ink); }
  .item.active { background: var(--mark); color: var(--ink); }

  main { overflow-y: auto; min-width: 0; display: flex; flex-direction: column; }
  .content { margin: 0 auto; width: 100%; }
  main > .titlebar { position: sticky; top: 0; z-index: 5; background: var(--bg); display: flex; justify-content: flex-end; align-items: center; padding: 0 18px; }
  .saved { color: var(--good); font-size: 12px; opacity: 0; transition: opacity 0.25s ease; }
  .saved.show { opacity: 1; }
  .content { padding: 8px 48px 40px; max-width: 720px; }
  h1 { font-size: 28px; line-height: 1.1; font-weight: 440; letter-spacing: -0.02em; margin: 4px 0 18px; }
  h2 { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 600; margin: 26px 0 8px; }
  .lead { color: var(--ink-2); margin: -6px 0 16px; line-height: 1.5; }
  .muted { color: var(--muted); }
  .error { color: var(--rec); margin: 0 0 14px; }
  .foot { color: var(--muted); font-size: 12px; margin: 14px 2px 0; }

  .group { background: var(--card); border: 1px solid var(--line); border-radius: 12px; margin-bottom: 14px; }
  .row { display: flex; align-items: center; gap: 16px; padding: 12px 16px; }
  .row + .row { border-top: 1px solid var(--line); }
  .row.stack { flex-wrap: wrap; }
  .row.stack > .what { flex-basis: 100%; }
  .row.disabled .what { opacity: 0.5; }
  .what { flex: 1; min-width: 0; }
  .name { font-weight: 500; color: var(--ink); }
  .name.course { font-weight: 440; font-size: 15.5px; }
  .hint { color: var(--muted); font-size: 12px; line-height: 1.4; margin-top: 2px; }
  .hint.path { color: var(--ink-2); overflow-wrap: anywhere; }
  .note { color: var(--warn); font-size: 12px; margin: 0; padding: 0 16px 12px; }

  select, input[type="time"] {
    font: inherit; color: var(--ink); background: var(--bg); border: 1px solid var(--line); border-radius: 8px;
    padding: 5px 8px; max-width: 230px;
  }
  select:focus, input[type="time"]:focus { outline: 2px solid color-mix(in srgb, var(--ink) 25%, transparent); outline-offset: 0; }
  .times { display: flex; align-items: center; gap: 8px; color: var(--muted); }

  .pill { border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 5px 13px; font-weight: 500; flex: none; white-space: nowrap; }
  .pill:hover { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }

  .shortcut {
    min-width: 120px; border: 1px solid var(--line); background: var(--bg); border-radius: 8px; padding: 5px 12px;
    font-size: 13.5px; letter-spacing: 0.04em; font-variant-numeric: tabular-nums;
  }
  .shortcut.listening { border-color: var(--ink); color: var(--muted); letter-spacing: 0; font-size: 12.5px; }

  .switch {
    appearance: none; -webkit-appearance: none; flex: none; margin: 0; width: 34px; height: 20px; border-radius: 999px;
    background: var(--line); position: relative; transition: background 0.18s ease;
  }
  .switch::after {
    content: ""; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
    background: #fff; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2); transition: transform 0.18s ease;
  }
  .switch:checked { background: var(--ink); }
  .switch:checked::after { transform: translateX(14px); background: var(--bg); }
  .switch:disabled { opacity: 0.45; }

  .segmented { display: inline-flex; background: var(--bar); border: 1px solid var(--line); border-radius: 9px; padding: 2px; gap: 2px; }
  .segmented button { border: 0; background: none; padding: 4px 12px; border-radius: 7px; color: var(--ink-2); }
  .segmented button:hover { color: var(--ink); }
  .segmented button.on { background: var(--card); color: var(--ink); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08); font-weight: 500; }

  .chips { display: flex; flex-wrap: wrap; gap: 6px; flex-basis: 100%; }
  .chip { border: 1px solid var(--line); background: none; border-radius: 999px; padding: 3px 11px; color: var(--ink-2); font-size: 12.5px; }
  .chip:hover { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .chip.on { background: var(--ink); border-color: var(--ink); color: var(--bg); }

  @media (prefers-reduced-motion: reduce) {
    .switch, .switch::after, .saved { transition: none; }
  }
</style>
