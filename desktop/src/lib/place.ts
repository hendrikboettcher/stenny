// A popup under a word in running text (the ready card's calendar and language menu) opens at that word, but never
// past the right edge of the card it's in (or the window): in a narrow window it moves left until it fits.
// Use as `use:keepInside` on the popup, which is positioned with `left` inside its wrapper.
export function keepInside(node: HTMLElement) {
  const fit = () => {
    node.style.translate = "";
    const box = node.getBoundingClientRect();
    const edge = (node.closest(".detail") ?? document.documentElement).getBoundingClientRect().right - 12;
    const over = box.right - Math.min(edge, window.innerWidth - 12);
    if (over > 0) node.style.translate = `${-Math.min(over, Math.max(0, box.left - 12))}px 0`;
  };
  fit();
  window.addEventListener("resize", fit);
  return { destroy: () => window.removeEventListener("resize", fit) };
}
