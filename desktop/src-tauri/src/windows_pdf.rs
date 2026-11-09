//! Share… as PDF on Windows: the engine's HTML copy (`make_copy` with format "html") printed to a PDF next to it by
//! WebView2 itself (`ICoreWebView2_7::PrintToPdf`), in a hidden window of Stenny's own; the HTML is removed. The
//! counterpart of stenny-agent's `--pdf` on a Mac (the text system there), with the same contract: the PDF's path,
//! or why it couldn't be made. WebView2 comes with Windows 10 and 11, so nothing extra is bundled.

use crate::i18n::tr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use webview2_com::Microsoft::Web::WebView2::Win32::{ICoreWebView2Environment6, ICoreWebView2_7};
use webview2_com::PrintToPdfCompletedHandler;
use windows::core::{Interface, HSTRING};

static NEXT: AtomicU32 = AtomicU32::new(1);

/// Where the outcome goes, once: the first of "printed", "failed" or "the page never loaded".
type Done = Arc<Mutex<Option<mpsc::Sender<Result<(), String>>>>>;

fn finish(done: &Done, outcome: Result<(), String>) {
    if let Some(tx) = done.lock().unwrap().take() {
        let _ = tx.send(outcome);
    }
}

/// The paper the system's region uses: Letter in the US, Canada, Mexico and the Philippines, A4 everywhere else.
/// In inches, as WebView2 wants them.
fn paper() -> (f64, f64) {
    let letter = sys_locale::get_locale().is_some_and(|l| {
        let region = l.rsplit(['-', '_']).next().unwrap_or("").to_uppercase();
        matches!(region.as_str(), "US" | "CA" | "MX" | "PH")
    });
    if letter { (8.5, 11.0) } else { (8.27, 11.69) }
}

/// Prints the page the hidden window shows into `pdf`; the outcome goes to `done`.
fn print(window: &WebviewWindow, pdf: PathBuf, done: Done) {
    let on_error = done.clone();
    let started = window.with_webview(move |webview| {
        let when_printed = done.clone();
        let printed = (|| -> windows::core::Result<()> {
            unsafe {
                let core: ICoreWebView2_7 = webview.controller().CoreWebView2()?.cast()?;
                let environment: ICoreWebView2Environment6 = webview.environment().cast()?;
                let settings = environment.CreatePrintSettings()?;
                let (width, height) = paper();
                settings.SetPageWidth(width)?;
                settings.SetPageHeight(height)?;
                // the margins of the Mac's PDF (54 and 62 points)
                settings.SetMarginTop(0.75)?;
                settings.SetMarginBottom(0.75)?;
                settings.SetMarginLeft(0.86)?;
                settings.SetMarginRight(0.86)?;
                settings.SetShouldPrintHeaderAndFooter(false)?; // no file name and date on every page
                settings.SetShouldPrintBackgrounds(true)?;
                let handler = PrintToPdfCompletedHandler::create(Box::new(move |result: windows::core::Result<()>, ok: bool| {
                    finish(&when_printed, match result {
                        Ok(()) if ok => Ok(()),
                        Ok(()) => Err(tr!("error.pdf")),
                        Err(e) => Err(tr!("error.pdfWhy", error = e.message())),
                    });
                    Ok(())
                }));
                core.PrintToPdf(&HSTRING::from(pdf.as_os_str()), &settings, &handler)
            }
        })();
        if let Err(e) = printed {
            finish(&done, Err(tr!("error.pdfWhy", error = e.message())));
        }
    });
    if let Err(e) = started {
        finish(&on_error, Err(tr!("error.pdfWhy", error = e)));
    }
}

/// The HTML copy → "<same name>.pdf" next to it. Off the main thread: it waits for the window it opens.
pub fn make_pdf(app: &AppHandle, html: &str) -> Result<String, String> {
    let page = PathBuf::from(html);
    let result = print_page(app, &page);
    let _ = std::fs::remove_file(&page);
    result
}

fn print_page(app: &AppHandle, page: &Path) -> Result<String, String> {
    let pdf = page.with_extension("pdf");
    let _ = std::fs::remove_file(&pdf);
    let url = Url::from_file_path(page).map_err(|_| tr!("error.pdf"))?;
    let (tx, rx) = mpsc::channel();
    let done: Done = Arc::new(Mutex::new(Some(tx)));
    let on_load = done.clone();
    let target = pdf.clone();
    let label = format!("pdf-{}", NEXT.fetch_add(1, Ordering::Relaxed));
    let window = WebviewWindowBuilder::new(app, &label, WebviewUrl::External(url))
        .visible(false)
        .skip_taskbar(true)
        .focused(false)
        .on_page_load(move |window, payload| {
            if matches!(payload.event(), PageLoadEvent::Finished) {
                print(&window, target.clone(), on_load.clone());
            }
        })
        .build()
        .map_err(|e| tr!("error.pdfWhy", error = e))?;
    let outcome = rx.recv_timeout(Duration::from_secs(60)).unwrap_or_else(|_| Err(tr!("error.pdf")));
    finish(&done, Err(String::new())); // a late answer has no one to go to
    let _ = window.destroy();
    outcome?;
    if pdf.exists() { Ok(pdf.to_string_lossy().into_owned()) } else { Err(tr!("error.pdf")) }
}
