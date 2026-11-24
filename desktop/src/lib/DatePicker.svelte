<script lang="ts">
  // A small calendar under a date in Stenny's window (the ready card's "Held on …", routes/+page.svelte), drawn like
  // the window's menus: white card, soft shadow, the day picked in Stenny's blue. Stenny's own, not the system's
  // <input type="date">: WebKit's picker looked out of place and couldn't be closed by a click elsewhere or Escape,
  // and WebView2's looks different again. Escape or a click outside closes it (the page's own handlers call
  // `onclose`); arrow keys move a day or a week, Page Up/Down a month, Return picks. Days after today can't be picked:
  // a recording is from the past.
  import { onMount, untrack } from "svelte";
  import { t, locale } from "$lib/i18n.svelte";
  import { keepInside } from "$lib/place";

  let { value, onpick, onclose }: { value: string; onpick: (date: string) => void; onclose: () => void } = $props();

  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const parse = (s: string) => {
    const d = new Date(`${s}T12:00:00`);
    return isNaN(+d) ? new Date() : d;
  };
  const today = iso(new Date());

  // the day with the keyboard's focus, and so the month shown
  let focused = $state(parse(untrack(() => value)));
  let month = $derived(new Date(focused.getFullYear(), focused.getMonth(), 1));

  // weeks start on Monday (both locales Stenny speaks); the grid always has six rows, so it never jumps
  let days = $derived.by(() => {
    const start = new Date(month);
    start.setDate(1 - ((month.getDay() + 6) % 7));
    return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
  });
  let weekdays = $derived(
    Array.from({ length: 7 }, (_, i) =>
      new Date(2024, 0, 1 + i).toLocaleDateString(locale(), { weekday: "short" }).replace(".", "").slice(0, 2)),
  );
  let title = $derived(month.toLocaleDateString(locale(), { month: "long", year: "numeric" }));
  const later = (d: Date) => iso(d) > today;
  const thisMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  let lastMonth = $derived(+month >= +thisMonth); // no months after this one

  let grid = $state<HTMLDivElement | null>(null);
  function focusDay() {
    grid?.querySelector<HTMLButtonElement>(`[data-day="${iso(focused)}"]`)?.focus();
  }
  onMount(focusDay);

  function move(byDays = 0, byMonths = 0) {
    const next = new Date(focused.getFullYear(), focused.getMonth() + byMonths, focused.getDate() + byDays);
    if (later(next)) return;
    focused = next;
    queueMicrotask(focusDay);
  }
  function showMonth(by: number) {
    const next = new Date(month.getFullYear(), month.getMonth() + by, 1);
    if (+next > +thisMonth) return;
    // the same day in that month where it exists, never after today
    const day = Math.min(focused.getDate(), new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate());
    const candidate = new Date(next.getFullYear(), next.getMonth(), day);
    focused = later(candidate) ? new Date() : candidate;
  }
  function keydown(e: KeyboardEvent) {
    const keys: Record<string, () => void> = {
      ArrowLeft: () => move(-1), ArrowRight: () => move(1), ArrowUp: () => move(-7), ArrowDown: () => move(7),
      PageUp: () => move(0, -1), PageDown: () => move(0, 1),
    };
    if (e.key in keys) {
      e.preventDefault();
      keys[e.key]();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onclose();
    }
  }
  function pick(d: Date) {
    if (later(d)) return;
    if (iso(d) === value) onclose();
    else onpick(iso(d));
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="picker" use:keepInside role="dialog" tabindex="-1" aria-label={t("ready.changeDate")} onclick={(e) => e.stopPropagation()} onkeydown={keydown}>
  <div class="head">
    <span class="title">{title}</span>
    <button class="nav" aria-label={t("date.previousMonth")} title={t("date.previousMonth")} onclick={() => showMonth(-1)}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3.5 5.5 8 10 12.5" /></svg>
    </button>
    <button class="nav" aria-label={t("date.nextMonth")} title={t("date.nextMonth")} disabled={lastMonth} onclick={() => showMonth(1)}>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" /></svg>
    </button>
  </div>
  <div class="grid" role="grid" bind:this={grid}>
    {#each weekdays as w}<span class="weekday" role="columnheader">{w}</span>{/each}
    {#each days as d (iso(d))}
      {@const day = iso(d)}
      <button class="day" data-day={day} role="gridcell" tabindex={day === iso(focused) ? 0 : -1}
              class:other={d.getMonth() !== month.getMonth()} class:today={day === today} class:picked={day === value}
              disabled={later(d)} aria-selected={day === value}
              aria-label={d.toLocaleDateString(locale(), { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
              onclick={() => pick(d)}>{d.getDate()}</button>
    {/each}
  </div>
</div>

<style>
  /* like the window's menus (routes/+page.svelte, .menu): a white card with a soft shadow and a hairline */
  .picker {
    position: absolute; top: calc(100% + 6px); left: -8px; z-index: 12; width: 252px; padding: 10px 10px 8px;
    background: var(--surface); border-radius: 14px; box-shadow: var(--shadow), inset 0 0 0 1px var(--hairline);
    font-size: 13px; line-height: 1; color: var(--ink); cursor: default;
  }
  .head { display: flex; align-items: center; gap: 2px; padding: 2px 2px 10px 6px; }
  .title { flex: 1; font-weight: 600; font-size: 13.5px; letter-spacing: -.01em; }
  .nav {
    width: 26px; height: 26px; padding: 0; border: 0; border-radius: 8px; background: none; color: var(--ink-2);
    display: grid; place-items: center;
  }
  .nav svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .nav:hover:not(:disabled) { background: var(--sunken); color: var(--ink); }
  .nav:disabled { opacity: .35; }
  .grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
  .weekday { text-align: center; font-size: 11px; font-weight: 500; color: var(--muted); padding: 0 0 6px; }
  .day {
    height: 30px; padding: 0; border: 0; border-radius: 8px; background: none; color: var(--ink);
    font: 500 12.5px var(--sans); font-variant-numeric: tabular-nums; transition: background .12s ease;
  }
  .day:hover:not(:disabled):not(.picked) { background: var(--sunken); }
  .day.other { color: var(--muted); font-weight: 400; }
  .day.today:not(.picked) { color: var(--accent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 35%, transparent); }
  .day.picked { background: var(--accent); color: var(--on-accent); }
  .day:disabled { color: var(--muted); opacity: .4; }
  .day:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  @media (prefers-reduced-motion: reduce) { .day { transition: none; } }
</style>
