<script module lang="ts">
  // For as long as Stenny runs: the library window makes this card anew each time it shows the library again
  // (after Settings, or when the window is opened again from the menu bar).
  let hiddenNow = false; // × until Stenny opens again
  let seen = false;      // shown with something still to do, so finishing it earns "You're all set"
</script>

<script lang="ts">
  // "Finish setting up Stenny": a calm card at the bottom of the library window's sidebar until the four things that
  // make Stenny really useful work: courses, the AI step, the connector for Claude, the calendar (for "Record this
  // lecture?"). A step is done because it works (app_server.setup_status, the same checks as Settings), never
  // because something was clicked; looked at when the window opens and whenever it gets the focus back (the user
  // signed in in a terminal, clicked Install in Claude, or allowed the calendar in System Settings).
  // × hides it until Stenny opens again; after a few closes it also offers "Don't show this again". Once everything
  // is done: "You're all set" for a moment, then `[app] setup_checklist = false`, gone for good.
  import { onMount, untrack } from "svelte";
  import { fade } from "svelte/transition";
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import { openUrl } from "@tauri-apps/plugin-opener";
  import {
    calendarAllow, engine, openSystemSettings, setupStatus,
    type AiStatus, type CalendarStatus, type ConnectorStatus, type SetupStatus,
  } from "$lib/engine";
  import { t, tn } from "$lib/i18n.svelte";

  type Section = "transcripts" | "claude" | "calendar";
  let { courses, onaddcourse, onsettings }: {
    courses: number | null;           // the window's own course list, once loaded: adding one counts at once
    onaddcourse: () => void;
    onsettings: (section: Section) => void;
  } = $props();

  const inApp = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  const CLOSES = "setupClosed";       // how often × was used (this computer only)
  const OFFER_NEVER = 3;              // from then on the card also offers "Don't show this again"
  const ALL_SET_FOR = 2800;
  const URLS = { claudeCode: "https://claude.com/claude-code", console: "https://platform.claude.com/settings/keys",
                 desktop: "https://claude.ai/download" };

  let status = $state<SetupStatus | null>(null);
  let checking = $state(false);
  let checkAgain = false;
  let hidden = $state(hiddenNow);
  let gone = $state(false);
  let allSet = $state(false);
  let open = $state(false);
  let card = $state<HTMLElement | null>(null);
  let panelAt = $state({ left: 0, bottom: 0 });
  let closedBefore = $state(readCloses());

  let signingIn = $state(false);      // the sign-in terminal was opened
  let installOpened = $state(false);  // Claude's Install window was opened
  let busy = $state<"signin" | "key" | "install" | "code" | "calendar" | null>(null);
  let keyInput = $state("");
  let error = $state<string | null>(null);

  function readCloses() {
    try {
      return Number(localStorage.getItem(CLOSES)) || 0;
    } catch {
      return 0;
    }
  }

  async function check() {
    if (gone) return;
    if (checking) { checkAgain = true; return; }
    checking = true;
    try {
      const st = await setupStatus();
      status = st;
      if (st.ai?.state === "ready") signingIn = false;
      if (st.connector?.installed || st.connector?.code.added) installOpened = false;
    } catch {
      // nothing to show: the next check (focus) tries again
    } finally {
      checking = false;
      if (checkAgain) { checkAgain = false; check(); }
    }
  }

  const aiDone = (ai?: AiStatus) => ai?.state === "ready" || ai?.state === "off";
  const claudeDone = (c?: ConnectorStatus) => !!c && (c.installed || c.code.added);
  // Stenny can read a calendar (the Mac's, or one by link), or the reminders are switched off, as the user chose
  const calendarDone = (c?: CalendarStatus) => !!c && (c.connected || !c.reminders);
  let courseCount = $derived(courses ?? status?.courses ?? 0);
  let steps = $derived(
    status?.show && status.ai && status.connector && status.calendar
      ? { courses: courseCount > 0, ai: aiDone(status.ai), claude: claudeDone(status.connector),
          calendar: calendarDone(status.calendar) }
      : null,
  );
  const TOTAL = 4;
  let done = $derived(steps ? [steps.courses, steps.ai, steps.claude, steps.calendar].filter(Boolean).length : 0);
  let complete = $derived(!!steps && done === TOTAL);
  let visible = $derived(!!steps && !gone && !hidden && (!complete || allSet));

  // seen with something to do: finishing it now gets its moment
  $effect(() => {
    if (steps && !complete && !hidden) seen = true;
  });

  // everything works: a moment of "You're all set" (only if the card was there to see), then gone for good
  $effect(() => {
    if (complete && !gone && !allSet) untrack(finish);
  });

  async function finish() {
    if (seen && !hidden) {
      allSet = true;
      open = false;
      await new Promise((r) => setTimeout(r, ALL_SET_FOR));
    }
    gone = true;
    await engine("set_preference", { key: "app.setup_checklist", value: false }).catch(() => {});
  }

  function hide(ev: MouseEvent) {
    ev.stopPropagation();
    hidden = hiddenNow = true;
    open = false;
    try {
      localStorage.setItem(CLOSES, String(closedBefore + 1));
    } catch {
      // only a convenience
    }
  }

  async function neverAgain(ev: MouseEvent) {
    ev.stopPropagation();
    gone = true;
    open = false;
    try {
      await engine("set_preference", { key: "app.setup_checklist", value: false });
    } catch (e) {
      gone = false;
      error = String(e);
    }
  }

  function toggle(ev: MouseEvent) {
    ev.stopPropagation();
    if (open) { open = false; return; }
    const r = card!.getBoundingClientRect();
    panelAt = { left: r.right + 12, bottom: Math.max(12, window.innerHeight - r.bottom) };
    error = null;
    open = true;
    check();
  }

  async function act(what: NonNullable<typeof busy>, run: () => Promise<void>) {
    busy = what;
    error = null;
    try {
      await run();
    } catch (e) {
      error = String(e);
    } finally {
      busy = null;
    }
  }

  const signIn = () => act("signin", async () => { await engine("claude_sign_in"); signingIn = true; });
  const saveKey = () => act("key", async () => {
    const ai = await engine<AiStatus>("set_api_key", { key: keyInput });
    keyInput = "";
    if (status) status = { ...status, ai };
  });
  const install = () => act("install", async () => { await engine("install_connector"); installOpened = true; });
  const addToCode = () => act("code", async () => {
    const connector = await engine<ConnectorStatus>("add_to_claude_code");
    if (status) status = { ...status, connector };
  });

  // macOS asks for the calendar in its own dialog; the answer shows at the next look (the window's focus comes back)
  const allowCalendar = () => act("calendar", async () => { await calendarAllow("calendar"); await check(); });

  function link(url: string) {
    if (inApp) openUrl(url).catch((e) => (error = String(e)));
    else window.open(url, "_blank", "noopener");
  }

  function settings(section: Section) {
    open = false;
    onsettings(section);
  }

  onMount(() => {
    check();
    let unlisten: (() => void) | undefined;
    let unmounted = false;
    const onFocus = () => check();
    if (inApp) {
      getCurrentWindow().onFocusChanged(({ payload: focused }) => { if (focused) check(); })
        .then((u) => (unmounted ? u() : (unlisten = u))).catch(() => {});
    } else {
      window.addEventListener("focus", onFocus);
    }
    return () => {
      unmounted = true;
      unlisten?.();
      window.removeEventListener("focus", onFocus);
    };
  });
</script>

<svelte:window onclick={() => (open = false)}
               onkeydown={(e) => { if (e.key === "Escape" && open) { e.stopPropagation(); open = false; } }} />

{#snippet mark(isDone: boolean)}
  <span class="mark" class:done={isDone} aria-hidden="true">
    {#if isDone}<svg viewBox="0 0 16 16"><path d="M4 8.4 6.8 11 12 5.2" /></svg>{/if}
  </span>
{/snippet}

{#if visible}
  <div class="setup" class:all-set={allSet} out:fade={{ duration: 260 }}>
    <button class="card" bind:this={card} onclick={toggle} aria-expanded={open} disabled={allSet}
            title={allSet ? undefined : t("setup.open")}>
      {#if allSet}
        <span class="title serif">{@render mark(true)}{t("setup.allSet")}</span>
        <span class="line">{t("setup.allSet.text")}</span>
      {:else}
        <span class="title serif">{t("setup.title")}</span>
        <span class="line">{t("setup.progress", { done, total: TOTAL })}</span>
        <span class="bar" aria-hidden="true"><span style="width: {(done / TOTAL) * 100}%"></span></span>
      {/if}
    </button>
    {#if !allSet}
      <button class="x" aria-label={t("setup.hide")} title={t("setup.hide")} onclick={hide}>
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7" /></svg>
      </button>
      {#if closedBefore >= OFFER_NEVER}
        <button class="never" onclick={neverAgain}>{t("setup.never")}</button>
      {/if}
    {/if}
  </div>
{/if}

{#if open && visible && steps && status?.ai && status.connector && status.calendar && !allSet}
  {@const ai = status.ai}
  {@const c = status.connector}
  {@const cal = status.calendar}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="panel" role="dialog" aria-label={t("setup.title")} tabindex="-1"
       style="left: {panelAt.left}px; bottom: {panelAt.bottom}px" onclick={(ev) => ev.stopPropagation()}>
    <header>
      <h2 class="serif">{t("setup.title")}</h2>
      <span class="line">{t("setup.progress", { done, total: TOTAL })}</span>
    </header>
    {#if error}<p class="error selectable">{error}</p>{/if}
    <ol>
      <li class:done={steps.courses}>
        {@render mark(steps.courses)}
        <div class="body">
          <div class="name serif">{t("setup.courses.name")}</div>
          <div class="why">{t("setup.courses.why")}</div>
          {#if steps.courses}
            <div class="state">{tn("setup.courses.done", courseCount)}</div>
          {:else}
            <div class="do">
              <button class="pill primary" onclick={() => { open = false; onaddcourse(); }}>{t("setup.courses.action")}</button>
            </div>
          {/if}
        </div>
      </li>

      <li class:done={steps.ai}>
        {@render mark(steps.ai)}
        <div class="body">
          <div class="name serif">{t("setup.ai.name")}</div>
          <div class="why">{t("setup.ai.why")}</div>
          {#if steps.ai}
            <div class="state">
              {#if ai.state === "off"}{t("setup.ai.offChosen")}
              {:else if ai.backend === "claude-code"}{ai.account ? t("settings.ai.cc.ready", { account: ai.account }) : t("settings.ai.cc.readyPlain")}
              {:else}{t("setup.ai.api.done")}{/if}
            </div>
          {:else if ai.backend === "claude-code"}
            <p class="note">
              {#if ai.state === "missing"}{t("setup.ai.cc.missing")}
              {:else if ai.state === "signed_out"}{signingIn ? t("settings.ai.cc.signingIn") : t("setup.ai.cc.signedOut")}
              {:else}{t("settings.ai.cc.unknown")}{#if ai.detail}<span class="selectable"> {ai.detail}</span>{/if}{/if}
            </p>
            <div class="do">
              {#if ai.state === "missing"}
                <button class="pill primary" onclick={() => link(URLS.claudeCode)}>{t("settings.ai.cc.get")}</button>
              {:else if ai.state === "signed_out"}
                <button class="pill primary" disabled={busy === "signin"} onclick={signIn}>{t("settings.ai.cc.signIn")}</button>
              {/if}
              <button class="link" disabled={checking} onclick={check}>{checking ? t("settings.ai.checking") : t("settings.ai.checkAgain")}</button>
            </div>
          {:else}
            {@const env = ai.source === "environment"}
            <p class="note">
              {#if ai.state === "bad_key"}{env ? t("settings.ai.api.badEnv", { hint: ai.hint ?? "" }) : t("settings.ai.api.badKey", { hint: ai.hint ?? "" })}
              {:else if ai.state === "unchecked"}{t("settings.ai.api.unchecked", { hint: ai.hint ?? "", detail: ai.detail ?? "" })}
              {:else}{t("setup.ai.api.paste")}{/if}
            </p>
            {#if !env}
              <form class="key" onsubmit={(e) => { e.preventDefault(); saveKey(); }}>
                <input type="password" placeholder="sk-ant-…" autocomplete="off" spellcheck="false" bind:value={keyInput}
                       aria-label="Anthropic API key" disabled={busy === "key"} />
                <button class="pill primary" type="submit" disabled={busy === "key" || !keyInput.trim()}>
                  {busy === "key" ? t("settings.ai.checking") : t("settings.ai.api.save")}
                </button>
              </form>
            {/if}
            <div class="do">
              <button class="link" onclick={() => link(URLS.console)}>{t("settings.ai.api.create")}</button>
            </div>
          {/if}
          {#if !steps.ai || ai.state === "off"}
            <button class="link more" onclick={() => settings("transcripts")}>{t("setup.more")}</button>
          {/if}
        </div>
      </li>

      <li class:done={steps.claude}>
        {@render mark(steps.claude)}
        <div class="body">
          <div class="name serif">{t("setup.claude.name")}</div>
          <div class="why">{t("setup.claude.why")}</div>
          {#if steps.claude}
            <div class="state">{c.installed ? t("setup.claude.done.desktop") : t("setup.claude.done.code")}</div>
            {#if c.installed && !c.enabled}<p class="note">{t("settings.claude.disabled")}</p>{/if}
          {:else}
            {#if !c.desktop}
              <p class="note">{t("setup.claude.noDesktop")}</p>
            {:else if !c.file}
              <p class="note">{t("settings.claude.noFile")}</p>
            {:else if installOpened}
              <p class="note">{t("settings.claude.opened")}</p>
            {/if}
            <div class="do">
              {#if !c.desktop}
                <button class="pill primary" onclick={() => link(URLS.desktop)}>{t("settings.claude.getDesktop")}</button>
              {:else if c.file}
                <button class="pill primary" disabled={busy === "install"} onclick={install}>{t("settings.claude.install")}</button>
              {/if}
              {#if c.code.available}
                <button class="pill" disabled={busy === "code"} onclick={addToCode}>
                  {busy === "code" ? t("common.oneMoment") : t("settings.claude.code.add")}
                </button>
              {/if}
            </div>
            <button class="link more" onclick={() => settings("claude")}>{t("setup.more")}</button>
          {/if}
        </div>
      </li>

      <li class:done={steps.calendar}>
        {@render mark(steps.calendar)}
        <div class="body">
          <div class="name serif">{t("setup.calendar.name")}</div>
          <div class="why">{t("setup.calendar.why")}</div>
          {#if !cal.reminders}
            <div class="state">{t("setup.calendar.offChosen")}</div>
          {:else if steps.calendar}
            <div class="state">{cal.lectures > 0 ? tn("setup.calendar.done", cal.lectures) : t("setup.calendar.done.none")}</div>
            {#if cal.lectures === 0}<p class="note">{t("setup.calendar.noMatch")}</p>{/if}
          {:else}
            {#if cal.access === "denied"}
              <p class="note">{t("setup.calendar.denied")}</p>
            {:else if cal.links.length}
              <p class="note">{t("setup.calendar.broken")}</p>
            {:else if cal.platform !== "mac"}
              <p class="note">{t("setup.calendar.link")}</p>
            {/if}
            <div class="do">
              {#if cal.access === "ask"}
                <button class="pill primary" disabled={busy === "calendar"} onclick={allowCalendar}>{t("settings.calendar.allow")}</button>
              {:else if cal.access === "denied"}
                <button class="pill primary" onclick={() => openSystemSettings("calendar").catch((e) => (error = String(e)))}>{t("settings.calendar.openSystem")}</button>
              {/if}
              <button class="pill" class:primary={cal.access !== "ask" && cal.access !== "denied"}
                      onclick={() => settings("calendar")}>{t("settings.calendar.addLink")}</button>
            </div>
          {/if}
          {#if !steps.calendar || !cal.reminders || cal.lectures === 0}
            <button class="link more" onclick={() => settings("calendar")}>{t("setup.more")}</button>
          {/if}
        </div>
      </li>
    </ol>
  </div>
{/if}

<style>
  /* the card: at the bottom of the sidebar, above its footer */
  .setup { position: relative; margin: 0 10px 10px; }
  .card {
    display: flex; flex-direction: column; gap: 3px; width: 100%; text-align: left; padding: 10px 30px 11px 12px;
    border: 1px solid var(--line); border-radius: 10px; background: var(--card);
  }
  .card:hover:not(:disabled) { border-color: color-mix(in srgb, var(--ink) 25%, var(--line)); }
  .card:disabled { opacity: 1; }
  .title { display: flex; align-items: center; gap: 6px; font-size: 14.5px; line-height: 1.25; font-weight: 460; color: var(--ink); }
  .line { color: var(--muted); font-size: 11.5px; }
  .bar { display: block; height: 3px; margin-top: 5px; border-radius: 2px; background: var(--line); overflow: hidden; }
  .bar span { display: block; height: 100%; border-radius: 2px; background: var(--good); transition: width 0.4s ease; }
  .all-set .card { padding-right: 12px; }
  .x {
    position: absolute; top: 6px; right: 6px; width: 20px; height: 20px; padding: 0; border: 0; border-radius: 5px;
    background: none; color: var(--muted); display: grid; place-items: center;
  }
  .x:hover { color: var(--ink); background: color-mix(in srgb, var(--ink) 8%, transparent); }
  .x svg { width: 12px; height: 12px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; }
  .never {
    display: block; border: 0; background: none; padding: 5px 2px 0; color: var(--muted); font-size: 11.5px;
    text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 2px;
  }
  .never:hover { color: var(--ink); }

  .mark {
    flex: none; width: 18px; height: 18px; border-radius: 50%; border: 1.5px solid var(--line);
    display: inline-grid; place-items: center;
  }
  .mark.done { border-color: var(--good); background: var(--good); }
  .mark svg { width: 12px; height: 12px; fill: none; stroke: var(--card); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .title .mark { width: 16px; height: 16px; }

  /* the steps: a panel next to the card, like a menu */
  .panel {
    position: fixed; z-index: 30; width: 380px; max-height: calc(100vh - 24px); overflow-y: auto; padding: 16px 18px 6px;
    background: var(--card); border: 1px solid var(--line); border-radius: 12px; outline: none;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06), 0 16px 40px rgba(0, 0, 0, 0.14);
  }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
  h2 { margin: 0; font-size: 18px; font-weight: 460; letter-spacing: -0.01em; }
  ol { list-style: none; margin: 0; padding: 0; }
  li { display: flex; gap: 11px; padding: 13px 0; border-top: 1px solid var(--line); }
  li:first-child { border-top: 0; }
  li .mark { margin-top: 2px; }
  .body { flex: 1; min-width: 0; }
  .name { font-size: 15px; line-height: 1.3; font-weight: 460; }
  li.done .name { color: var(--ink-2); }
  .why { color: var(--muted); margin-top: 2px; }
  .state { color: var(--good); margin-top: 5px; }
  .note { margin: 8px 0 0; color: var(--ink-2); }
  .do { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin-top: 9px; }
  .key { display: flex; gap: 6px; margin-top: 9px; }
  .key input {
    flex: 1; min-width: 0; padding: 5px 9px; border: 1px solid var(--line); border-radius: 7px; background: var(--bg);
    color: var(--ink); font: inherit;
  }
  .key input:focus { outline: none; border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .pill { border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 5px 13px; font-weight: 500; }
  .pill:hover:not(:disabled) { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .pill.primary { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .pill:disabled { opacity: 0.55; }
  .link {
    border: 0; background: none; padding: 0; color: var(--muted); text-decoration: underline;
    text-decoration-color: var(--line); text-underline-offset: 2px;
  }
  .link:hover:not(:disabled) { color: var(--ink); }
  .more { display: block; margin-top: 9px; font-size: 12px; }
  .error { margin: 6px 0 4px; color: var(--rec); }
  @media (prefers-reduced-motion: reduce) {
    .bar span { transition: none; }
  }
</style>
