<script lang="ts">
  // "Send Feedback…" from the menu bar or the library window: goes to stenny.eu, like the website's form.
  import { getCurrentWindow } from "@tauri-apps/api/window";
  import { engine } from "$lib/engine";
  import { t } from "$lib/i18n.svelte";

  let message = $state("");
  let contact = $state("");
  let details = $state(false);
  let error = $state<string | null>(null);
  let sent = $state(false);
  let busy = $state(false);

  async function send(e: SubmitEvent) {
    e.preventDefault();
    if (!message.trim()) {
      error = t("feedback.empty");
      return;
    }
    busy = true;
    error = null;
    try {
      await engine("send_feedback", { message, contact, details });
      sent = true;
    } catch (err) {
      // the message mustn't be lost: it goes to the clipboard
      await navigator.clipboard.writeText(message).catch(() => {});
      error = t("feedback.onClipboard", { error: String(err) });
    } finally {
      busy = false;
    }
  }
</script>

{#if sent}
  <div class="done">
    <h1 class="serif">{t("feedback.thanks")}</h1>
    <p>{t("feedback.sent")}</p>
    <button class="pill primary" onclick={() => getCurrentWindow().close()}>{t("common.close")}</button>
  </div>
{:else}
  <form onsubmit={send}>
    <h1 class="serif">{t("feedback.title")}</h1>
    <p>{t("feedback.lead")}</p>
    <!-- svelte-ignore a11y_autofocus -->
    <textarea bind:value={message} oninput={() => (error = null)} rows="7" autofocus></textarea>
    <input bind:value={contact} placeholder={t("feedback.contact")} />
    <label><input type="checkbox" bind:checked={details} /> {t("feedback.details")}</label>
    {#if error}<p class="error">{error}</p>{/if}
    <div class="buttons">
      <button type="button" class="pill" onclick={() => getCurrentWindow().close()}>{t("common.cancel")}</button>
      <button type="submit" class="pill primary" disabled={busy}>{busy ? t("feedback.sending") : t("feedback.send")}</button>
    </div>
  </form>
{/if}

<style>
  form, .done { padding: 22px 24px; display: flex; flex-direction: column; gap: 10px; height: 100vh; }
  .done { align-items: center; justify-content: center; text-align: center; }
  h1 { font-size: 21px; font-weight: 460; margin: 0; letter-spacing: -0.01em; }
  p { margin: 0; color: var(--muted); }
  textarea, input:not([type]) {
    font: inherit; font-size: 13.5px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px;
    background: var(--card); color: var(--ink); -webkit-user-select: text; user-select: text; resize: none;
  }
  textarea:focus, input:not([type]):focus { outline: 2px solid color-mix(in srgb, var(--ink) 40%, transparent); outline-offset: 0; }
  label { display: flex; gap: 7px; align-items: flex-start; color: var(--muted); }
  label input { accent-color: var(--ink); margin: 2px 0 0; }
  .error { color: var(--rec); }
  .buttons { display: flex; justify-content: flex-end; gap: 8px; margin-top: auto; }
  .pill { border: 1px solid var(--line); background: var(--card); border-radius: 999px; padding: 6px 15px; font-weight: 500; }
  .pill.primary { background: var(--ink); color: var(--bg); border-color: var(--ink); }
  .pill:disabled { opacity: 0.55; }
</style>
