<script lang="ts">
  // Windows: how Stenny gets into the Claude desktop app. Claude can't be handed Stenny.mcpb while it runs (a second
  // start only brings its window up and drops the file), so the student drags Stenny into Claude's window, which
  // shows its Install window for it (one of Claude's documented ways in); or, if dragging doesn't work, pastes the
  // copied path into Claude's own Install extension dialog. Shared by the setup card and Settings ▸ Claude. A Mac
  // opens the file with Claude instead.
  import { copyText, dragFile, engine } from "$lib/engine";
  import { t } from "$lib/i18n.svelte";

  let { file, onerror }: { file: string; onerror: (message: string) => void } = $props();

  let copied = $state(false);
  let opening = $state(false);

  function drag(ev: DragEvent) {
    ev.preventDefault();  // the web view's own drag can't carry a file out: Windows' drag takes over, button still down
    dragFile(file).catch((e) => onerror(String(e)));
  }

  async function openClaude() {
    opening = true;
    try {
      await engine("open_claude");
    } catch (e) {
      onerror(String(e));
    } finally {
      opening = false;
    }
  }

  async function copy() {
    try {
      await copyText(file);
      copied = true;
    } catch (e) {
      onerror(String(e));
    }
  }
</script>

<p class="how">{t("settings.claude.drag.how")}</p>
<div class="do">
  <div class="tile" role="img" draggable="true" ondragstart={drag} title={t("settings.claude.drag.title")}
       aria-label={t("settings.claude.drag.title")}>
    <img src="/extension-icon.png" alt="" draggable="false" />
    <span>Stenny</span>
    <svg class="grip" viewBox="0 0 8 14" aria-hidden="true">
      <circle cx="2" cy="2" r="1.2" /><circle cx="6" cy="2" r="1.2" /><circle cx="2" cy="7" r="1.2" />
      <circle cx="6" cy="7" r="1.2" /><circle cx="2" cy="12" r="1.2" /><circle cx="6" cy="12" r="1.2" />
    </svg>
  </div>
  <button class="pill primary" disabled={opening} onclick={openClaude}>{t("settings.claude.openClaude")}</button>
</div>
<p class="pick">
  {t("settings.claude.drag.pick")}
  <button class="link" onclick={copy}>{copied ? t("settings.claude.drag.copied") : t("settings.claude.drag.copy")}</button>
</p>

<style>
  .how { margin: 8px 0 0; color: var(--ink-2); }
  .do { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-top: 10px; }
  .tile {
    display: inline-flex; align-items: center; gap: 8px; padding: 5px 10px 5px 5px; border-radius: 10px;
    background: var(--surface); box-shadow: var(--shadow-sm), inset 0 0 0 1px var(--hairline);
    cursor: grab; user-select: none; -webkit-user-select: none; font-weight: 600;
  }
  .tile:hover { box-shadow: var(--shadow-sm), inset 0 0 0 1px var(--accent); }
  .tile:active { cursor: grabbing; }
  .tile img { width: 26px; height: 26px; border-radius: 6px; }
  .grip { width: 8px; height: 14px; margin-left: 2px; fill: var(--muted); }
  .pick { margin: 10px 0 0; color: var(--muted); font-size: 12.5px; }
  .link {
    border: 0; background: none; padding: 0; color: var(--accent); font-size: inherit; text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent); text-underline-offset: 3px;
  }
  .link:hover { text-decoration-color: currentColor; }
</style>
