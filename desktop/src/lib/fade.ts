// The window fades in once it has something to show, instead of popping up in pieces (src/app.html has the CSS and
// shows it after 600 ms at the latest). The layout shows a page as soon as its language is known; a page that loads
// more first (the library) holds it and shows it itself.
let held = false;

/** For a page that shows itself (`showPage`) once its first content is there; call while it's set up. */
export function holdPage() {
  held = true;
}

export function pageHeld() {
  return held;
}

export function showPage() {
  if (typeof document !== "undefined") document.documentElement.classList.add("shown");
}
