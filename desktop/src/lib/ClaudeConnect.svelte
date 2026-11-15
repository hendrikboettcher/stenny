<script lang="ts">
  // Connect: how Stenny gets into the Claude desktop app, the same on Mac and Windows, for the setup card and
  // Settings ▸ Claude. One button hands Stenny.mcpb to Claude, which shows its own Install window
  // (claude_setup.install_connector); the parent sees Stenny in Claude at its next check and stops showing this.
  // Windows: Claude takes the file only when it starts, so while Claude runs the engine answers "waiting" and this asks
  // the user to close Claude, then Stenny opens it. Claude from the Microsoft Store can't be handed a file at all.
  // Whatever doesn't end with Stenny in Claude (the Store app, an error, nothing after a while) shows one line,
  // "Didn't work?", with Try again and Tell us: one click sends a report (report_connector), so we learn about it.
  import { onDestroy } from "svelte";
  import { engine } from "$lib/engine";
  import { t } from "$lib/i18n.svelte";

  type Reply = { opened?: boolean; waiting?: boolean; manual?: boolean };
  const WAIT_MS = 1500;
  const GIVE_UP_MS = 10 * 60_000;  // waiting for Claude to close
  const NOTHING_MS = 90_000;       // Claude's Install window should have done its work by then

  let phase = $state<"idle" | "waiting" | "opened" | "failed">("idle");
  let reason = "";
  let busy = $state(false);
  let report = $state<"idle" | "sending" | "sent">("idle");
  let reportError = $state<string | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let until = 0;

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
          fail("Claude didn't close");
        }
      } else if (r.manual) {
        fail("store");
      } else {
        phase = "opened";
        timer = setTimeout(() => fail("timeout"), NOTHING_MS);
      }
    } catch (e) {
      fail(String(e));
    }
  }

  async function connect() {
    clearTimeout(timer);
    busy = true;
    report = "idle";
    reportError = null;
    until = Date.now() + GIVE_UP_MS;
    await ask();
    busy = false;
  }

  function cancel() {
    clearTimeout(timer);
    phase = "idle";
  }

  async function tellUs() {
    report = "sending";
    reportError = null;
    try {
      await engine("report_connector", { reason });
      report = "sent";
    } catch (e) {
      report = "idle";
      reportError = String(e);
    }
  }

  onDestroy(() => clearTimeout(timer));
</script>

{#if phase === "waiting"}
  <div class="do">
    <span class="line" aria-live="polite">{t("claude.connect.close")}</span>
    <button class="pill" onclick={cancel}>{t("common.cancel")}</button>
  </div>
{:else if phase === "failed"}
  <div class="do">
    <span class="line">{report === "sent" ? t("claude.connect.thanks") : t("claude.connect.failed")}</span>
    <button class="pill primary" disabled={busy} onclick={connect}>{t("claude.connect.again")}</button>
    {#if report !== "sent"}
      <button class="pill" disabled={report === "sending"} onclick={tellUs}>
        {report === "sending" ? t("common.oneMoment") : t("claude.connect.tell")}
      </button>
    {/if}
  </div>
  {#if reportError}<p class="error selectable">{reportError}</p>{/if}
{:else}
  <div class="do">
    <button class="pill primary" disabled={busy} onclick={connect}>
      {busy ? t("common.oneMoment") : t("claude.connect.button")}
    </button>
    {#if phase === "opened"}<span class="line" aria-live="polite">{t("claude.connect.opened")}</span>{/if}
  </div>
{/if}

<style>
  .do { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-top: 10px; }
  .line { color: var(--ink-2); }
  .error { margin: 8px 0 0; color: var(--danger, #b3261e); font-size: 12.5px; }
</style>
