<script lang="ts">
  // A small sheet over the window: a question, optional fields, two buttons. Escape or a click outside cancels.
  // `danger`: the main button deletes something; Cancel is then the one Return presses.
  import type { Snippet } from "svelte";
  import { t } from "$lib/i18n.svelte";

  let {
    title, text = "", confirm, danger = false, busy = false, error = null, oncancel, onconfirm, children,
  }: {
    title: string; text?: string; confirm: string; danger?: boolean; busy?: boolean; error?: string | null;
    oncancel: () => void; onconfirm: () => void; children?: Snippet;
  } = $props();

  let cancelButton = $state<HTMLButtonElement>();
  let form = $state<HTMLFormElement>();

  $effect(() => {
    // focus: the first field, else (for a deletion) Cancel, else the main button
    const field = form?.querySelector<HTMLInputElement | HTMLSelectElement>("input, select");
    if (field) { field.focus(); if (field instanceof HTMLInputElement) field.select(); }
    else if (danger) cancelButton?.focus();
    else form?.querySelector<HTMLButtonElement>("button.main")?.focus();
  });

  function submit(ev: SubmitEvent) {
    ev.preventDefault();
    if (!busy) onconfirm();
  }
</script>

<svelte:window onkeydown={(e) => { if (e.key === "Escape") { e.stopPropagation(); oncancel(); } }} />

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="backdrop" onclick={(e) => { if (e.target === e.currentTarget) oncancel(); }}>
  <form class="sheet" bind:this={form} onsubmit={submit} role="dialog" aria-modal="true" aria-label={title}>
    <h2>{title}</h2>
    {#if text}<p class="text">{text}</p>{/if}
    {@render children?.()}
    {#if error}<p class="error selectable">{error}</p>{/if}
    <div class="buttons">
      <button type="button" class="pill" bind:this={cancelButton} onclick={oncancel}>{t("common.cancel")}</button>
      <button type="submit" class="pill main" class:danger disabled={busy}>{busy ? t("common.oneMoment") : confirm}</button>
    </div>
  </form>
</div>

<style>
  /* a white card over a dimmed window, like the website's feedback popup */
  .backdrop {
    position: fixed; inset: 0; z-index: 50; display: flex; align-items: flex-start; justify-content: center;
    padding-top: 12vh; background: var(--scrim); animation: dim .2s ease;
  }
  .sheet {
    width: min(420px, calc(100vw - 32px)); background: var(--surface); border-radius: var(--r-card);
    padding: 22px 24px 20px; box-shadow: var(--shadow), inset 0 0 0 1px var(--hairline); animation: rise .25s var(--ease);
  }
  @keyframes dim { from { opacity: 0; } }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
  h2 { font-size: 20px; font-weight: 600; letter-spacing: -.03em; margin: 0 0 6px; }
  .text { margin: 0 0 12px; color: var(--ink-2); line-height: 1.5; }
  .error { margin: 10px 0 0; color: var(--rec); }
  .buttons { display: flex; justify-content: flex-end; gap: 8px; margin-top: 20px; }
  .sheet :global(label) { display: block; font-size: 12.5px; font-weight: 500; color: var(--ink-2); margin: 12px 0 6px; }
  .sheet :global(input), .sheet :global(select) {
    width: 100%; box-sizing: border-box; font: inherit; color: var(--ink); background-color: var(--surface);
    border: 1px solid var(--hairline); border-radius: var(--r-ctl); padding: 0 12px; min-height: 36px;
    transition: border-color .15s ease, box-shadow .15s ease;
  }
  .sheet :global(select) { padding-right: 32px; }
  .sheet :global(input:focus), .sheet :global(select:focus) { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  @media (prefers-reduced-motion: reduce) { .backdrop, .sheet { animation: none; } }
</style>
