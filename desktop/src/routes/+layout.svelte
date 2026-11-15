<script lang="ts">
  import "../app.css";
  import { onMount, tick } from "svelte";
  import { invoke } from "@tauri-apps/api/core";
  import { listen } from "@tauri-apps/api/event";
  import { engine } from "$lib/engine";
  import { setLanguage } from "$lib/i18n.svelte";
  import { pageHeld, showPage } from "$lib/fade";

  let { children } = $props();
  // the page shows once its language is known, so it never flashes up in the other one
  let ready = $state(false);
  const inApp = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

  // ⌘R (Ctrl+R on Windows) loads this window again, like View ▸ Reload (src-tauri/src/lib.rs, `reload`): only the
  // page; recordings and final passes run in their own processes and go on. Caught here so it's the same in every
  // window, on both systems and in a browser during development.
  const onWindows = typeof navigator !== "undefined" && /Windows/.test(navigator.userAgent);
  function reloadKey(e: KeyboardEvent) {
    if (e.key.toLowerCase() === "r" && (onWindows ? e.ctrlKey : e.metaKey) && !e.altKey && !e.shiftKey) {
      e.preventDefault();
      if (inApp) invoke("reload_window").catch(() => location.reload());
      else location.reload();
    }
  }

  // The recording's red dots (the bar, its row, the sidebar) blink together: a dot that appears waits, lit, for the
  // next beat of the page's own clock and starts there, so all blinks share it and none starts halfway through one
  const inStep = new WeakSet<Animation>(); // set once: its start event comes again when the beat begins
  function blinkInStep(e: AnimationEvent) {
    if (e.animationName !== "blink" || !(e.target instanceof Element)) return;
    const now = Number(document.timeline.currentTime) || 0;
    for (const a of e.target.getAnimations({ subtree: true })) {
      if (!(a instanceof CSSAnimation) || a.animationName !== "blink" || inStep.has(a)) continue;
      inStep.add(a);
      const beat = Number(a.effect?.getTiming().duration) || 1400;
      a.startTime = Math.ceil(now / beat) * beat;
    }
  }

  onMount(() => {
    window.addEventListener("keydown", reloadKey);
    document.addEventListener("animationstart", blinkInStep);
    let unlisten: (() => void) | undefined;
    const known = inApp
      ? invoke<string>("ui_language")
      : engine<{ ui_language: string }>("preferences").then((p) => p.ui_language);
    known.then(setLanguage, () => setLanguage(navigator.language.slice(0, 2))).finally(async () => {
      ready = true;
      // the page is set up now; it fades in at once unless it waits for its content (lib/fade.ts)
      await tick();
      if (!pageHeld()) showPage();
    });
    // changed in Settings (in any window): every window follows at once
    if (inApp) listen<string>("language", (e) => setLanguage(e.payload)).then((u) => (unlisten = u));
    return () => {
      window.removeEventListener("keydown", reloadKey);
      document.removeEventListener("animationstart", blinkInStep);
      unlisten?.();
    };
  });
</script>

{#if ready}{@render children()}{/if}
