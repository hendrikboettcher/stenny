<script lang="ts">
  // The library window: courses and talks on the left, their recordings in the middle, one recording on the right.
  import { onMount, untrack } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import Figure from "$lib/Figure.svelte";
  import { renderMarkdown } from "$lib/markdown";
  import {
    library, lecture, makeCopy, makePdf, finishNow, openTranscript, reveal, lecturerAgreed,
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
  let busy = $state<string | null>(null);   // "share" | "finish"
  let notice = $state<{ text: string; path?: string; error?: boolean } | null>(null);

  const id = (e: Entry) => `${e.kind}/${e.course}/${e.folder}`;
  const same = (a: Group | null, b: Group | null) => !!a && !!b && a.course === b.course && a.event === b.event;
  const isDraft = (e: Entry) => e.status !== "final";

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
  const rowTitle = (e: Entry) => (e.number > 0 ? `Lecture ${e.number}` : prettyDate(e));
  const rowDetail = (e: Entry) => [e.number > 0 ? prettyDate(e) : e.start, e.duration].filter(Boolean).join(" · ");
  const details = (e: Entry) =>
    [prettyDate(e) + (e.start ? `, ${e.start}` : ""), e.duration, languageName(e.language)].filter(Boolean).join(" · ");
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

<svelte:window onclick={() => (shareOpen = false)} onkeydown={(e) => e.key === "Escape" && (shareOpen = false)} />

<div class="window">
  <aside class="sidebar">
    <div class="titlebar" data-tauri-drag-region></div>
    <div class="brand" data-tauri-drag-region>
      <Figure height={24} />
      <span class="serif">Stenny</span>
    </div>

    <nav>
      {#snippet section(title: string, list: Group[])}
        {#if list.length}
          <div class="kicker">{title}</div>
          {#each list as g (g.course + g.event)}
            <button class="group" class:active={same(g, group)} onclick={() => pick(g)} title={g.course}>
              {g.course}
            </button>
          {/each}
        {/if}
      {/snippet}
      {@render section("Courses", groups.courses)}
      {@render section("Past courses", groups.past)}
      {@render section("Talks & events", groups.talks)}
    </nav>
    <footer>
      <button onclick={() => invoke("show_feedback")}>Send Feedback…</button>
      <button onclick={() => invoke("check_updates")}>Check for Updates…</button>
    </footer>
  </aside>

  <section class="list">
    <div class="titlebar" data-tauri-drag-region></div>
    {#if group}
      <div class="kicker list-head">
        {entries.length} {entries.length === 1 ? "recording" : "recordings"}
      </div>
      {#each entries as e (id(e))}
        <button class="row" class:active={id(e) === selectedId} onclick={() => (selectedId = id(e))}>
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

<style>
  .window { display: grid; grid-template-columns: 220px 290px minmax(0, 1fr); height: 100vh; }
  .titlebar { height: var(--titlebar); flex: none; }

  /* Sidebar */
  .sidebar { background: var(--bar); border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
  .brand { display: flex; align-items: center; gap: 9px; padding: 6px 20px 22px; }
  .brand span { font-size: 22px; font-weight: 460; letter-spacing: -0.01em; }
  nav { overflow-y: auto; padding: 0 10px 20px; flex: 1; }
  footer { padding: 10px 10px 14px; border-top: 1px solid var(--line); display: flex; flex-direction: column; }
  footer button { border: 0; background: none; text-align: left; padding: 4px 10px; color: var(--muted); border-radius: 6px; }
  footer button:hover { color: var(--ink); }
  .kicker { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
  nav .kicker { padding: 16px 10px 6px; }
  nav .kicker:first-of-type { padding-top: 0; }
  .group {
    display: block; width: 100%; text-align: left; border: 0; background: none; padding: 6px 10px; border-radius: 7px;
    font-family: var(--serif); font-size: 15px; line-height: 1.25; color: var(--ink-2); text-wrap: pretty; hyphens: auto; overflow-wrap: break-word;
  }
  .group:hover { color: var(--ink); }
  .group.active { background: var(--mark); color: var(--ink); }

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
