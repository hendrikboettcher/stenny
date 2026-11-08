<script lang="ts">
  // "Settings…" in the library window's footer: the window turns into Settings, sections on the left, until
  // "Back to your lectures" (or Escape). Every change is
  // saved at once: config.toml through the engine (src/stenny/settings.py), the microphone and opening at login
  // through the app (src-tauri/src/menubar.rs), like the menu bar's choices.
  import { invoke } from "@tauri-apps/api/core";
  import { onMount } from "svelte";
  import Figure from "$lib/Figure.svelte";
  import Dialog from "$lib/Dialog.svelte";
  import { addCourse, bringBack, deleteCourse, engine, library, removeCourse, reveal } from "$lib/engine";
  import { t, tn, setLanguage, languageName, trashName, revealLabel } from "$lib/i18n.svelte";

  interface Settings {
    keep_audio: boolean;
    live: { draft: boolean; backup: string };
    llm: { backend: "claude-code" | "api" | "none"; summary: boolean };
    processing: { priority: "normal" | "low" | "background"; require_ac_for_queue: boolean; night_only: boolean; night_start: string; night_end: string };
    app: { pause_shortcut: string; language: "system" | "en" | "de" };
    ui_language: "en" | "de";        // what "system" comes to, or the language picked
    system_language: "en" | "de";
    recordings: { languages: string[]; folder: string };
    paths: { vault: string; lectures: string; data: string; config: string };
    model: string;
    courses: { name: string; language: string; languages: string[] }[];
  }

  const inApp = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const SECTIONS = ["general", "recording", "transcripts", "background", "courses"] as const;
  type Section = (typeof SECTIONS)[number];
  const sectionName = (id: Section) => t(`settings.section.${id}`);
  // only the languages Stenny is built and tested for (settings.py checks the same)
  const LANGS = ["en", "de", "fr"];
  const langName = (code: string) => (LANGS.includes(code) ? languageName(code)! : code.toUpperCase());

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

  // Past courses: removed from the list (hidden in the config) or only left as recordings. Brought back, or
  // deleted with everything Stenny saved for them (manage.delete_course, to the Trash or Recycle Bin).
  let past = $state<{ name: string; lectures: number }[]>([]);
  let pastBusy = $state<string | null>(null);   // the course being brought back
  let deleting = $state<{ name: string; lectures: number; current?: boolean } | null>(null);
  let deleteBusy = $state(false);
  let deleteError = $state<string | null>(null);

  // The current courses can be added, removed (→ Past courses) and deleted here too, like in the window's sidebar:
  // "Add Course" under the list, and each course's ⋯ menu.
  let lectureCount = $state(new Map<string, number>());
  let courseMenu = $state<{ name: string; x: number; y: number } | null>(null);
  let adding = $state(false);
  let removing = $state<string | null>(null);
  let newName = $state("");
  let newLanguage = $state("en");
  let newAlias = $state("");
  let courseBusy = $state(false);
  let courseError = $state<string | null>(null);

  function openCourseMenu(ev: MouseEvent, name: string) {
    ev.stopPropagation();
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    courseMenu = courseMenu?.name === name ? null : { name, x: Math.max(8, r.right - 200), y: r.bottom + 4 };
  }

  function ask(what: "add" | "remove" | "delete", name = "") {
    courseMenu = null;
    courseError = null;
    deleteError = null;
    if (what === "add") { newName = ""; newLanguage = "en"; newAlias = ""; adding = true; }
    else if (what === "remove") removing = name;
    else deleting = { name, lectures: lectureCount.get(name) ?? 0, current: true };
  }

  async function changeCourses(action: () => Promise<unknown>, close: () => void) {
    courseBusy = true;
    courseError = null;
    try {
      await action();
      close();
      s = await engine<Settings>("preferences");
      await loadPast();
      done();
    } catch (e) {
      courseError = String(e);
    } finally {
      courseBusy = false;
    }
  }

  async function loadPast() {
    const lib = await library();
    const shown = new Set(lib.courses.filter((c) => !c.hidden).map((c) => c.name));
    const lectures = new Map<string, number>();
    for (const e of lib.entries) if (e.kind !== "event") lectures.set(e.course, (lectures.get(e.course) ?? 0) + 1);
    lectureCount = lectures;
    const names = new Set([...lib.courses.filter((c) => c.hidden).map((c) => c.name), ...lectures.keys()]);
    past = [...names].filter((n) => !shown.has(n)).sort((a, b) => a.localeCompare(b))
      .map((name) => ({ name, lectures: lectures.get(name) ?? 0 }));
  }

  async function restore(name: string) {
    pastBusy = name;
    try {
      await bringBack(name);
      s = await engine<Settings>("preferences");
      await loadPast();
      done();
    } catch (e) {
      error = String(e);
    } finally {
      pastBusy = null;
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    deleteBusy = true;
    deleteError = null;
    try {
      await deleteCourse(deleting.name, false);
      deleting = null;
      s = await engine<Settings>("preferences");
      await loadPast();
      done();
    } catch (e) {
      deleteError = String(e);
    } finally {
      deleteBusy = false;
    }
  }

  onMount(async () => {
    try {
      s = await engine<Settings>("preferences");
    } catch (e) {
      loadError = String(e);
    }
    loadPast().catch(() => {});
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

  // Stenny's own language (not the lectures'): saved like any setting, then every window and the menu bar follow
  async function setUiLanguage(value: string) {
    if (!(await set("app.language", value)) || !s) return;
    setLanguage(s.ui_language);
    if (inApp) invoke("set_ui_language", { language: s.ui_language }).catch(() => {});
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
      start = [t("common.home")];
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
    const k = key === "space" ? t("settings.shortcut.space") : /^f\d+$/.test(key) ? key.toUpperCase() : key.toUpperCase();
    return mods.map((m) => SYMBOLS[m] ?? m).join("") + k;
  }

  async function capture(e: KeyboardEvent) {
    if (!recording) {
      if (e.key === "Escape" && courseMenu) { courseMenu = null; return; }
      if (e.key === "Escape" && !e.defaultPrevented && !deleting && !adding && !removing) onclose();
      return;
    }
    e.preventDefault();
    if (e.key === "Escape") { recording = false; return; }
    if (["Meta", "Control", "Alt", "Shift", "CapsLock", "Fn"].includes(e.key)) return;
    const mods = [e.metaKey && "cmd", e.ctrlKey && "ctrl", e.altKey && "opt", e.shiftKey && "shift"].filter(Boolean);
    if (!mods.some((m) => m !== "shift")) {
      shortcutNote = t("settings.shortcut.needsModifier");
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
        if (problem) shortcutNote = t("settings.shortcut.notYet", { problem });
      }
    }
  }
</script>

<svelte:window onkeydown={capture} onclick={() => (courseMenu = null)} onblur={() => (courseMenu = null)} />

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
        {t("settings.back")}
      </button>
      <div class="kicker">{t("settings.title")}</div>
      {#each SECTIONS as id}
        <button class="item" class:active={section === id} onclick={() => { section = id; error = null; }}>{sectionName(id)}</button>
      {/each}
    </nav>
  </aside>

  <main>
    <div class="titlebar" data-tauri-drag-region>
      <span class="saved" class:show={saved} aria-live="polite">{t("settings.saved")}</span>
    </div>
    <div class="content">
      {#if loadError}
        <p class="error selectable">{loadError}</p>
      {:else if !s}
        <p class="muted">{t("common.loading")}</p>
      {:else}
        <h1 class="serif">{sectionName(section)}</h1>
        {#if error}<p class="error selectable">{error}</p>{/if}

        {#if section === "general"}
          <div class="group">
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.uiLanguage")}</div>
                <div class="hint">{t("settings.uiLanguage.hint")}</div>
              </div>
              <select value={s.app.language} onchange={(e) => setUiLanguage(e.currentTarget.value)}>
                <option value="system">{t("settings.uiLanguage.system", { language: s.system_language === "de" ? "Deutsch" : "English" })}</option>
                <option value="en">English</option>
                <option value="de">Deutsch</option>
              </select>
            </div>
            {#if login !== null}
              <div class="row">
                <div class="what">
                  <div class="name">{t("settings.login")}</div>
                  <div class="hint">{login === "approval" ? t("settings.login.approval") : t("settings.login.hint")}</div>
                </div>
                <input type="checkbox" class="switch" checked={login !== "off"} onchange={(e) => setLogin(e.currentTarget.checked)} />
              </div>
            {/if}
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.shortcut")}</div>
                <div class="hint">{t("settings.shortcut.hint")}</div>
              </div>
              <button class="shortcut" class:listening={recording}
                      onclick={() => { recording = !recording; shortcutNote = null; }}>
                {recording ? t("settings.shortcut.press") : pretty(s.app.pause_shortcut)}
              </button>
            </div>
            {#if shortcutNote}<p class="note">{shortcutNote}</p>{/if}
          </div>

          <h2>{t("settings.places")}</h2>
          <div class="group">
            {#snippet place(name: string, hint: string, path: string)}
              <div class="row">
                <div class="what">
                  <div class="name">{name}</div>
                  <div class="hint path" title={path}>{friendly(path)}</div>
                  <div class="hint">{hint}</div>
                </div>
                <button class="pill" onclick={() => reveal(path)}>{revealLabel()}</button>
              </div>
            {/snippet}
            {@render place(t("settings.places.transcripts"), t("settings.places.transcripts.hint"), s.paths.lectures)}
            {@render place(t("common.talksEvents"), t("settings.places.talks.hint"), s.recordings.folder)}
            {@render place(t("settings.places.data"), t("settings.places.data.hint"), s.paths.data)}
          </div>
        {:else if section === "recording"}
          <div class="group">
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.mic")}</div>
                <div class="hint">{t("settings.mic.hint")}</div>
              </div>
              <select value={microphone} onchange={(e) => setMicrophone(e.currentTarget.value)}>
                <option value="">{defaultDevice ? t("settings.mic.defaultNamed", { device: defaultDevice }) : t("settings.mic.default")}</option>
                {#each devices as d}<option value={d}>{d}</option>{/each}
                {#if microphone && !devices.includes(microphone)}<option value={microphone}>{t("settings.mic.notConnected", { device: microphone })}</option>{/if}
              </select>
            </div>
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.backup")}</div>
                <div class="hint">{t("settings.backup.hint")}</div>
              </div>
              <select value={s.live.backup} onchange={(e) => set("live.backup", e.currentTarget.value)}>
                <option value="auto">{t("settings.backup.mac")}</option>
                <option value="off">{t("common.off")}</option>
                {#each devices as d}<option value={d}>{d}</option>{/each}
                {#if !["auto", "off", ...devices].includes(s.live.backup)}<option value={s.live.backup}>{s.live.backup}</option>{/if}
              </select>
            </div>
          </div>
          <div class="group">
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.draft")}</div>
                <div class="hint">{t("settings.draft.hint")}</div>
              </div>
              <input type="checkbox" class="switch" checked={s.live.draft} onchange={(e) => set("live.draft", e.currentTarget.checked)} />
            </div>
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.keepAudio")}</div>
                <div class="hint">{t("settings.keepAudio.hint")}</div>
              </div>
              <input type="checkbox" class="switch" checked={s.keep_audio} onchange={(e) => set("keep_audio", e.currentTarget.checked)} />
            </div>
          </div>
        {:else if section === "transcripts"}
          <div class="group">
            <div class="row stack">
              <div class="what">
                <div class="name">{t("settings.llm")}</div>
                <div class="hint">{t("settings.llm.hint")}</div>
              </div>
              <div class="segmented">
                {#each [["claude-code", "Claude Code"], ["api", "Anthropic API"], ["none", t("common.off")]] as [v, label]}
                  <button class:on={s.llm.backend === v} onclick={() => set("llm.backend", v)}>{label}</button>
                {/each}
              </div>
            </div>
            <div class="row" class:disabled={s.llm.backend === "none"}>
              <div class="what">
                <div class="name">{t("settings.summary")}</div>
                <div class="hint">{t("settings.summary.hint")}</div>
              </div>
              <input type="checkbox" class="switch" checked={s.llm.summary} disabled={s.llm.backend === "none"}
                     onchange={(e) => set("llm.summary", e.currentTarget.checked)} />
            </div>
          </div>
          <div class="group">
            <div class="row stack">
              <div class="what">
                <div class="name">{t("settings.talkLanguages")}</div>
                <div class="hint">{t("settings.talkLanguages.hint")}</div>
              </div>
              <div class="chips">
                {#each LANGS as code}
                  <button class="chip" class:on={s.recordings.languages.includes(code)}
                          onclick={() => { const next = toggleIn(s!.recordings.languages, code); if (next.length) set("recordings.languages", next); }}>{langName(code)}</button>
                {/each}
              </div>
            </div>
          </div>
          <p class="foot">{t("settings.model", { model: s.model.split("/").pop() ?? s.model })}</p>
        {:else if section === "background"}
          <p class="lead">{t("settings.background.lead")}</p>
          <div class="group">
            <div class="row stack">
              <div class="what">
                <div class="name">{t("settings.priority")}</div>
                <div class="hint">{t(`settings.priority.${s.processing.priority}.hint`)}</div>
              </div>
              <div class="segmented">
                {#each ["normal", "low", "background"] as const as v}
                  <button class:on={s.processing.priority === v} onclick={() => set("processing.priority", v)}>{t(`settings.priority.${v}`)}</button>
                {/each}
              </div>
            </div>
          </div>
          <div class="group">
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.power")}</div>
                <div class="hint">{t("settings.power.hint")}</div>
              </div>
              <input type="checkbox" class="switch" checked={s.processing.require_ac_for_queue}
                     onchange={(e) => set("processing.require_ac_for_queue", e.currentTarget.checked)} />
            </div>
            <div class="row">
              <div class="what">
                <div class="name">{t("settings.night")}</div>
                <div class="hint">{t("settings.night.hint")}</div>
              </div>
              <input type="checkbox" class="switch" checked={s.processing.night_only}
                     onchange={(e) => set("processing.night_only", e.currentTarget.checked)} />
            </div>
            {#if s.processing.night_only}
              <div class="row">
                <div class="what"><div class="name">{t("settings.hours")}</div></div>
                <div class="times">
                  <input type="time" value={s.processing.night_start} onchange={(e) => set("processing.night_start", e.currentTarget.value)} />
                  <span>{t("settings.hoursTo")}</span>
                  <input type="time" value={s.processing.night_end} onchange={(e) => set("processing.night_end", e.currentTarget.value)} />
                </div>
              </div>
            {/if}
          </div>
        {:else if section === "courses"}
          <p class="lead">{t("settings.courses.lead")}</p>
          {#if !s.courses.length}
            <p class="muted">{t("settings.courses.none")}</p>
          {:else}
            <div class="group">
              {#each s.courses as c (c.name)}
                {@const multi = c.language === "mixed" || c.language === "auto"}
                <div class="row" class:stack={multi}>
                  <div class="what">
                    <div class="name serif course">{c.name}</div>
                    <div class="hint">
                      {c.language === "mixed" ? t("settings.courses.mixed") : c.language === "auto" ? t("settings.courses.auto")
                        : t("settings.courses.always", { language: langName(c.language) })}
                    </div>
                  </div>
                  <select value={c.language} onchange={(e) => {
                    const v = e.currentTarget.value;
                    const m = v === "mixed" || v === "auto";
                    setCourseLanguage(c.name, v, m ? (c.languages.length > 1 ? c.languages : ["en", "de", "fr"]) : null);
                  }}>
                    {#each LANGS as code}<option value={code}>{langName(code)}</option>{/each}
                    {#if !LANGS.includes(c.language) && !multi}<option value={c.language}>{c.language}</option>{/if}
                    <option value="auto">{t("settings.courses.onePerLecture")}</option>
                    <option value="mixed">{t("lang.mixed")}</option>
                  </select>
                  <button class="more" class:open={courseMenu?.name === c.name} aria-label={t("lib.optionsFor", { course: c.name })}
                          title={t("lib.options")} onclick={(ev) => openCourseMenu(ev, c.name)}>
                    <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.25" /><circle cx="8" cy="8" r="1.25" /><circle cx="12.5" cy="8" r="1.25" /></svg>
                  </button>
                  {#if multi}
                    <div class="chips">
                      {#each LANGS as code}
                        <button class="chip" class:on={c.languages.includes(code)}
                                onclick={() => { const next = toggleIn(c.languages, code); if (next.length) setCourseLanguage(c.name, c.language, next); }}>{langName(code)}</button>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
          <button class="pill add-course" onclick={() => ask("add")}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9" /></svg>{t("add.confirm")}
          </button>
          {#if past.length}
            <h2>{t("settings.past")}</h2>
            <p class="lead small">{t("settings.past.lead")}</p>
            <div class="group">
              {#each past as c (c.name)}
                <div class="row">
                  <div class="what">
                    <div class="name serif course">{c.name}</div>
                    <div class="hint">{c.lectures === 0 ? t("settings.past.nothing") : tn("settings.past.lectures", c.lectures)}</div>
                  </div>
                  <div class="actions">
                    <button class="pill" disabled={pastBusy !== null} onclick={() => restore(c.name)}>
                      {pastBusy === c.name ? t("common.oneMoment") : t("settings.past.bringBack")}
                    </button>
                    <button class="pill danger" disabled={pastBusy !== null}
                            onclick={() => { deleting = c; deleteError = null; }}>{t("settings.past.delete")}</button>
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        {/if}
      {/if}
    </div>
  </main>
</div>

{#if courseMenu}
  {@const name = courseMenu.name}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="menu" role="menu" tabindex="-1" style="left: {courseMenu.x}px; top: {courseMenu.y}px" onclick={(ev) => ev.stopPropagation()}>
    <button role="menuitem" onclick={() => ask("remove", name)}>{t("lib.menu.removeCourse")}</button>
    <div class="menu-line"></div>
    <button role="menuitem" class="danger" onclick={() => ask("delete", name)}>{t("lib.menu.deleteCourse")}</button>
  </div>
{/if}

{#if adding}
  <Dialog title={t("add.title")} confirm={t("add.confirm")} busy={courseBusy} error={courseError} text={t("add.text")}
          oncancel={() => (adding = false)}
          onconfirm={() => changeCourses(() => addCourse(newName, newLanguage, newAlias), () => (adding = false))}>
    <label for="settings-course-name">{t("add.name")}</label>
    <input id="settings-course-name" bind:value={newName} placeholder={t("add.namePlaceholder")} autocomplete="off" spellcheck="false" />
    <label for="settings-course-language">{t("common.language")}</label>
    <select id="settings-course-language" bind:value={newLanguage}>
      {#each [...LANGS, "mixed", "auto"] as code}<option value={code}>{languageName(code)}</option>{/each}
    </select>
    <label for="settings-course-alias">{t("add.alias")}</label>
    <input id="settings-course-alias" bind:value={newAlias} placeholder={t("add.aliasPlaceholder")} autocomplete="off" spellcheck="false" />
  </Dialog>
{/if}

{#if removing}
  {@const name = removing}
  {@const n = lectureCount.get(name) ?? 0}
  <Dialog title={t("remove.title", { course: name })} confirm={t("remove.confirm")} busy={courseBusy} error={courseError}
          text={t(n === 0 ? "settings.removeCourse.text.none" : n === 1 ? "settings.removeCourse.text.one" : "settings.removeCourse.text.other", { n })}
          oncancel={() => (removing = null)}
          onconfirm={() => changeCourses(() => removeCourse(name), () => (removing = null))} />
{/if}

{#if deleting}
  {@const n = deleting.lectures}
  {@const which = n === 0 ? "none" : n === 1 ? "one" : "other"}
  <Dialog title={t("deleteGroup.title", { name: deleting.name })} confirm={t("deleteGroup.confirm.course")} danger
          busy={deleteBusy} error={deleteError}
          text={t(deleting.current ? `deleteGroup.text.course.${which}` : `settings.deleteCourse.text.${which}`, { n, trash: trashName() })}
          oncancel={() => (deleting = null)} onconfirm={confirmDelete} />
{/if}

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
  .lead.small { color: var(--muted); font-size: 12.5px; margin: 0 0 10px; }
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
  .pill:hover:not(:disabled) { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .pill:disabled { opacity: 0.5; }
  .pill.danger { color: var(--rec); }
  .pill.danger:hover:not(:disabled) { border-color: color-mix(in srgb, var(--rec) 45%, var(--line)); }
  .actions { display: flex; gap: 8px; flex: none; }

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

  /* the courses: each one's ⋯ (Remove / Delete), and Add Course under the list */
  .more {
    flex: none; width: 26px; height: 26px; padding: 0; border: 0; border-radius: 6px; background: none; color: var(--muted);
    display: grid; place-items: center;
  }
  .more svg { width: 14px; height: 14px; fill: currentColor; display: block; }
  .more:hover, .more.open { color: var(--ink); background: color-mix(in srgb, var(--ink) 8%, transparent); }
  .add-course { display: inline-flex; align-items: center; gap: 6px; padding-left: 10px; }
  .add-course svg { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
  .menu {
    position: fixed; z-index: 40; min-width: 200px; padding: 5px; background: var(--card); border: 1px solid var(--line);
    border-radius: 10px; box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06), 0 12px 32px rgba(0, 0, 0, 0.12);
  }
  .menu button { display: block; width: 100%; text-align: left; border: 0; background: none; padding: 7px 10px; border-radius: 6px; }
  .menu button:hover { background: var(--mark); }
  .menu .danger { color: var(--rec); }
  .menu-line { height: 1px; background: var(--line); margin: 4px 6px; }

  .chips { display: flex; flex-wrap: wrap; gap: 6px; flex-basis: 100%; }
  .chip { border: 1px solid var(--line); background: none; border-radius: 999px; padding: 3px 11px; color: var(--ink-2); font-size: 12.5px; }
  .chip:hover { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .chip.on { background: var(--ink); border-color: var(--ink); color: var(--bg); }

  @media (prefers-reduced-motion: reduce) {
    .switch, .switch::after, .saved { transition: none; }
  }
</style>
