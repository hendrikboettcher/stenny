<script lang="ts">
  // Connect: how Stenny gets into the Claude desktop app, on Mac and Windows, for the setup card and Settings ▸ Claude.
  // Every state says what happens and what to do next. Connect hands Stenny.mcpb to Claude, which shows its own Install
  // window (claude_setup.install_connector); the parent sees Stenny in Claude at its next check and stops showing this.
  // Windows: Claude takes the file only when it starts, so while Claude runs the engine answers "waiting" and this asks
  // the user to quit Claude, then Stenny opens it. Claude from the Microsoft Store (`store`, or the engine's "manual")
  // can't be handed a file at all: there this shows at once how to paste the path into Claude's Install extension
  // dialog, with Copy path. On Windows that way is also offered whenever Claude's Install
  // window doesn't come up. Whatever doesn't end with Stenny in Claude (an error, nothing after a while) shows
  // "Didn't work?" with Try again and Tell us: one click sends a report (report_connector), so we learn about it.
  import { onDestroy } from "svelte";
  import { copyText, engine } from "$lib/engine";
  import { t, onWindows } from "$lib/i18n.svelte";

  let { file, store = false }: { file: string; store?: boolean } = $props();

  type Reply = { opened?: boolean; waiting?: boolean; manual?: boolean };
  const WAIT_MS = 1500;
  const GIVE_UP_MS = 10 * 60_000;  // waiting for Claude to quit
  const NOTHING_MS = 90_000;       // Claude's Install window should have done its work by then

  let phase = $state<"idle" | "waiting" | "opened" | "manual" | "failed">("idle");
  let reason = "";
  let busy = $state(false);
  let copied = $state(false);
  let report = $state<"idle" | "sending" | "sent">("idle");
  let error = $state<string | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let until = 0;

  // the Store app: the paste-the-path steps right away, no Connect button that can only fail
  const shown = $derived(phase === "idle" && store ? "manual" : phase);

  function fail(why: string) {
    clearTimeout(timer);
    reason = why;
    phase = "failed";
  }

  async function ask() {
    try {
      const r = await engine<Reply>("install_connector");
      if (r.waiting) {
        if (Date.now() < until) {
          phase = "waiting";
          timer = setTimeout(ask, WAIT_MS);
        } else {
          fail("Claude didn't quit");
        }
      } else if (r.manual) {
        phase = "manual";
      } else {
        phase = "opened";
        timer = setTimeout(() => fail("timeout"), NOTHING_MS);
      }
    } catch (e) {
      error = String(e);
      fail(String(e));
    }
  }

  async function connect() {
    clearTimeout(timer);
    busy = true;
    report = "idle";
    error = null;
    until = Date.now() + GIVE_UP_MS;
    await ask();
    busy = false;
  }

  function cancel() {
    clearTimeout(timer);
    phase = "idle";
  }

  async function copy() {
    error = null;
    try {
      await copyText(file);
      copied = true;
    } catch (e) {
      error = String(e);
    }
  }

  async function openClaude() {
    error = null;
    try {
      await engine("open_claude");
    } catch (e) {
      error = String(e);
    }
  }

  async function tellUs() {
    report = "sending";
    error = null;
    try {
      await engine("report_connector", { reason: reason || (shown === "manual" ? "store" : "") });
      report = "sent";
    } catch (e) {
      report = "idle";
      error = String(e);
    }
  }

  onDestroy(() => clearTimeout(timer));
</script>

{#snippet tell()}
  {#if report === "sent"}
    <span class="line">{t("claude.connect.thanks")}</span>
  {:else}
    <button class="pill" disabled={report === "sending"} onclick={tellUs}>
      {report === "sending" ? t("common.oneMoment") : t("claude.connect.tell")}
    </button>
  {/if}
{/snippet}

{#snippet pick()}
  <!-- Windows: the way that needs no Install window from Claude -->
  {#if onWindows}
    <p class="pick">
      {t("claude.connect.pick")}
      <button class="link" onclick={copy}>{copied ? t("claude.connect.copied") : t("claude.connect.copy")}</button>
    </p>
  {/if}
{/snippet}

{#if shown === "waiting"}
  <p class="how">{t("claude.connect.quit")}</p>
  <div class="do">
    <span class="line" aria-live="polite">{t("claude.connect.waiting")}</span>
    <button class="pill" onclick={cancel}>{t("common.cancel")}</button>
  </div>
{:else if shown === "manual"}
  <p class="how">{t("claude.connect.store")}</p>
  <div class="do">
    <button class="pill primary" onclick={copy}>{copied ? t("claude.connect.copied") : t("claude.connect.copy")}</button>
    <button class="pill" onclick={openClaude}>{t("settings.claude.openClaude")}</button>
  </div>
  <div class="do">
    <span class="line muted">{t("claude.connect.failed")}</span>
    {@render tell()}
  </div>
{:else if shown === "failed"}
  <div class="do">
    <span class="line">{t("claude.connect.failed")}</span>
    <button class="pill primary" disabled={busy} onclick={connect}>{t("claude.connect.again")}</button>
    {@render tell()}
  </div>
  {@render pick()}
{:else}
  <p class="how" aria-live="polite">{shown === "opened" ? t("claude.connect.opened") : t("claude.connect.how")}</p>
  <div class="do">
    <button class="pill primary" disabled={busy} onclick={connect}>
      {busy ? t("common.oneMoment") : t("claude.connect.button")}
    </button>
  </div>
  {#if shown === "opened"}{@render pick()}{/if}
{/if}
{#if error}<p class="error selectable">{error}</p>{/if}

<style>
  .how { margin: 8px 0 0; color: var(--ink-2); }
  .do { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-top: 10px; }
  .line { color: var(--ink-2); }
  .muted { color: var(--muted); }
  .pick { margin: 10px 0 0; color: var(--muted); font-size: 12.5px; }
  .link {
    border: 0; background: none; padding: 0; color: var(--accent); font-size: inherit; text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent); text-underline-offset: 3px;
  }
  .link:hover { text-decoration-color: currentColor; }
  .error { margin: 8px 0 0; color: var(--danger, #b3261e); font-size: 12.5px; }
</style>
