<script lang="ts">
  // "Record a Talk or Event…" from the menu bar: a title and the language, then the recording starts
  // (as `stenny live --event … --lang …`). The language last picked is picked again.
  import { invoke } from "@tauri-apps/api/core";
  import { getCurrentWindow } from "@tauri-apps/api/window";

  const LANGUAGES = [["mixed", "Mixed"], ["en", "English"], ["de", "German"], ["fr", "French"]] as const;
  const remembered = (() => { try { return localStorage.getItem("talkLanguage"); } catch { return null; } })();
  let title = $state("");
  let language = $state<string>(LANGUAGES.some(([c]) => c === remembered) ? remembered! : "mixed");
  let error = $state<string | null>(null);
  let busy = $state(false);

  async function start(e: SubmitEvent) {
    e.preventDefault();
    if (!title.trim()) {
      error = "Give the talk a title, like “Guest talk on climate models”.";
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
  <h1 class="serif">Record a talk or event</h1>
  <p>Not one of your courses: a guest talk, a meeting, a panel. Its transcript goes to Talks &amp; events.</p>
  <!-- svelte-ignore a11y_autofocus -->
  <input bind:value={title} oninput={() => (error = null)} placeholder="Title, e.g. Guest talk on climate models" autofocus />
  <div class="language">
    <span class="label">Language</span>
    <div class="segmented" role="radiogroup" aria-label="Language">
      {#each LANGUAGES as [code, name]}
        <button type="button" role="radio" aria-checked={language === code} class:on={language === code}
                onclick={() => (language = code)}>{name}</button>
      {/each}
    </div>
  </div>
  <p class="hint">
    {language === "mixed" ? "For speakers who switch: English, German and French are told apart as the talk goes."
      : `The whole talk in ${LANGUAGES.find(([c]) => c === language)?.[1]}.`}
  </p>
  {#if error}<p class="error">{error}</p>{/if}
  <div class="buttons">
    <button type="button" class="pill" onclick={() => getCurrentWindow().close()}>Cancel</button>
    <button type="submit" class="pill primary" disabled={busy}>{busy ? "Starting…" : "Start Recording"}</button>
  </div>
</form>

<style>
  form { padding: 22px 24px; display: flex; flex-direction: column; gap: 10px; height: 100vh; }
  h1 { font-size: 21px; font-weight: 460; margin: 0; letter-spacing: -0.01em; }
  p { margin: 0; color: var(--muted); }
  input {
    font: inherit; font-size: 14px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px;
    background: var(--card); color: var(--ink); -webkit-user-select: text; user-select: text;
  }
  input:focus { outline: 2px solid color-mix(in srgb, var(--ink) 40%, transparent); outline-offset: 0; }
  .error { color: var(--rec); }
  .language { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
  .label { color: var(--ink-2); font-weight: 500; }
  .hint { font-size: 12px; margin-top: -4px; }
  .segmented { display: inline-flex; background: var(--bar); border: 1px solid var(--line); border-radius: 9px; padding: 2px; gap: 2px; }
  .segmented button { border: 0; background: none; padding: 4px 12px; border-radius: 7px; color: var(--ink-2); }
  .segmented button:hover { color: var(--ink); }
  .segmented button.on { background: var(--card); color: var(--ink); box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08); font-weight: 500; }
  .buttons { display: flex; justify-content: flex-end; gap: 8px; margin-top: auto; }
  .pill { border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 6px 15px; font-weight: 500; }
  .pill.primary { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .pill:disabled { opacity: 0.55; }
</style>
