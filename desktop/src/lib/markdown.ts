// Summaries are Markdown written by Stenny (headings, lists, bold, timestamps). Timestamps get a quiet style so
// the sentence reads first, as in the shared copies.
import { marked } from "marked";

const TIMESTAMP = /\[(\d+:\d{2}:\d{2})\]/g;

export function renderMarkdown(text: string): string {
  const html = marked.parse(text, { async: false, gfm: true, breaks: false }) as string;
  return html.replace(TIMESTAMP, '<span class="ts">$1</span>');
}
