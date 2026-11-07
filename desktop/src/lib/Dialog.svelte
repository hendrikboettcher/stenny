<script lang="ts">
  // A small sheet over the window: a question, optional fields, two buttons. Escape or a click outside cancels.
  // `danger`: the main button deletes something; Cancel is then the one Return presses.
  import type { Snippet } from "svelte";

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
    <h2 class="serif">{title}</h2>
    {#if text}<p class="text">{text}</p>{/if}
    {@render children?.()}
    {#if error}<p class="error selectable">{error}</p>{/if}
    <div class="buttons">
      <button type="button" class="pill" bind:this={cancelButton} onclick={oncancel}>Cancel</button>
      <button type="submit" class="pill main" class:danger disabled={busy}>{busy ? "One moment…" : confirm}</button>
    </div>
  </form>
</div>

<style>
  .backdrop {
    position: fixed; inset: 0; z-index: 50; display: flex; align-items: flex-start; justify-content: center;
    padding-top: 12vh; background: color-mix(in srgb, var(--ink) 18%, transparent);
  }
  .sheet {
    width: min(400px, calc(100vw - 32px)); background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 20px 22px 18px; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.06), 0 20px 50px rgba(0, 0, 0, 0.18);
  }
  h2 { font-size: 20px; font-weight: 460; letter-spacing: -0.01em; margin: 0 0 6px; }
  .text { margin: 0 0 12px; color: var(--muted); line-height: 1.45; }
  .error { margin: 10px 0 0; color: var(--rec); }
  .buttons { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
  .pill { border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 6px 15px; font-weight: 500; }
  .pill:hover:not(:disabled) { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
  .pill.main { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .pill.main.danger { background: var(--rec); border-color: var(--rec); color: #fff; }
  .pill:disabled { opacity: 0.55; }
  .sheet :global(label) { display: block; font-size: 12px; color: var(--muted); margin: 10px 0 4px; }
  .sheet :global(input), .sheet :global(select) {
    width: 100%; box-sizing: border-box; font: inherit; color: var(--ink); background: var(--bg);
    border: 1px solid var(--line); border-radius: 8px; padding: 7px 10px;
  }
  .sheet :global(input:focus), .sheet :global(select:focus) { outline: 2px solid color-mix(in srgb, var(--ink) 25%, transparent); outline-offset: 0; }
  .sheet :global(.hint) { font-size: 11.5px; color: var(--muted); margin: 4px 0 0; }
</style>
