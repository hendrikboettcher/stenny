<script lang="ts">
  // Windows: how Stenny gets into the Claude desktop app. Claude reads a Stenny.mcpb it's handed only when it starts
  // (a second start just brings its window up and drops the file), so Install starts Claude with the file, and if
  // Claude is running, asks the student to quit it and waits: the engine answers "waiting" until Claude is gone, then
  // starts it (claude_setup.install_connector). Claude then shows its own Install window. The Store app can't be
  // started with a file: there, and as the fallback, the student pastes the path into Claude's Install extension
  // dialog. Shared by the setup card and Settings ▸ Claude. A Mac opens the file with Claude instead.
  import { onDestroy } from "svelte";
  import { copyText, engine } from "$lib/engine";
  import { t } from "$lib/i18n.svelte";

  let { file, onerror }: { file: string; onerror: (message: string) => void } = $props();

  type Reply = { opened?: boolean; waiting?: boolean; manual?: boolean };
  const WAIT_MS = 1500;
  const GIVE_UP_MS = 10 * 60_000;

  let phase = $state<"idle" | "waiting" | "opened" | "manual">("idle");
  let busy = $state(false);
  let copied = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let until = 0;

  async function ask() {
    try {
      const r = await engine<Reply>("install_connector");
      if (r.waiting) {
        phase = Date.now() < until ? "waiting" : "idle";
        if (phase === "waiting") timer = setTimeout(ask, WAIT_MS);
      } else {
        phase = r.manual ? "manual" : "opened";
      }
    } catch (e) {
      phase = "idle";
      onerror(String(e));
    }
  }

  async function install() {
    busy = true;
    until = Date.now() + GIVE_UP_MS;
    await ask();
    busy = false;
  }

  function cancel() {
    clearTimeout(timer);
    phase = "idle";
  }

  onDestroy(() => clearTimeout(timer));

  async function copy() {
    try {
      await copyText(file);
      copied = true;
    } catch (e) {
      onerror(String(e));
    }
  }
</script>

{#if phase === "waiting"}
  <p class="how">{t("settings.claude.win.quit")}</p>
  <div class="do">
    <span class="wait" aria-live="polite">{t("settings.claude.win.waiting")}</span>
    <button class="pill" onclick={cancel}>{t("common.cancel")}</button>
  </div>
{:else if phase === "manual"}
  <p class="how">{t("settings.claude.win.manual")}</p>
  <div class="do">
    <button class="pill primary" onclick={copy}>{copied ? t("settings.claude.win.copied") : t("settings.claude.win.copy")}</button>
  </div>
{:else}
  <p class="how">{phase === "opened" ? t("settings.claude.opened") : t("settings.claude.win.how")}</p>
  <div class="do">
    <button class="pill primary" disabled={busy} onclick={install}>
      {busy ? t("common.oneMoment") : t("settings.claude.install")}
    </button>
  </div>
  <p class="pick">
    {t("settings.claude.win.pick")}
    <button class="link" onclick={copy}>{copied ? t("settings.claude.win.copied") : t("settings.claude.win.copy")}</button>
  </p>
{/if}

<style>
  .how { margin: 8px 0 0; color: var(--ink-2); }
  .do { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-top: 10px; }
  .wait { color: var(--muted); }
  .pick { margin: 10px 0 0; color: var(--muted); font-size: 12.5px; }
  .link {
    border: 0; background: none; padding: 0; color: var(--accent); font-size: inherit; text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent); text-underline-offset: 3px;
  }
  .link:hover { text-decoration-color: currentColor; }
</style>
