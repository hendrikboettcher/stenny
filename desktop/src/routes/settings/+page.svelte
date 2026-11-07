<script lang="ts">
  // "Settings…" in the library window's footer. Nothing to set here yet: the page says so, and where things are now.
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import Figure from "$lib/Figure.svelte";
</script>

<div class="page">
  <Figure height={34} />
  <h1 class="serif">Settings are still being built</h1>
  <p>
    They'll be here soon. Until then, add and rename courses in the sidebar of Stenny's window, and pick the
    microphone in Stenny's menu bar.
  </p>
  <button class="pill" onclick={() => getCurrentWindow().close()}>Close</button>
</div>

<style>
  .page {
    height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px;
    padding: 24px 32px; text-align: center;
  }
  h1 { font-size: 21px; font-weight: 460; margin: 4px 0 0; letter-spacing: -0.01em; }
  p { margin: 0; color: var(--muted); line-height: 1.5; max-width: 340px; }
  .pill { margin-top: 8px; border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 6px 15px; font-weight: 500; }
  .pill:hover { border-color: color-mix(in srgb, var(--ink) 35%, var(--line)); }
</style>
