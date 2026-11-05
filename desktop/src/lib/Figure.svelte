<script lang="ts">
  // Stenny's stick figure, the stenny.eu logo (same geometry as app/Sources/Stenny/MenuBarImage.swift).
  let { height = 26, stroke = 9 }: { height?: number; stroke?: number } = $props();
</script>

<svg viewBox="36 2 64 152" style:height="{height}px" style:width="{(height * 64) / 152}px" role="img" aria-label="Stenny">
  <g fill="none" stroke="currentColor" stroke-width={stroke} stroke-linecap="round" stroke-linejoin="round">
    <circle cx="60" cy="22" r="11" />
    <path d="M60 34 L60 84 M60 34 L50 58 L46 80 M60 34 L72 60 L90 62 M60 84 L54 114 L50 144 M60 84 L66 114 L70 144" />
  </g>
</svg>
