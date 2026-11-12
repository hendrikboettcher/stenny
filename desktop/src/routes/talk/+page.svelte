<script lang="ts">
  // "Record a Talk or Event…" from the menu bar: a title and the language, then the recording starts
  // (as `stenny live --event … --lang …`). The language last picked is picked again.
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import { t, languageName } from "$lib/i18n.svelte";

  const LANGUAGES = ["mixed", "en", "de", "fr"];
  const remembered = (() => { try { return localStorage.getItem("talkLanguage"); } catch { return null; } })();
  let title = $state("");
  let language = $state<string>(LANGUAGES.includes(remembered ?? "") ? remembered! : "mixed");
  let error = $state<string | null>(null);
  let busy = $state(false);

  async function start(e: SubmitEvent) {
    e.preventDefault();
    if (!title.trim()) {
      error = t("talk.needsTitle");
      return;
    }
    busy = true;
    error = null;
    try {
      try { localStorage.setItem("talkLanguage", language); } catch {}
      await invoke("start_talk", { title, language });
      await getCurrentWindow().close();
    } catch (err) {
      error = String(err);
    } finally {
      busy = false;
    }
  }
</script>

<form onsubmit={start}>
  <h1>{t("talk.title")}</h1>
  <p>{t("talk.lead")}</p>
  <!-- svelte-ignore a11y_autofocus -->
  <input bind:value={title} oninput={() => (error = null)} placeholder={t("talk.placeholder")} autofocus />
  <div class="language">
    <span class="label">{t("common.language")}</span>
    <div class="segmented" role="radiogroup" aria-label={t("common.language")}>
      {#each LANGUAGES as code}
        <button type="button" role="radio" aria-checked={language === code} class:on={language === code}
                onclick={() => (language = code)}>{languageName(code)}</button>
      {/each}
    </div>
  </div>
  <p class="hint">
    {language === "mixed" ? t("talk.mixed") : t("talk.whole", { language: languageName(language) ?? language })}
  </p>
  {#if error}<p class="error">{error}</p>{/if}
  <div class="buttons">
    <button type="button" class="pill" onclick={() => getCurrentWindow().close()}>{t("common.cancel")}</button>
    <button type="submit" class="pill primary" disabled={busy}>{busy ? t("common.starting") : t("talk.start")}</button>
  </div>
</form>

<style>
  form { padding: 20px 24px; display: flex; flex-direction: column; gap: 10px; height: 100vh; }
  h1 { font-size: 22px; font-weight: 600; margin: 0; letter-spacing: -.03em; }
  p { margin: 0; color: var(--muted); }
  input {
    font: inherit; font-size: 14px; min-height: 36px; padding: 0 12px; border: 1px solid var(--hairline); border-radius: var(--r-ctl);
    background: var(--surface); color: var(--ink); -webkit-user-select: text; user-select: text; margin-top: 2px;
    transition: border-color .15s ease, box-shadow .15s ease;
  }
  input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  input::placeholder { color: var(--muted); }
  .error { color: var(--rec); }
  .language { display: flex; align-items: center; gap: 10px; margin-top: 2px; }
  .label { color: var(--ink-2); font-weight: 500; }
  .hint { font-size: 12.5px; margin-top: -2px; }
  /* segmented tabs, as on the website: a sunken track with a white pill for the chosen one */
  .segmented { display: inline-flex; background: var(--sunken); border-radius: var(--r-ctl); padding: 3px; gap: 2px; }
  .segmented button { border: 0; background: none; padding: 4px 10px; border-radius: 8px; color: var(--ink-2); font-weight: 500; }
  .segmented button:hover { color: var(--ink); }
  .segmented button.on { background: var(--surface); color: var(--ink); box-shadow: 0 1px 3px rgba(11, 12, 14, .12); }
  .buttons { display: flex; justify-content: flex-end; gap: 8px; margin-top: auto; }
</style>
