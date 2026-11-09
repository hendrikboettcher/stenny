<script lang="ts">
  // The library window: courses and talks on the left, their recordings in the middle, one recording on the right.
  import { onMount, untrack } from "svelte";
  import { cubicInOut } from "svelte/easing";
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import Figure from "$lib/Figure.svelte";
  import Dialog from "$lib/Dialog.svelte";
  import Settings from "$lib/Settings.svelte";
  import SetupChecklist from "$lib/SetupChecklist.svelte";
  import { renderMarkdown } from "$lib/markdown";
  import {
    library, lecture, makeCopy, makePdf, finishNow, openTranscript, reveal, shareFiles, lecturerAgreed,
    renameLecture, deleteLecture, renameCourse, addCourse, removeCourse, deleteCourse, copyText,
    type Entry, type Library,
  } from "$lib/engine";
  import { t, tn, locale, languageName, trashName, revealLabel, type Key } from "$lib/i18n.svelte";

  type Group = { course: string; event: boolean };

  let data = $state<Library | null>(null);
  let failed = $state<string | null>(null);
  let group = $state<Group | null>(null);
  let selectedId = $state<string | null>(null);
  let summary = $state<string | null | undefined>(undefined); // undefined: loading
  let progress = $state<string | null>(null); // the text so far, while there is no transcript yet
  let withSummary = $state(localStorage.getItem("withSummary") !== "false");
  let shareOpen = $state(false);
  let shareButton = $state<HTMLButtonElement | null>(null); // the share menu opens below it
  // Going into and out of Settings: the view coming in dissolves in on top of the one going away, which stays
  // put underneath (so the background never shows through halfway) and no longer takes clicks.
  const SWAP_MS = 240;
  const still = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  function comeIn(_: Element) {
    return { duration: still() ? 0 : SWAP_MS, easing: cubicInOut, css: (t: number) => `opacity: ${t}; z-index: 1;` };
  }
  function goAway(_: Element) {
    return { duration: still() ? 0 : SWAP_MS, css: () => "z-index: 0; pointer-events: none;" };
  }
  let settingsOpen = $state(false); // the footer's Settings…: the window shows Settings instead (lib/Settings.svelte)
  let settingsSection = $state<"general" | "transcripts" | "claude">("general"); // where Settings opens
  let linkCopied = $state(false); // the footer's Share Stenny: "Link copied" for a moment
  let copiedTimer: ReturnType<typeof setTimeout> | undefined;
  let busy = $state<string | null>(null);   // "share" | "finish"
  let notice = $state<{ text: string; path?: string; error?: boolean } | null>(null);

  // renaming, deleting, the course list: a right-click (or ⋯) menu, then a dialog
  type Menu = { x: number; y: number; group?: Group; entry?: Entry };
  type Ask =
    | { kind: "add" } | { kind: "rename-group"; group: Group } | { kind: "remove-course"; group: Group }
    | { kind: "delete-group"; group: Group }
    | { kind: "rename-lecture"; entry: Entry } | { kind: "delete-lecture"; entry: Entry };
  let menu = $state<Menu | null>(null);
  let ask = $state<Ask | null>(null);
  let askBusy = $state(false);
  let askError = $state<string | null>(null);
  let field = $state("");
  let alias = $state("");
  let language = $state("en");
  const LANGUAGES = ["en", "de", "fr", "mixed", "auto"];

  const id = (e: Entry) => `${e.kind}/${e.course}/${e.folder}`;
  const same = (a: Group | null, b: Group | null) => !!a && !!b && a.course === b.course && a.event === b.event;
  const isDraft = (e: Entry) => e.status !== "final";
  const isBusy = (e: Entry) => e.status === "live" || e.status === "finalizing"; // can't be renamed or deleted now
  // The courses in the config (in its order), then talks. Past courses (removed ones, and courses that only have
  // recordings) live in Settings ▸ Courses, where they can be brought back or deleted.
  let groups = $derived.by(() => {
    if (!data) return { courses: [] as Group[], talks: [] as Group[] };
    const talks = [...new Set(data.entries.filter((e) => e.kind === "event").map((e) => e.course))];
    return {
      courses: data.courses.filter((c) => !c.hidden).map((c) => ({ course: c.name, event: false })),
      talks: talks.sort().map((course) => ({ course, event: true })),
    };
  });

  let entries = $derived(
    data && group
      ? data.entries.filter((e) => e.course === group!.course && (e.kind === "event") === group!.event).reverse()
      : [],
  );
  let selected = $derived(entries.find((e) => id(e) === selectedId) ?? null);
  // what the detail view depends on: a reload every few seconds gives new objects, but the same key
  let selectedKey = $derived(selected ? `${id(selected)}:${selected.status}` : "");

  const groupOf = (e: Entry): Group => ({ course: e.course, event: e.kind === "event" });
  // what's recording now, else what's having its final transcript made (the newest of them)
  const running = (list: Entry[]) =>
    [...list].reverse().find((e) => e.status === "live") ?? [...list].reverse().find((e) => e.status === "finalizing");

  // `cameBack`: the window has just come back (opened, reloaded with ⌘R, shown again from the Dock or the menu
  // bar). Then a running recording is what it shows, so its text is right there. Otherwise the selection stays
  // (the refresh every few seconds never takes it away while someone is clicking around).
  // `show`: the entry to show once it's read (the menu bar's "recording" event), in the same step, so the list never
  // shows it with the old selection.
  // Several reloads can be under way at once (the window's focus, the timer, the menu bar's event), and the engine
  // answers each in its own thread: only the latest one counts, or an older answer could take a recording that just
  // appeared away again for a moment. What an older one was asked to show, the latest one shows.
  let reloads = 0;
  let wanted: { cameBack: boolean; show?: (all: Entry[]) => Entry | undefined } = { cameBack: false };
  async function reload({ cameBack = false, show }: { cameBack?: boolean; show?: (all: Entry[]) => Entry | undefined } = {}) {
    const mine = ++reloads;
    wanted = { cameBack: wanted.cameBack || cameBack, show: show ?? wanted.show };
    let next: Library;
    try {
      next = await library();
    } catch (e) {
      if (mine === reloads) failed = String(e);
      return;
    }
    if (mine !== reloads) return;
    const asked = wanted;
    wanted = { cameBack: false };
    data = next;
    failed = null;
    const all = [...groups.courses, ...groups.talks];
    const now = asked.show?.(next.entries) ?? (asked.cameBack ? running(next.entries) : undefined);
    if (now && all.some((g) => same(g, groupOf(now)))) {
      group = groupOf(now);
      selectedId = id(now);
      return;
    }
    if (!group || !all.some((g) => same(g, group))) group = all[0] ?? null;
    if (!selected) selectedId = entries[0] ? id(entries[0]) : null;
  }

  // From the menu bar (src-tauri/src/menubar.rs): the recording's state changed. The library is read again at once;
  // a recording that just started is shown, in its course, without waiting for a click into the window.
  type RecordingEvent = { state: string; course: string; kind: string; transcript: string; started: boolean };
  async function recordingChanged(r: RecordingEvent) {
    if (r.state === "starting") return; // its transcript file isn't there yet
    await reload({ show: r.started && !editing ? (all) => {
      const live = all.filter((e) => e.status === "live");
      return live.find((e) => e.transcript === r.transcript)
        ?? live.find((e) => e.course === r.course && (e.kind === "event") === (r.kind === "event"));
    } : undefined });
  }

  // The selection outlives the window (⌘R, quitting): it opens where it was when nothing records.
  const SELECTION = "selection";
  function lastSelection() {
    try {
      const saved = JSON.parse(localStorage.getItem(SELECTION) ?? "null");
      if (typeof saved?.group?.course === "string") {
        group = { course: saved.group.course, event: !!saved.group.event };
        selectedId = typeof saved.selectedId === "string" ? saved.selectedId : null;
      }
    } catch {}
  }
  $effect(() => {
    const saved = JSON.stringify({ group, selectedId });
    if (!group) return;
    try { localStorage.setItem(SELECTION, saved); } catch {}
  });

  function pick(g: Group) {
    group = g;
    selectedId = entries[0] ? id(entries[0]) : null;
  }

  // The summary of the selected recording, or its text so far while it has no transcript yet (looked at again
  // every 10 s, as it grows during a recording); reloaded when the selection or its status changes. A draft that
  // only moves on (live → waiting → being made) keeps its text on screen meanwhile instead of blinking "Loading…".
  // Otherwise "Loading…" shows only if it takes a moment: usually the text is there at once, and a word flashing up
  // in between reads as a flicker.
  let shownDraft = ""; // id() of the draft whose text is on screen
  let slow = $state(false);
  $effect(() => {
    const key = selectedKey;
    const entry = untrack(() => selected);
    const stillDraft = !!entry && isDraft(entry) && id(entry) === shownDraft;
    shownDraft = entry && isDraft(entry) ? id(entry) : "";
    if (!stillDraft) untrack(() => { summary = undefined; progress = null; notice = null; shareOpen = false; slow = false; });
    if (!key || !entry) return;
    let current = true;
    const load = () => lecture(entry)
      .then((r) => { if (current) { summary = r.summary; progress = r.progress; } })
      .catch(() => { if (current) summary = null; });
    load();
    const wait = setTimeout(() => { if (current) slow = true; }, 400);
    const timer = isDraft(entry) ? setInterval(load, 10000) : undefined;
    return () => { current = false; clearTimeout(wait); clearInterval(timer); };
  });

  $effect(() => localStorage.setItem("withSummary", String(withSummary)));

  // While a draft waits or finishes, look again now and then, so "final" shows up by itself.
  $effect(() => {
    if (!data?.entries.some((e) => e.status === "queued" || e.status === "finalizing" || e.status === "live")) return;
    const timer = setInterval(() => reload(), 5000);
    return () => clearInterval(timer);
  });

  // ⌘, (Ctrl+, on Windows) opens Settings, like in every app: the app menu's Settings… sends "open-settings"
  // (src-tauri/src/lib.rs); the key itself is caught here too, for Windows and a browser during development.
  const onWindows = typeof navigator !== "undefined" && /Windows/.test(navigator.userAgent);
  const settingsKey = onWindows ? "Ctrl+," : "⌘,";
  function settingsShortcut(e: KeyboardEvent) {
    if (e.key === "," && (onWindows ? e.ctrlKey : e.metaKey) && !e.altKey && !e.shiftKey) {
      e.preventDefault();
      openSettings();
    }
  }

  function openSettings(section: typeof settingsSection = "general") {
    settingsSection = section;
    settingsOpen = true;
  }

  onMount(() => {
    lastSelection();
    reload({ cameBack: true });
    const onFocus = () => reload();
    window.addEventListener("focus", onFocus);
    window.addEventListener("keydown", settingsShortcut);
    const unlisten: (() => void)[] = [];
    if ("__TAURI_INTERNALS__" in window) {
      listen("open-settings", () => openSettings()).then((u) => unlisten.push(u));
      listen<RecordingEvent>("recording", (e) => recordingChanged(e.payload)).then((u) => unlisten.push(u));
      // shown again from the Dock, Spotlight or the menu bar (src-tauri/src/lib.rs, `present`)
      listen("library-shown", () => reload({ cameBack: true })).then((u) => unlisten.push(u));
    }
    return () => {
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("keydown", settingsShortcut);
      unlisten.forEach((u) => u());
    };
  });

  function showMenu(ev: MouseEvent, target: { group?: Group; entry?: Entry }) {
    ev.preventDefault();
    ev.stopPropagation();
    shareOpen = false;
    const r = (ev.currentTarget as HTMLElement).getBoundingClientRect();
    // from a ⋯ button: under it; from a right-click: at the pointer
    const [x, y] = ev.type === "contextmenu" ? [ev.clientX, ev.clientY] : [r.left, r.bottom + 4];
    menu = { x: Math.min(x, window.innerWidth - 230), y: Math.min(y, window.innerHeight - 120), ...target };
  }

  // Renaming in place, like Notes or Notion: click the heading, type, Enter (Escape keeps the old name). A talk's
  // heading is the talk's name; a lecture's is its title, and an empty one (or "Lecture 5") goes back to the number.
  let editing = $state<string | null>(null); // id() of the entry whose heading is being edited
  let titleField = $state("");
  const headingOf = (e: Entry) => (e.kind === "event" ? e.course : rowTitle(e));

  function startRename(e: Entry) {
    if (isBusy(e) || editing) return;
    titleField = headingOf(e);
    editing = id(e);
  }

  function focusAll(input: HTMLInputElement) {
    input.focus();
    input.select();
  }

  async function finishRename(e: Entry, keep: boolean) {
    if (editing !== id(e)) return;
    editing = null;
    const next = titleField.trim().normalize("NFC");
    if (!keep || next === headingOf(e)) return;
    try {
      if (e.kind === "event") {
        if (!next) return;
        const { name } = await renameCourse(e.course, true, next);
        group = { course: name, event: true };
        selectedId = id({ ...e, course: name });
      } else {
        const back = !next || (e.number > 0 && next === t("lib.lectureN", { n: e.number }));
        if (back && !e.title) return;
        await renameLecture(e, back ? "" : next);
      }
      await reload();
    } catch (err) {
      notice = { text: t("lib.renameFailed", { error: String(err) }), error: true };
    }
  }

  function open(a: Ask) {
    menu = null;
    askError = null;
    askBusy = false;
    field = a.kind === "rename-group" ? a.group.course : a.kind === "rename-lecture" ? a.entry.title ?? "" : "";
    alias = "";
    language = "en";
    ask = a;
  }

  async function answer() {
    if (!ask) return;
    const a = ask;
    askBusy = true;
    askError = null;
    try {
      if (a.kind === "add") {
        await addCourse(field, language, alias);
        await reload();
        pick({ course: field.trim().normalize("NFC"), event: false });
      } else if (a.kind === "rename-group") {
        const { name } = await renameCourse(a.group.course, a.group.event, field);
        const wasOpen = same(group, a.group);
        await reload();
        if (wasOpen) group = { course: name, event: a.group.event };
      } else if (a.kind === "remove-course") {
        await removeCourse(a.group.course);
        await reload();
      } else if (a.kind === "delete-group") {
        await deleteCourse(a.group.course, a.group.event);
        if (same(group, a.group)) group = null;
        await reload();
      } else if (a.kind === "rename-lecture") {
        await renameLecture(a.entry, field);
        await reload();
      } else if (a.kind === "delete-lecture") {
        const at = entries.findIndex((e) => id(e) === id(a.entry));
        await deleteLecture(a.entry);
        await reload();
        const next = entries[Math.min(at, entries.length - 1)];
        selectedId = next ? id(next) : null;
      }
      ask = null;
    } catch (e) {
      askError = String(e);
    } finally {
      askBusy = false;
    }
  }

  // Share Stenny…: the website, where friends get it
  const STENNY_URL = "https://stenny.eu";
  async function shareStenny() {
    try {
      await copyText(STENNY_URL);
    } catch (e) {
      notice = { text: t("lib.copyFailed", { error: String(e), url: STENNY_URL }), error: true };
      return;
    }
    linkCopied = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (linkCopied = false), 1800);
  }

  const count = (g: Group) => data?.entries.filter((e) => e.course === g.course && (e.kind === "event") === g.event).length ?? 0;
  const lectureName = (e: Entry) =>
    e.title ? t("lib.quoted", { title: e.title }) : e.kind === "event" ? `${e.course} (${prettyDate(e, false)})`
      : t("lib.lectureOfCourse", { lecture: rowTitle(e), course: e.course });

  const prettyDate = (e: Entry, withWeekday = true) => {
    const d = new Date(`${e.date}T12:00:00`);
    if (isNaN(+d)) return e.date;
    const text = d.toLocaleDateString(locale(), { weekday: withWeekday ? "short" : undefined, day: "numeric", month: "short", year: "numeric" });
    const second = e.folder.length > 10 ? ` ${e.folder.slice(10).trim()}` : "";
    return text + second;
  };
  const rowTitle = (e: Entry) => e.title ?? (e.number > 0 ? t("lib.lectureN", { n: e.number }) : prettyDate(e));
  // always there, renamed or not, so a lecture keeps the same line whatever its title
  const number = (e: Entry) => (e.number > 0 ? t("lib.lectureN", { n: e.number }) : null);
  // "1:42:17" → "1 h 42 min": next to a clock time ("16:09") the two mustn't look alike
  const length = (duration: string | null) => {
    const parts = duration?.split(":").map(Number);
    if (!parts || parts.some(isNaN)) return duration;
    const [h, m, sec] = parts.length === 3 ? parts : [0, ...parts];
    if (!h && !m) return `${sec} s`;
    return h ? `${h} h${m ? ` ${m} min` : ""}` : `${m} min`;
  };
  const rowDetail = (e: Entry) =>
    [number(e), e.number > 0 || e.title ? prettyDate(e) : e.start, length(e.duration)].filter(Boolean).join(" · ");
  const details = (e: Entry) =>
    [number(e), prettyDate(e) + (e.start ? `, ${e.start}` : ""), length(e.duration), languageName(e.language)].filter(Boolean).join(" · ");
  const statusLabel = (status: string) =>
    t((["live", "queued", "finalizing"].includes(status) ? `lib.status.${status}` : "lib.status.draft") as Key);

  const FORMATS = [
    { id: "pdf", title: "PDF", help: "lib.format.pdf" },
    { id: "docx", title: "Word", help: "lib.format.docx" },
    { id: "md", title: "Markdown", help: "lib.format.md" },
  ] as const;

  async function share(format: string) {
    if (!selected) return;
    shareOpen = false;
    busy = "share";
    notice = null;
    try {
      if (!(await lecturerAgreed(selected, "share"))) return;
      // a PDF is made from the HTML copy (the engine can't paginate)
      const copy = await makeCopy(selected, format === "pdf" ? "html" : format, withSummary && !!selected.summary);
      const path = format === "pdf" ? await makePdf(copy.path) : copy.path;
      notice = { text: t("lib.made", { file: path.split(/[\\/]/).pop() ?? path }), path };
      // the system's share menu (AirDrop, Messages, Mail … / Windows' Share panel), at the Share button
      await shareFiles([path], shareButton!.getBoundingClientRect());
    } catch (e) {
      notice = { text: String(e), error: true };
    } finally {
      busy = null;
    }
  }

  async function finish() {
    if (!selected) return;
    busy = "finish";
    notice = null;
    try {
      // the engine marks the draft "Finishing" before it answers, so one reload shows it, button gone
      await finishNow(selected);
      await reload();
    } catch (e) {
      notice = { text: String(e), error: true };
    } finally {
      busy = null;
    }
  }
</script>

<svelte:window onclick={() => { shareOpen = false; menu = null; }}
               onkeydown={(e) => { if (e.key === "Escape") { shareOpen = false; menu = null; } }}
               onblur={() => (menu = null)} />

{#if settingsOpen}
  <div class="view" in:comeIn out:goAway>
    <Settings start={settingsSection} onclose={() => { settingsOpen = false; reload(); }} />
  </div>
{:else}
<div class="view" in:comeIn out:goAway>
<div class="window">
  <aside class="sidebar">
    <div class="titlebar" data-tauri-drag-region></div>
    <div class="brand" data-tauri-drag-region>
      <Figure height={24} />
      <span class="serif">Stenny</span>
    </div>

    <nav>
      {#snippet row(g: Group)}
        <div class="group-row" class:active={same(g, group)} oncontextmenu={(ev) => showMenu(ev, { group: g })}>
          <button class="group" onclick={() => pick(g)} title={g.course}>{g.course}</button>
          <button class="more" aria-label={t("lib.optionsFor", { course: g.course })} title={t("lib.options")}
                  onclick={(ev) => showMenu(ev, { group: g })}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.25" /><circle cx="8" cy="8" r="1.25" /><circle cx="12.5" cy="8" r="1.25" /></svg>
          </button>
        </div>
      {/snippet}
      {#snippet section(title: string, list: Group[])}
        {#if list.length}
          <div class="kicker">{title}</div>
          {#each list as g (g.course + g.event)}{@render row(g)}{/each}
        {/if}
      {/snippet}
      <div class="kicker with-add">
        <span>{t("lib.courses")}</span>
        <button class="add" aria-label={t("lib.addCourse")} title={t("lib.addCourse")} onclick={() => open({ kind: "add" })}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.5v9M3.5 8h9" /></svg>
        </button>
      </div>
      {#each groups.courses as g (g.course)}{@render row(g)}{/each}
      {#if data && !groups.courses.length}
        <button class="group quiet-add" onclick={() => open({ kind: "add" })}>{t("lib.addFirstCourse")}</button>
      {/if}
      {@render section(t("common.talksEvents"), groups.talks)}
    </nav>
    <!-- "Finish setting up Stenny", until courses, the AI step and the connector work (lib/SetupChecklist.svelte) -->
    <SetupChecklist courses={data ? groups.courses.length : null} onaddcourse={() => open({ kind: "add" })}
                    onsettings={openSettings} />
    <footer>
      <button class="with-key" onclick={() => openSettings()}>
        <span>{t("lib.settings")}</span><kbd aria-label={onWindows ? "Control comma" : "Command comma"}>{settingsKey}</kbd>
      </button>
      <button onclick={() => invoke("show_feedback")}>{t("lib.feedback")}</button>
      <button onclick={() => invoke("check_updates")}>{t("lib.updates")}</button>
      <!-- copies stenny.eu, where friends get it; says so for a moment -->
      <button class="share-stenny" class:copied={linkCopied} onclick={shareStenny} title={t("lib.shareStenny.title")}
              aria-label={linkCopied ? t("lib.linkCopied") : t("lib.shareStenny.aria")}>
        <span class="icon" aria-hidden="true">
          <svg class="idle" viewBox="0 0 16 16"><path d="M5.5 6H4.5a1 1 0 0 0-1 1v6.5a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1h-1M8 1.5v8.5M5.5 4 8 1.5 10.5 4" /></svg>
          <svg class="done" viewBox="0 0 16 16"><path d="M3.5 8.5 6.5 11.5 12.5 4.5" /></svg>
        </span>
        <span class="labels">
          <span class="idle">{t("lib.shareStenny")}</span>
          <span class="done">{t("lib.linkCopied")}</span>
        </span>
      </button>
    </footer>
  </aside>

  <section class="list">
    <div class="titlebar" data-tauri-drag-region></div>
    {#if group}
      <div class="kicker list-head">{tn("lib.recordings", entries.length)}</div>
      {#each entries as e (id(e))}
        <button class="row" class:active={id(e) === selectedId} onclick={() => (selectedId = id(e))}
                oncontextmenu={(ev) => { selectedId = id(e); showMenu(ev, { entry: e }); }}>
          <span class="row-title serif">{rowTitle(e)}</span>
          {#if isDraft(e)}<span class="status {e.status}">{statusLabel(e.status)}</span>{/if}
          <span class="row-detail">{rowDetail(e)}</span>
        </button>
      {:else}
        <p class="empty">{t("lib.noRecordings")}</p>
      {/each}
    {/if}
    <div class="bottombar" aria-hidden="true"></div>
  </section>

  <main class="detail">
    <div class="titlebar" data-tauri-drag-region></div>
    <!-- deleting: an icon right after the other actions, like Mail's; it asks first, and it's the Trash -->
    {#snippet trashButton(e: Entry)}
      <button class="trash" aria-label={t("lib.trashButton", { trash: trashName() })}
              title={t("lib.trashButton", { trash: trashName() })}
              onclick={() => open({ kind: "delete-lecture", entry: e })}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.8 4.3h10.4 M6.3 4.3V2.8h3.4v1.5 M4.3 4.3l.6 8.6a1 1 0 0 0 1 .9h4.2a1 1 0 0 0 1-.9l.6-8.6 M6.7 6.8v4.6 M9.3 6.8v4.6" /></svg>
      </button>
    {/snippet}
    {#if selected}
      <article>
        <div class="kicker">{selected.kind === "event" ? t("lib.talk") : selected.course}</div>
        <!-- click the heading to rename (see startRename); the pencil only shows while pointing at it -->
        {#if editing === id(selected)}
          {@const e = selected}
          <!-- the field is as wide as its text: a hidden copy of the text (::after) sizes the grid it shares -->
          <div class="title-wrap serif" data-value={titleField || (e.kind === "event" ? "" : e.number > 0 ? t("lib.lectureN", { n: e.number }) : prettyDate(e))}>
          <input class="title-input" size="1" bind:value={titleField} use:focusAll
                 aria-label={e.kind === "event" ? t("lib.rename.talkName") : t("lib.rename.lectureTitle")}
                 placeholder={e.kind === "event" ? "" : e.number > 0 ? t("lib.lectureN", { n: e.number }) : prettyDate(e)}
                 onkeydown={(ev) => { if (ev.key === "Enter") finishRename(e, true); if (ev.key === "Escape") { ev.stopPropagation(); finishRename(e, false); } }}
                 onblur={() => finishRename(e, true)} />
          </div>
        {:else if isBusy(selected)}
          <h1 class="serif selectable">{headingOf(selected)}</h1>
        {:else}
          {@const e = selected}
          <h1 class="serif"><button class="title-button" title={t("lib.rename.click")}
              aria-label={t("lib.rename.aria", { heading: headingOf(e) })} onclick={() => startRename(e)}>{headingOf(e)}<svg
              class="pen" viewBox="0 0 16 16" aria-hidden="true"><path d="M10.6 2.6a1.4 1.4 0 0 1 2 0l.8.8a1.4 1.4 0 0 1 0 2L6 12.8 2.8 13.2l.4-3.2z M9.4 3.8l2.8 2.8" /></svg></button></h1>
        {/if}
        <p class="meta">{details(selected)}</p>

        {#if isDraft(selected)}
          <div class="draft">
            <p class="serif">
              <span class="status {selected.status}">{statusLabel(selected.status)}</span>
              {selected.draft_note ?? t("lib.comesLater")}
            </p>
            {#if selected.status === "queued"}
              <button class="pill small" onclick={finish} disabled={busy === "finish"}>
                {busy === "finish" ? t("common.starting") : t("lib.finishNow")}
              </button>
            {/if}
            {#if !isBusy(selected)}{@render trashButton(selected)}{/if}
          </div>
        {/if}

        {#if !isDraft(selected)}
        <div class="actions">
          <div class="share-wrap">
            <button class="pill primary" disabled={busy === "share"} bind:this={shareButton}
                    onclick={(ev) => { ev.stopPropagation(); shareOpen = !shareOpen; }}>
              {busy === "share" ? t("lib.preparing") : t("lib.share")}
            </button>
            {#if shareOpen}
              <div class="menu" role="menu">
                {#each FORMATS as f}
                  <button role="menuitem" onclick={() => share(f.id)}>
                    <span>{f.title}</span><small>{t(f.help)}</small>
                  </button>
                {/each}
              </div>
            {/if}
          </div>
          <button class="pill" onclick={() => openTranscript(selected!.transcript)}>{t("lib.openTranscript")}</button>
          <button class="pill" onclick={() => reveal(selected!.transcript)}>{revealLabel()}</button>
          {@render trashButton(selected)}
        </div>
        {/if}
        {#if selected.summary && !isDraft(selected)}
          <label class="check">
            <input type="checkbox" bind:checked={withSummary} /> {t("lib.summaryOnTop")}
          </label>
        {/if}
        {#if notice}
          <p class="notice" class:error={notice.error}>
            {notice.text}{#if notice.path}. <button class="link" onclick={() => reveal(notice!.path!)}>{revealLabel()}</button>{/if}
          </p>
        {/if}

        <hr />

        {#if summary === undefined}
          <p class="quiet serif" class:hidden={!slow}>{t("common.loading")}</p>
        {:else if isDraft(selected)}
          {#if progress}
            <div class="kicker">{t("lib.soFar")}</div>
            <div class="summary serif selectable">{@html renderMarkdown(progress)}</div>
          {:else}
            <p class="quiet serif">{t("lib.nothingYet")} {selected.status === "live" ? t("lib.nothingYet.live") : t("lib.nothingYet.later")}</p>
          {/if}
        {:else if summary}
          <div class="kicker">{t("lib.summary")}</div>
          <div class="summary serif selectable">{@html renderMarkdown(summary)}</div>
        {:else}
          <p class="quiet serif">{selected.kind === "event" ? t("lib.noSummary.event") : t("lib.noSummary.lecture")}</p>
        {/if}
      </article>
    {:else if failed}
      <div class="center">
        <p class="serif big">{t("lib.readFailed")}</p>
        <p class="quiet selectable">{failed}</p>
        <button class="pill" onclick={() => reload()}>{t("lib.tryAgain")}</button>
      </div>
    {:else if data && group}
      <div class="center">
        <p class="serif big">{t("lib.emptyCourse", { course: group.course })}</p>
        <p class="quiet">{t("lib.emptyCourse.hint")}</p>
      </div>
    {:else if !data}
      <div class="center"><p class="quiet serif">{t("common.loading")}</p></div>
    {/if}
    <div class="bottombar" aria-hidden="true"></div>
  </main>
</div>
</div>
{/if}

{#if menu}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="menu floating" role="menu" style="left: {menu.x}px; top: {menu.y}px" onclick={(ev) => ev.stopPropagation()}>
    {#if menu.group}
      {@const g = menu.group}
      <button role="menuitem" onclick={() => open({ kind: "rename-group", group: g })}>
        <span>{t("lib.menu.rename")}</span>
      </button>
      {#if !g.event}
        <button role="menuitem" onclick={() => open({ kind: "remove-course", group: g })}>
          <span>{t("lib.menu.removeCourse")}</span>
        </button>
      {/if}
      <div class="menu-line"></div>
      <button role="menuitem" class="danger" onclick={() => open({ kind: "delete-group", group: g })}>
        <span>{g.event ? t("lib.menu.deleteTalk") : t("lib.menu.deleteCourse")}</span>
      </button>
    {:else if menu.entry}
      {@const e = menu.entry}
      {#if isBusy(e)}
        <p class="menu-note">{e.status === "live" ? t("lib.menu.stillRecording") : t("lib.menu.beingMade")}</p>
      {:else}
        <button role="menuitem" onclick={() => open({ kind: "rename-lecture", entry: e })}>
          <span>{t("lib.menu.rename")}</span>
        </button>
        <button role="menuitem" class="danger" onclick={() => open({ kind: "delete-lecture", entry: e })}>
          <span>{t("lib.menu.moveToTrash", { trash: trashName() })}</span>
        </button>
      {/if}
    {/if}
  </div>
{/if}

{#if ask}
  {#if ask.kind === "add"}
    <Dialog title={t("add.title")} confirm={t("add.confirm")} busy={askBusy} error={askError} text={t("add.text")}
            oncancel={() => (ask = null)} onconfirm={answer}>
      <label for="course-name">{t("add.name")}</label>
      <input id="course-name" bind:value={field} placeholder={t("add.namePlaceholder")} autocomplete="off" spellcheck="false" />
      <label for="course-language">{t("common.language")}</label>
      <select id="course-language" bind:value={language}>
        {#each LANGUAGES as code}<option value={code}>{languageName(code)}</option>{/each}
      </select>
      <label for="course-alias">{t("add.alias")}</label>
      <input id="course-alias" bind:value={alias} placeholder={t("add.aliasPlaceholder")} autocomplete="off" spellcheck="false" />
    </Dialog>
  {:else if ask.kind === "rename-group"}
    <Dialog title={ask.group.event ? t("renameGroup.title.event") : t("renameGroup.title.course")} confirm={t("common.rename")}
            busy={askBusy} error={askError}
            text={ask.group.event ? t("renameGroup.text.event") : t("renameGroup.text.course")}
            oncancel={() => (ask = null)} onconfirm={answer}>
      <input bind:value={field} aria-label={t("renameGroup.aria")} autocomplete="off" spellcheck="false" />
    </Dialog>
  {:else if ask.kind === "remove-course"}
    {@const n = count(ask.group)}
    <Dialog title={t("remove.title", { course: ask.group.course })} confirm={t("remove.confirm")} busy={askBusy} error={askError}
            text={t(n === 0 ? "remove.text.none" : n === 1 ? "remove.text.one" : "remove.text.other", { n })}
            oncancel={() => (ask = null)} onconfirm={answer} />
  {:else if ask.kind === "delete-group"}
    {@const n = count(ask.group)}
    {@const what = ask.group.event ? "event" : "course"}
    <Dialog title={t("deleteGroup.title", { name: ask.group.course })} confirm={t(`deleteGroup.confirm.${what}`)} danger
            busy={askBusy} error={askError}
            text={t(`deleteGroup.text.${what}.${n === 0 ? "none" : n === 1 ? "one" : "other"}`, { n, trash: trashName() })}
            oncancel={() => (ask = null)} onconfirm={answer} />
  {:else if ask.kind === "rename-lecture"}
    {@const n = ask.entry.number}
    <Dialog title={n > 0 ? t("renameLecture.title.number", { n }) : t("renameLecture.title.other")} confirm={t("common.rename")}
            busy={askBusy} error={askError}
            text={n > 0 ? t("renameLecture.text.number", { n }) : t("renameLecture.text.other")}
            oncancel={() => (ask = null)} onconfirm={answer}>
      <input bind:value={field} aria-label={t("renameLecture.aria")} placeholder={t("renameLecture.placeholder")} autocomplete="off" />
    </Dialog>
  {:else if ask.kind === "delete-lecture"}
    <Dialog title={t("deleteLecture.title", { name: lectureName(ask.entry), trash: trashName() })}
            confirm={t("deleteLecture.confirm", { trash: trashName() })} danger busy={askBusy} error={askError}
            text={t(ask.entry.kind === "lecture" ? "deleteLecture.text.lecture" : "deleteLecture.text.event", { trash: trashName() })}
            oncancel={() => (ask = null)} onconfirm={answer} />
  {/if}
{/if}

<style>
  /* the library and Settings fill the window; both are there for a moment while one dissolves into the other */
  .view { position: fixed; inset: 0; background: var(--bg); }
  .window { display: grid; grid-template-columns: 220px 290px minmax(0, 1fr); height: 100vh; }
  .titlebar { height: var(--titlebar); flex: none; }
  /* the scrolling columns keep a solid band under the window's title bar, like the sidebar's */
  .list > .titlebar, .detail > .titlebar { position: sticky; top: 0; z-index: 5; background: var(--bg); }
  .list > .titlebar { margin: 0 -10px; }
  /* ... and at the bottom, so text never runs into the window's rounded corner */
  .bottombar { position: sticky; bottom: 0; height: 18px; flex: none; background: var(--bg); z-index: 5; }
  .list > .bottombar { margin: 0 -10px; }

  /* Sidebar */
  .sidebar { background: var(--bar); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
  .brand { display: flex; align-items: center; gap: 9px; padding: 6px 20px 22px; }
  .brand span { font-size: 22px; font-weight: 460; letter-spacing: -0.01em; }
  nav { overflow-y: auto; padding: 0 10px 20px; flex: 1; }
  footer { padding: 10px 10px 14px; border-top: 1px solid var(--line); display: flex; flex-direction: column; }
  footer button { border: 0; background: none; text-align: left; padding: 4px 10px; color: var(--muted); border-radius: 6px; }
  footer button:hover { color: var(--ink); }
  /* Settings… with its shortcut on the right, like in a menu */
  footer .with-key { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
  footer kbd { font-family: var(--sans); font-size: 11.5px; color: var(--muted); opacity: 0.8; letter-spacing: 0.02em; }
  /* Share Stenny: the share symbol and label turn into a check and "Link copied", crossfading in place */
  footer .share-stenny { display: flex; align-items: center; gap: 6px; }
  .share-stenny .icon, .share-stenny .labels { display: grid; }
  .share-stenny .icon > *, .share-stenny .labels > * { grid-area: 1 / 1; transition: opacity 0.22s ease, transform 0.22s ease; }
  .share-stenny svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.4; stroke-linecap: round; stroke-linejoin: round; }
  .share-stenny .done { opacity: 0; transform: translateY(4px); }
  .share-stenny.copied { color: var(--good); }
  .share-stenny.copied .idle { opacity: 0; transform: translateY(-4px); }
  .share-stenny.copied .done { opacity: 1; transform: none; }
  @media (prefers-reduced-motion: reduce) {
    .share-stenny .icon > *, .share-stenny .labels > * { transition: none; transform: none; }
  }
  .kicker { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
  nav .kicker { padding: 16px 10px 6px; }
  nav .kicker:first-of-type { padding-top: 0; }
  .group {
    display: block; width: 100%; text-align: left; border: 0; background: none; padding: 6px 10px; border-radius: 7px;
    font-family: var(--serif); font-size: 15px; line-height: 1.25; color: var(--ink-2); text-wrap: pretty; hyphens: auto; overflow-wrap: break-word;
  }
  .group:hover { color: var(--ink); }
  .group-row { position: relative; border-radius: 7px; }
  .group-row.active { background: var(--mark); }
  .group-row.active .group { color: var(--ink); }
  .group-row .group { padding-right: 28px; }
  .more, .add {
    position: absolute; right: 4px; top: 50%; transform: translateY(-50%); border: 0; background: none;
    color: var(--muted); width: 22px; height: 22px; border-radius: 5px; padding: 0;
    display: grid; place-items: center;  /* drawn icons, so they sit in the middle (text glyphs sit on the baseline) */
  }
  .more svg, .add svg { width: 14px; height: 14px; display: block; }
  .more svg { fill: currentColor; }
  .add svg { fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; }
  .more { opacity: 0; }
  .group-row:hover .more, .more:focus-visible { opacity: 1; }
  .more:hover, .add:hover { color: var(--ink); background: color-mix(in srgb, var(--ink) 8%, transparent); }
  /* "Courses" and its +: the + centred on the label's line, its right edge where the rows' ⋯ sits */
  nav .kicker.with-add { display: flex; align-items: center; justify-content: space-between; padding-bottom: 7px; }
  /* no negative margin above: the list scrolls, and its edge would cut the top of the hover box off */
  .add { position: static; transform: none; width: 20px; height: 20px; margin: 0 -6px 0 0; flex: none; }
  .quiet-add { color: var(--muted); font-size: 14px; }

  /* List */
  .list { border-right: 1px solid var(--line); overflow-y: auto; padding: 0 10px; }  /* the bottom band is its end space */
  .list-head { padding: 0 12px 10px; }
  .row {
    display: grid; grid-template-columns: 1fr auto; align-items: baseline; gap: 2px 8px; width: 100%; text-align: left;
    border: 0; background: none; padding: 10px 12px; border-radius: 9px;
  }
  .row:hover { background: color-mix(in srgb, var(--mark) 50%, transparent); }
  .row.active { background: var(--mark); }
  .row-title { font-size: 17px; font-weight: 440; }
  .row-detail { grid-column: 1 / -1; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .empty { color: var(--muted); padding: 0 12px; font-family: var(--serif); font-size: 15px; }

  .status { font-family: var(--sans); font-size: 10.5px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--warn); }
  .status::before { content: ""; display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: currentColor; margin-right: 5px; vertical-align: 1px; }
  .status.live { color: var(--rec); }

  /* Detail */
  .detail { overflow-y: auto; min-width: 0; }
  article { max-width: 660px; padding: 4px 48px 56px; }
  h1 { font-size: 34px; line-height: 1.1; font-weight: 440; letter-spacing: -0.02em; margin: 6px 0 6px; }
  .meta { color: var(--muted); margin: 0; font-variant-numeric: tabular-nums; }

  .draft { margin-top: 22px; padding: 2px 0 2px 14px; border-left: 2px solid var(--warn); }
  .draft p { margin: 0; font-size: 15px; line-height: 1.5; }
  .draft .status { margin-right: 6px; }
  .draft .pill, .draft .trash { margin-top: 10px; }
  .draft .trash { margin-left: 4px; vertical-align: top; }

  /* the heading renames in place: pointing at it shows a soft background and a faint pencil, clicking edits it */
  .title-button {
    font: inherit; letter-spacing: inherit; color: inherit; text-align: left; border: 0; background: none;
    padding: 0 8px; margin: 0 -8px; border-radius: 8px; cursor: text; max-width: calc(100% + 16px);
  }
  .title-button:hover, .title-button:focus-visible { background: color-mix(in srgb, var(--ink) 5%, transparent); }
  .pen {
    display: inline-block; width: 16px; height: 16px; margin-left: 10px; vertical-align: 4px; opacity: 0;
    fill: none; stroke: var(--muted); stroke-width: 1.3; stroke-linecap: round; stroke-linejoin: round; transition: opacity 0.15s;
  }
  .title-button:hover .pen, .title-button:focus-visible .pen { opacity: 1; }
  /* exactly where the heading is: a block like the h1 (so its margin meets the kicker's the same way), and 1px less
     margin on each side to make up for the field's border, so neither the title nor the line below moves */
  .title-wrap {
    display: grid; width: fit-content; max-width: calc(100% + 16px); margin: 5px -8px 5px;
    font-size: 34px; line-height: 1.1; font-weight: 440; letter-spacing: -0.02em;
  }
  .title-wrap::after, .title-input {
    grid-area: 1 / 1; font: inherit; letter-spacing: inherit; padding: 0 7px; border: 1px solid transparent; min-width: 0;
  }
  .title-wrap::after { content: attr(data-value) "\00a0"; visibility: hidden; white-space: pre; overflow: hidden; }
  .title-input {
    width: 100%; border-color: var(--line); border-radius: 8px; background: var(--card); color: var(--ink); outline: none;
  }
  .title-input:focus { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }

  /* deleting: a round icon button in the actions' row, grey until pointed at */
  .trash {
    width: 33px; height: 33px; padding: 0; flex: none; border-radius: 999px; display: inline-grid; place-items: center;
    border: 1px solid var(--line); background: var(--card); color: var(--muted);  /* as tall as the pills beside it */
  }
  .trash svg { display: block; width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.3; stroke-linecap: round; stroke-linejoin: round; }
  .trash:hover { color: var(--rec); border-color: color-mix(in srgb, var(--rec) 40%, var(--line)); }

  .actions { display: flex; gap: 8px; margin-top: 24px; flex-wrap: wrap; }
  .pill {
    border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 6px 15px; font-weight: 500;
  }
  .pill:hover:not(:disabled) { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .pill.primary { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .pill.small { padding: 4px 12px; font-size: 12px; }
  .pill:disabled { opacity: 0.55; }
  .share-wrap { position: relative; }
  .menu {
    position: absolute; top: calc(100% + 6px); left: 0; z-index: 10; min-width: 240px; padding: 5px;
    background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06), 0 12px 32px rgba(0, 0, 0, 0.12);
  }
  .menu button { display: flex; flex-direction: column; width: 100%; text-align: left; border: 0; background: none; padding: 7px 10px; border-radius: 6px; }
  .menu button:hover { background: var(--mark); }
  .menu small { color: var(--muted); font-size: 11.5px; }
  .menu.floating { position: fixed; top: 0; left: 0; z-index: 40; min-width: 200px; max-width: 260px; }
  .menu .danger { color: var(--rec); }
  .menu-line { height: 1px; background: var(--line); margin: 4px 6px; }
  .menu-note { margin: 0; padding: 7px 10px; color: var(--muted); font-size: 12px; line-height: 1.4; }
  .check { display: flex; align-items: center; gap: 7px; margin-top: 12px; color: var(--muted); }
  .check input { accent-color: var(--ink); margin: 0; }
  .notice { margin: 12px 0 0; color: var(--muted); }
  .notice.error { color: var(--rec); }
  .link { border: 0; background: none; padding: 0; text-decoration: underline; text-underline-offset: 2px; color: inherit; }

  hr { border: 0; border-top: 1px solid var(--line); margin: 30px 0 26px; }
  .quiet { color: var(--muted); }
  .hidden { visibility: hidden; }  /* keeps its line, so nothing moves when it shows */
  .detail .kicker { margin-bottom: 4px; }

  .summary { font-size: 16px; line-height: 1.6; }
  .summary :global(h2) { font-size: 21px; font-weight: 480; margin: 22px 0 6px; letter-spacing: -0.01em; }
  .summary :global(h3) { font-size: 17px; font-weight: 520; margin: 18px 0 4px; }
  .summary :global(p) { margin: 0 0 10px; }
  .summary :global(ul) { margin: 0 0 12px; padding-left: 20px; }
  .summary :global(li) { margin: 0 0 5px; }
  .summary :global(blockquote) { margin: 0 0 14px; padding-left: 12px; border-left: 2px solid var(--line); color: var(--muted); font-size: 14px; }
  .summary :global(.ts) { font-family: var(--sans); font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; margin-left: 2px; }
  .summary :global(strong) { font-weight: 560; }

  .center { height: calc(100% - var(--titlebar) - 18px); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; text-align: center; padding: 0 40px; }
  .big { font-size: 20px; margin: 0; }
  .center p { margin: 0; }
</style>
