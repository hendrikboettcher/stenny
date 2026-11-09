<script module lang="ts">
  export interface Spelling { wrong: string; right: string }
</script>

<script lang="ts">
  // "Always write “Bolzano-Weierstrass” like this in Data Analytics?" [Yes] [Only here]: a calm card under the
  // transcript after the student fixed a misheard name or term in it (#20). The editor (#18) asks the engine which
  // changes look like one (`spelling_suggestions`, ordinary words and rewording ask nothing) and shows this with
  // them, one question at a time. Yes puts `wrong → right` and the right spelling into the course's glossary
  // (`always_spell`), so every lecture from now on gets it right; Only here leaves the glossary as it is.
  import { fade } from "svelte/transition";
  import { engine } from "$lib/engine";
  import { t } from "$lib/i18n.svelte";

  let { course, suggestions, ondone }: {
    course: string;              // the course as the library names it
    suggestions: Spelling[];     // from spelling_suggestions
    ondone: () => void;          // all answered
  } = $props();

  let index = $state(0);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let learned = $state<Spelling | null>(null);
  const DONE_FOR = 2400;
  const current = $derived(suggestions[index]);

  function next() {
    error = null;
    index += 1;
    if (index >= suggestions.length) ondone();
  }

  async function always() {
    busy = true;
    try {
      await engine("always_spell", { course, wrong: current.wrong, right: current.right });
      learned = current;
      setTimeout(() => { learned = null; }, DONE_FOR);
      next();
    } catch (e) {
      error = String(e);
    } finally {
      busy = false;
    }
  }
</script>

{#if current}
  <div class="ask" role="group" aria-label={t("spelling.ask", { right: current.right, course })} transition:fade={{ duration: 120 }}>
    <div class="words">
      <p class="question">{t("spelling.ask", { right: current.right, course })}</p>
      <p class="hint">{t("spelling.ask.text", { wrong: current.wrong })}</p>
      {#if error}<p class="error selectable">{error}</p>{/if}
    </div>
    <div class="buttons">
      <button type="button" class="pill" onclick={next} disabled={busy}>{t("spelling.onlyHere")}</button>
      <button type="button" class="pill main" onclick={always} disabled={busy}>
        {busy ? t("common.oneMoment") : t("spelling.always")}
      </button>
    </div>
  </div>
{:else if learned}
  <p class="done" transition:fade={{ duration: 120 }}>{t("spelling.done", { right: learned.right, course })}</p>
{/if}

<style>
  .ask {
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
  }
  .words { flex: 1 1 240px; min-width: 0; }
  .question { margin: 0; font-weight: 500; }
  .hint { margin: 2px 0 0; color: var(--muted); font-size: 12.5px; }
  .error { margin: 6px 0 0; color: var(--rec); font-size: 12.5px; }
  .buttons { display: flex; gap: 8px; }
  .pill { border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 5px 14px; font-weight: 500; }
  .pill:hover:not(:disabled) { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .pill.main { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .pill:disabled { opacity: 0.55; }
  .done { margin: 0; color: var(--muted); font-size: 12.5px; }
</style>
