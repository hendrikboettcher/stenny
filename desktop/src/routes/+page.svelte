<script lang="ts">
  // The library window: courses and talks on the left, their recordings in the middle, one recording on the right.
  import { onMount, untrack } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import Figure from "$lib/Figure.svelte";
  import Dialog from "$lib/Dialog.svelte";
  import { renderMarkdown } from "$lib/markdown";
  import {
    library, lecture, makeCopy, makePdf, finishNow, openTranscript, reveal, lecturerAgreed,
    renameLecture, deleteLecture, renameCourse, addCourse, removeCourse, deleteCourse, bringBack, copyText,
    type Entry, type Library,
  } from "$lib/engine";

  type Group = { course: string; event: boolean };

  let data = $state<Library | null>(null);
  let failed = $state<string | null>(null);
  let group = $state<Group | null>(null);
  let selectedId = $state<string | null>(null);
  let summary = $state<string | null | undefined>(undefined); // undefined: loading
  let progress = $state<string | null>(null); // the text so far, while there is no transcript yet
  let withSummary = $state(localStorage.getItem("withSummary") !== "false");
  let shareOpen = $state(false);
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
  const LANGUAGES = [["en", "English"], ["de", "German"], ["fr", "French"], ["mixed", "Mixed"], ["auto", "Auto"]];

  const id = (e: Entry) => `${e.kind}/${e.course}/${e.folder}`;
  const same = (a: Group | null, b: Group | null) => !!a && !!b && a.course === b.course && a.event === b.event;
  const isDraft = (e: Entry) => e.status !== "final";
  const isBusy = (e: Entry) => e.status === "live" || e.status === "finalizing"; // can't be renamed or deleted now
  const configured = (g: Group) => !g.event && !!data?.courses.some((c) => c.name === g.course && !c.hidden);

  // Courses from the config first (in its order), then past courses that only have recordings, then talks.
  let groups = $derived.by(() => {
    if (!data) return { courses: [] as Group[], past: [] as Group[], talks: [] as Group[] };
    const shown = data.courses.filter((c) => !c.hidden).map((c) => c.name);
    const recorded = (event: boolean) =>
      [...new Set(data!.entries.filter((e) => (e.kind === "event") === event).map((e) => e.course))];
    return {
      courses: shown.map((course) => ({ course, event: false })),
      past: recorded(false).filter((c) => !shown.includes(c)).sort().map((course) => ({ course, event: false })),
      talks: recorded(true).sort().map((course) => ({ course, event: true })),
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

  async function reload() {
    try {
      data = await library();
      failed = null;
    } catch (e) {
      failed = String(e);
      return;
    }
    const all = [...groups.courses, ...groups.past, ...groups.talks];
    if (!group || !all.some((g) => same(g, group))) group = all[0] ?? null;
    if (!selected) selectedId = entries[0] ? id(entries[0]) : null;
  }

  function pick(g: Group) {
    group = g;
    selectedId = entries[0] ? id(entries[0]) : null;
  }

  // The summary of the selected recording, or its text so far while it has no transcript yet (looked at again
  // every 10 s, as it grows during a recording); reloaded when the selection or its status changes.
  $effect(() => {
    const key = selectedKey;
    const entry = untrack(() => selected);
    untrack(() => { summary = undefined; progress = null; notice = null; shareOpen = false; });
    if (!key || !entry) return;
    let current = true;
    const load = () => lecture(entry)
      .then((r) => { if (current) { summary = r.summary; progress = r.progress; } })
      .catch(() => { if (current) summary = null; });
    load();
    const timer = isDraft(entry) ? setInterval(load, 10000) : undefined;
    return () => { current = false; clearInterval(timer); };
  });

  $effect(() => localStorage.setItem("withSummary", String(withSummary)));

  // While a draft waits or finishes, look again now and then, so "final" shows up by itself.
  $effect(() => {
    if (!data?.entries.some((e) => e.status === "queued" || e.status === "finalizing" || e.status === "live")) return;
    const timer = setInterval(reload, 5000);
    return () => clearInterval(timer);
  });

  onMount(() => {
    reload();
    const onFocus = () => reload();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
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

  async function bring(g: Group) {
    menu = null;
    try {
      await bringBack(g.course);
      await reload();
    } catch (e) {
      notice = { text: String(e), error: true };
    }
  }

  // Share Stenny…: the website, where friends get it
  const STENNY_URL = "https://stenny.eu";
  async function shareStenny() {
    try {
      await copyText(STENNY_URL);
    } catch (e) {
      notice = { text: `Couldn't copy the link (${e}). It's ${STENNY_URL}.`, error: true };
      return;
    }
    linkCopied = true;
    clearTimeout(copiedTimer);
    copiedTimer = setTimeout(() => (linkCopied = false), 1800);
  }

  const count = (g: Group) => data?.entries.filter((e) => e.course === g.course && (e.kind === "event") === g.event).length ?? 0;
  const lectureName = (e: Entry) =>
    e.title ? `“${e.title}”` : e.kind === "event" ? `${e.course} (${prettyDate(e, false)})` : `${rowTitle(e)} of ${e.course}`;

  const languageName = (code: string | null) => {
    if (!code) return null;
    try { return new Intl.DisplayNames(["en"], { type: "language" }).of(code) ?? code; } catch { return code; }
  };
  const prettyDate = (e: Entry, withWeekday = true) => {
    const d = new Date(`${e.date}T12:00:00`);
    if (isNaN(+d)) return e.date;
    const text = d.toLocaleDateString("en-GB", { weekday: withWeekday ? "short" : undefined, day: "numeric", month: "short", year: "numeric" });
    const second = e.folder.length > 10 ? ` ${e.folder.slice(10).trim()}` : "";
    return text + second;
  };
  const rowTitle = (e: Entry) => e.title ?? (e.number > 0 ? `Lecture ${e.number}` : prettyDate(e));
  const number = (e: Entry) => (e.title && e.number > 0 ? `Lecture ${e.number}` : null); // its title took the number's place
  const rowDetail = (e: Entry) =>
    [number(e), e.number > 0 || e.title ? prettyDate(e) : e.start, e.duration].filter(Boolean).join(" · ");
  const details = (e: Entry) =>
    [number(e), prettyDate(e) + (e.start ? `, ${e.start}` : ""), e.duration, languageName(e.language)].filter(Boolean).join(" · ");
  const statusLabel: Record<string, string> = {
    live: "Recording", queued: "Waiting", finalizing: "Finishing", draft: "Draft",
  };

  const FORMATS = [
    { id: "pdf", title: "PDF", help: "Opens on any phone or computer" },
    { id: "docx", title: "Word", help: "To read, print or edit" },
    { id: "md", title: "Markdown", help: "For notes apps and AI chats" },
  ];

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
      notice = { text: `Saved ${path.split("/").pop()}`, path };
      await reveal(path);
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
      await finishNow(selected);
      // it shows as "Finishing" once the final pass has taken the draft; until then the button says "Starting…"
      setTimeout(() => { reload(); busy = null; }, 2500);
    } catch (e) {
      notice = { text: String(e), error: true };
      busy = null;
    }
  }
</script>

<svelte:window onclick={() => { shareOpen = false; menu = null; }}
               onkeydown={(e) => { if (e.key === "Escape") { shareOpen = false; menu = null; } }}
               onblur={() => (menu = null)} />

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
          <button class="more" aria-label="Options for {g.course}" title="Options"
                  onclick={(ev) => showMenu(ev, { group: g })}>⋯</button>
        </div>
      {/snippet}
      {#snippet section(title: string, list: Group[])}
        {#if list.length}
          <div class="kicker">{title}</div>
          {#each list as g (g.course + g.event)}{@render row(g)}{/each}
        {/if}
      {/snippet}
      <div class="kicker with-add">
        <span>Courses</span>
        <button class="add" aria-label="Add Course" title="Add a course" onclick={() => open({ kind: "add" })}>+</button>
      </div>
      {#each groups.courses as g (g.course)}{@render row(g)}{/each}
      {#if data && !groups.courses.length}
        <button class="group quiet-add" onclick={() => open({ kind: "add" })}>Add your first course</button>
      {/if}
      {@render section("Past courses", groups.past)}
      {@render section("Talks & events", groups.talks)}
    </nav>
    <footer>
      <button onclick={() => invoke("show_settings")}>Settings…</button>
      <button onclick={() => invoke("show_feedback")}>Send Feedback…</button>
      <button onclick={() => invoke("check_updates")}>Check for Updates…</button>
      <!-- copies stenny.eu, where friends get it; says so for a moment -->
      <button class="share-stenny" class:copied={linkCopied} onclick={shareStenny} title="Copy the link to stenny.eu"
              aria-label={linkCopied ? "Link copied" : "Share Stenny: copy the link to stenny.eu"}>
        <span class="icon" aria-hidden="true">
          <svg class="idle" viewBox="0 0 16 16"><path d="M5.5 6H4.5a1 1 0 0 0-1 1v6.5a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1h-1M8 1.5v8.5M5.5 4 8 1.5 10.5 4" /></svg>
          <svg class="done" viewBox="0 0 16 16"><path d="M3.5 8.5 6.5 11.5 12.5 4.5" /></svg>
        </span>
        <span class="labels">
          <span class="idle">Share Stenny</span>
          <span class="done">Link copied</span>
        </span>
      </button>
    </footer>
  </aside>

  <section class="list">
    <div class="titlebar" data-tauri-drag-region></div>
    {#if group}
      <div class="kicker list-head">
        {entries.length} {entries.length === 1 ? "recording" : "recordings"}
      </div>
      {#each entries as e (id(e))}
        <button class="row" class:active={id(e) === selectedId} onclick={() => (selectedId = id(e))}
                oncontextmenu={(ev) => { selectedId = id(e); showMenu(ev, { entry: e }); }}>
          <span class="row-title serif">{rowTitle(e)}</span>
          {#if isDraft(e)}<span class="status {e.status}">{statusLabel[e.status] ?? "Draft"}</span>{/if}
          <span class="row-detail">{rowDetail(e)}</span>
        </button>
      {:else}
        <p class="empty">No recordings yet.</p>
      {/each}
    {/if}
  </section>

  <main class="detail">
    <div class="titlebar" data-tauri-drag-region></div>
    {#if selected}
      <article>
        <div class="kicker">{selected.kind === "event" ? "Talk" : selected.course}</div>
        <h1 class="serif selectable">{selected.kind === "event" ? selected.course : rowTitle(selected)}</h1>
        <p class="meta">{details(selected)}</p>

        {#if isDraft(selected)}
          <div class="draft">
            <p class="serif">
              <span class="status {selected.status}">{statusLabel[selected.status] ?? "Draft"}</span>
              {selected.draft_note ?? "Its transcript comes later."}
            </p>
            {#if selected.status === "queued"}
              <button class="pill small" onclick={finish} disabled={busy === "finish"}>
                {busy === "finish" ? "Starting…" : "Make final transcript now"}
              </button>
            {/if}
            {#if !isBusy(selected)}
              <button class="pill small" aria-label="More" title="Rename or move to the Trash"
                      onclick={(ev) => showMenu(ev, { entry: selected! })}>⋯</button>
            {/if}
          </div>
        {/if}

        {#if !isDraft(selected)}
        <div class="actions">
          <div class="share-wrap">
            <button class="pill primary" disabled={busy === "share"}
                    onclick={(ev) => { ev.stopPropagation(); shareOpen = !shareOpen; }}>
              {busy === "share" ? "Preparing…" : "Share…"}
            </button>
            {#if shareOpen}
              <div class="menu" role="menu">
                {#each FORMATS as f}
                  <button role="menuitem" onclick={() => share(f.id)}>
                    <span>{f.title}</span><small>{f.help}</small>
                  </button>
                {/each}
              </div>
            {/if}
          </div>
          <button class="pill" onclick={() => openTranscript(selected!.transcript)}>Open transcript</button>
          <button class="pill" onclick={() => reveal(selected!.transcript)}>Show in Finder</button>
          <button class="pill" aria-label="More" title="Rename or move to the Trash"
                  onclick={(ev) => showMenu(ev, { entry: selected! })}>⋯</button>
        </div>
        {/if}
        {#if selected.summary && !isDraft(selected)}
          <label class="check">
            <input type="checkbox" bind:checked={withSummary} /> Put the summary at the top of shared copies
          </label>
        {/if}
        {#if notice}
          <p class="notice" class:error={notice.error}>
            {notice.text}{#if notice.path}. <button class="link" onclick={() => reveal(notice!.path!)}>Show in Finder</button>{/if}
          </p>
        {/if}

        <hr />

        {#if summary === undefined}
          <p class="quiet serif">Loading…</p>
        {:else if isDraft(selected)}
          {#if progress}
            <div class="kicker">Transcribed so far</div>
            <div class="summary serif selectable">{@html renderMarkdown(progress)}</div>
          {:else}
            <p class="quiet serif">Nothing transcribed yet. {selected.status === "live"
              ? "While the Mac is plugged in, the text appears here as the recording goes on."
              : "The text appears here once the transcript is being made."}</p>
          {/if}
        {:else if summary}
          <div class="kicker">Summary</div>
          <div class="summary serif selectable">{@html renderMarkdown(summary)}</div>
        {:else}
          <p class="quiet serif">No summary for this {selected.kind === "event" ? "talk" : "lecture"}.</p>
        {/if}
      </article>
    {:else if failed}
      <div class="center">
        <p class="serif big">Couldn't read your lectures</p>
        <p class="quiet selectable">{failed}</p>
        <button class="pill" onclick={reload}>Try again</button>
      </div>
    {:else if data && group}
      <div class="center">
        <p class="serif big">Nothing recorded for {group.course} yet</p>
        <p class="quiet">Start a recording from Stenny in the menu bar; its transcript appears here.</p>
      </div>
    {:else if !data}
      <div class="center"><p class="quiet serif">Loading…</p></div>
    {/if}
  </main>
</div>

{#if menu}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="menu floating" role="menu" style="left: {menu.x}px; top: {menu.y}px" onclick={(ev) => ev.stopPropagation()}>
    {#if menu.group}
      {@const g = menu.group}
      <button role="menuitem" onclick={() => open({ kind: "rename-group", group: g })}>
        <span>Rename…</span>
      </button>
      {#if configured(g)}
        <button role="menuitem" onclick={() => open({ kind: "remove-course", group: g })}>
          <span>Remove Course…</span><small>Its recordings stay, under Past courses</small>
        </button>
      {:else if !g.event}
        <button role="menuitem" onclick={() => bring(g)}>
          <span>Bring Back to the Menu</span>
        </button>
      {/if}
      <div class="menu-line"></div>
      <button role="menuitem" class="danger" onclick={() => open({ kind: "delete-group", group: g })}>
        <span>{g.event ? "Delete Talk…" : "Delete Course…"}</span>
      </button>
    {:else if menu.entry}
      {@const e = menu.entry}
      {#if isBusy(e)}
        <p class="menu-note">{e.status === "live"
          ? "Still recording. To throw it away, use Cancel Recording… in Stenny's menu bar."
          : "Its transcript is being made. Rename or delete it when that's done."}</p>
      {:else}
        <button role="menuitem" onclick={() => open({ kind: "rename-lecture", entry: e })}>
          <span>Rename…</span>
        </button>
        <button role="menuitem" class="danger" onclick={() => open({ kind: "delete-lecture", entry: e })}>
          <span>Move to Trash…</span>
        </button>
      {/if}
    {/if}
  </div>
{/if}

{#if ask}
  {#if ask.kind === "add"}
    <Dialog title="Add a course" confirm="Add Course" busy={askBusy} error={askError}
            text="Mixed is for lecturers who switch languages within a lecture. Auto is for one language per lecture, but not always the same one."
            oncancel={() => (ask = null)} onconfirm={answer}>
      <label for="course-name">Name</label>
      <input id="course-name" bind:value={field} placeholder="e.g. Microeconomics" autocomplete="off" spellcheck="false" />
      <label for="course-language">Language</label>
      <select id="course-language" bind:value={language}>
        {#each LANGUAGES as [code, name]}<option value={code}>{name}</option>{/each}
      </select>
      <label for="course-alias">Short name (optional)</label>
      <input id="course-alias" bind:value={alias} placeholder="e.g. micro" autocomplete="off" spellcheck="false" />
    </Dialog>
  {:else if ask.kind === "rename-group"}
    <Dialog title={ask.group.event ? "Rename this talk" : "Rename this course"} confirm="Rename" busy={askBusy} error={askError}
            text={ask.group.event
              ? "Its folder and files are renamed too."
              : "Its folder, files and Obsidian links are renamed too. The old name still finds it in your calendar."}
            oncancel={() => (ask = null)} onconfirm={answer}>
      <input bind:value={field} aria-label="New name" autocomplete="off" spellcheck="false" />
    </Dialog>
  {:else if ask.kind === "remove-course"}
    {@const n = count(ask.group)}
    <Dialog title="Remove {ask.group.course}?" confirm="Remove Course" busy={askBusy} error={askError}
            text="It leaves the menu and calendar matching. {n === 0
              ? 'Nothing was recorded for it yet. Its glossary, if it has one, stays.'
              : n === 1 ? 'Its transcript and glossary stay, under Past courses in this window.'
              : `Its ${n} transcripts and glossary stay, under Past courses in this window.`} You can bring it back from there."
            oncancel={() => (ask = null)} onconfirm={answer} />
  {:else if ask.kind === "delete-group"}
    {@const n = count(ask.group)}
    {@const what = ask.group.event ? "talk" : "course"}
    <Dialog title="Delete {ask.group.course}?" confirm="Delete {ask.group.event ? 'Talk' : 'Course'}" danger
            busy={askBusy} error={askError}
            text="This deletes the {what} and also {n === 0 ? 'everything Stenny saved for it' : n === 1
              ? 'its recording' : `all ${n} of its ${ask.group.event ? 'recordings' : 'lectures'}`}: transcripts, summaries, audio and everything else in Stenny's folders{ask.group.event ? '' : ', its glossary included'}. {ask.group.event ? '' : 'It also leaves the menu. '}Everything goes to the Trash; once the Trash is emptied, it's gone for good."
            oncancel={() => (ask = null)} onconfirm={answer} />
  {:else if ask.kind === "rename-lecture"}
    <Dialog title="Rename {ask.entry.number > 0 ? `Lecture ${ask.entry.number}` : 'this recording'}" confirm="Rename"
            busy={askBusy} error={askError}
            text={ask.entry.number > 0
              ? `The title shows instead of “Lecture ${ask.entry.number}”. Leave it empty to show the number again.`
              : "The title shows instead of the date. Leave it empty to show the date again."}
            oncancel={() => (ask = null)} onconfirm={answer}>
      <input bind:value={field} aria-label="Title" placeholder="e.g. Decision trees" autocomplete="off" />
    </Dialog>
  {:else if ask.kind === "delete-lecture"}
    <Dialog title="Move {lectureName(ask.entry)} to the Trash?" confirm="Move to Trash" danger busy={askBusy} error={askError}
            text="Its transcript, summary and audio go to the Trash, where you can put them back.{ask.entry.kind === 'lecture'
              ? ' Later lectures of the course move up a number.' : ''}"
            oncancel={() => (ask = null)} onconfirm={answer} />
  {/if}
{/if}

<style>
  .window { display: grid; grid-template-columns: 220px 290px minmax(0, 1fr); height: 100vh; }
  .titlebar { height: var(--titlebar); flex: none; }
  /* the scrolling columns keep a solid band under the window's title bar, like the sidebar's */
  .list > .titlebar, .detail > .titlebar { position: sticky; top: 0; z-index: 5; background: var(--bg); }
  .list > .titlebar { margin: 0 -10px; }

  /* Sidebar */
  .sidebar { background: var(--bar); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
  .brand { display: flex; align-items: center; gap: 9px; padding: 6px 20px 22px; }
  .brand span { font-size: 22px; font-weight: 460; letter-spacing: -0.01em; }
  nav { overflow-y: auto; padding: 0 10px 20px; flex: 1; }
  footer { padding: 10px 10px 14px; border-top: 1px solid var(--line); display: flex; flex-direction: column; }
  footer button { border: 0; background: none; text-align: left; padding: 4px 10px; color: var(--muted); border-radius: 6px; }
  footer button:hover { color: var(--ink); }
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
    color: var(--muted); width: 22px; height: 22px; border-radius: 5px; padding: 0; line-height: 1; font-size: 14px;
  }
  .more { opacity: 0; }
  .group-row:hover .more, .more:focus-visible { opacity: 1; }
  .more:hover, .add:hover { color: var(--ink); background: color-mix(in srgb, var(--ink) 8%, transparent); }
  /* "Courses" and its +: the + centred on the label's line, its right edge where the rows' ⋯ sits */
  nav .kicker.with-add { display: flex; align-items: center; justify-content: space-between; padding-bottom: 10px; }
  .add { position: static; transform: none; width: 20px; height: 20px; margin: -3px -6px -3px 0; font-size: 16px; font-weight: 500; flex: none; }
  .quiet-add { color: var(--muted); font-size: 14px; }

  /* List */
  .list { border-right: 1px solid var(--line); overflow-y: auto; padding: 0 10px 20px; }
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
  .draft .pill { margin-top: 10px; }

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

  .center { height: calc(100% - var(--titlebar)); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; text-align: center; padding: 0 40px; }
  .big { font-size: 20px; margin: 0; }
  .center p { margin: 0; }
</style>
