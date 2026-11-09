<script lang="ts">
  // How far a lecture's final pass is (src/stenny/progress.py, polled with the library every 5 s): in the Finishing
  // banner a bar with the percentage and the three steps underneath, in the list's row only a thin bar, in the
  // window's activity bar (#17) a short bar with "46 % · about 9 min left" on one line. Calm on
  // purpose: the bar glides from one reading to the next (no pulsing, no stripes), and the time left shows only
  // once the engine has measured Whisper's speed.
  import type { FinalPass } from "$lib/engine";
  import { t, locale, type Key } from "$lib/i18n.svelte";

  let { pass, compact = false, inline = false }: { pass: FinalPass; compact?: boolean; inline?: boolean } = $props();

  const STEPS = ["transcribing", "tidying", "saving"] as const;
  // never "100 %" while it still works
  let fraction = $derived(Math.min(Math.max(pass.fraction, 0), 0.99));
  let percent = $derived(
    new Intl.NumberFormat(locale(), { style: "percent", maximumFractionDigits: 0 }).format(Math.floor(fraction * 100) / 100),
  );
  let current = $derived(STEPS.indexOf(pass.step));

  // "1:02:15" (or "12:05" for a lecture under an hour), as in the transcript's timestamps
  function clock(s: number, long: boolean) {
    const total = Math.max(0, Math.floor(s));
    const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), sec = total % 60;
    const two = (n: number) => String(n).padStart(2, "0");
    return long ? `${h}:${two(m)}:${two(sec)}` : `${m}:${two(sec)}`;
  }

  // "about 20 min left": rounded to 5 minutes once it's more than a quarter of an hour, so it doesn't twitch
  let left = $derived.by(() => {
    if (pass.step === "saving") return t("lib.pass.almost");
    if (pass.step !== "transcribing" || pass.left_s == null) return null;
    if (pass.left_s < 90) return t("lib.pass.almost");
    let min = Math.round(pass.left_s / 60);
    if (min > 15) min = Math.round(min / 5) * 5;
    const h = Math.floor(min / 60), m = min % 60;
    const time = h === 0 ? t("lib.pass.min", { n: m }) : m === 0 ? t("lib.pass.h", { h }) : t("lib.pass.hours", { h, m });
    return t("lib.pass.left", { time });
  });
</script>

{#if compact}
  <span class="track thin" aria-hidden="true"><span class="fill" style="transform: scaleX({fraction})"></span></span>
{:else if inline}
  <span class="inline">
    <span class="track short" role="progressbar" aria-label={t("lib.pass.aria", { percent })}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(fraction * 100)}>
      <span class="fill" style="transform: scaleX({fraction})"></span>
    </span>
    <span class="percent">{percent}{#if left}{" · "}{left}{/if}</span>
  </span>
{:else}
  <div class="pass">
    <div class="line">
      <span class="track" role="progressbar" aria-label={t("lib.pass.aria", { percent })}
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(fraction * 100)}>
        <span class="fill" style="transform: scaleX({fraction})"></span>
      </span>
      <span class="percent">{percent}</span>
    </div>
    <div class="steps">
      {#each STEPS as step, i}
        <span class="step" class:now={i === current} class:done={i < current}>
          {#if i < current}
            <svg class="mark" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.4 4.9 8.8 9.5 3.6" /></svg>
          {:else}
            <span class="mark dot" aria-hidden="true"></span>
          {/if}
          <span>{t(`lib.pass.${step}` as Key)}{#if step === "transcribing" && i === current && pass.of > 0}<span
            class="where">{" · "}{t("lib.pass.at", { at: clock(pass.at, pass.of >= 3600), of: clock(pass.of, pass.of >= 3600) })}</span>{/if}</span>
        </span>
      {/each}
      {#if left}<span class="left">{left}</span>{/if}
    </div>
  </div>
{/if}

<style>
  .pass { margin-top: 12px; max-width: 520px; font-family: var(--sans); }
  .line { display: flex; align-items: center; gap: 12px; }
  .track {
    position: relative; flex: 1; height: 5px; border-radius: 999px; overflow: hidden;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .inline { display: inline-flex; align-items: center; gap: 8px; white-space: nowrap; }
  .track.short { flex: none; width: 72px; height: 4px; }
  .inline .percent { min-width: 0; text-align: left; }
  .track.thin { display: block; height: 3px; flex: none; grid-column: 1 / -1; margin-top: 6px; }
  .fill {
    position: absolute; inset: 0; border-radius: inherit; background: var(--warn);
    transform-origin: left center; transition: transform 4.8s linear;  /* one reading to the next, about 5 s apart */
  }
  .percent { font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; min-width: 3.2em; text-align: right; }
  .steps {
    display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 16px; margin-top: 9px;
    font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums;
  }
  .step { display: inline-flex; align-items: baseline; gap: 6px; white-space: nowrap; }
  .step.now { color: var(--ink); }
  .mark { flex: none; width: 8px; height: 8px; align-self: center; }
  .dot { border-radius: 50%; border: 1.5px solid currentColor; box-sizing: border-box; opacity: 0.7; }
  .now .dot { background: var(--warn); border-color: var(--warn); opacity: 1;
              box-shadow: 0 0 0 2.5px color-mix(in srgb, var(--warn) 22%, transparent); }
  svg.mark { width: 11px; height: 11px; fill: none; stroke: var(--good); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .where { color: var(--muted); }
  .left { margin-left: auto; white-space: nowrap; }
  @media (prefers-reduced-motion: reduce) { .fill { transition: none; } }
</style>
