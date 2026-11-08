//! Share… on Windows: the system's Share panel (Nearby Sharing, Mail, Teams, OneDrive …) for the copy just made,
//! the counterpart of macOS's share menu (macos.rs). Through DataTransferManager, as a desktop app may use it.
//! Everything runs on the window's thread: Windows' objects mustn't move between threads.

use std::sync::Mutex;
use windows::core::{Interface, Ref, HSTRING};
use windows::ApplicationModel::DataTransfer::{DataRequestedEventArgs, DataTransferManager};
use windows::Foundation::TypedEventHandler;
use windows::Storage::{IStorageItem, StorageFile};
use windows::Win32::Foundation::HWND;
use windows::Win32::System::WinRT::RoGetActivationFactory;
use windows::Win32::UI::Shell::IDataTransferManagerInterop;
use windows_collections::IIterable;

/// The handler of the last share (the window's manager and its token), removed before the next one.
static LAST: Mutex<Option<(isize, i64)>> = Mutex::new(None);

fn manager(interop: &IDataTransferManagerInterop, hwnd: HWND) -> Result<DataTransferManager, String> {
    unsafe { interop.GetForWindow(hwnd) }.map_err(|e| e.message())
}

/// Opens the Share panel over the window for these files. On the window's (UI) thread.
pub fn share(hwnd: isize, paths: Vec<String>) -> Result<(), String> {
    let window = HWND(hwnd as *mut _);
    let interop: IDataTransferManagerInterop =
        unsafe { RoGetActivationFactory(&HSTRING::from("Windows.ApplicationModel.DataTransfer.DataTransferManager")) }
            .map_err(|e| e.message())?;
    let manager = manager(&interop, window)?;
    if let Some((_, token)) = LAST.lock().unwrap().take() {
        let _ = manager.RemoveDataRequested(token);
    }
    let title = paths
        .first()
        .and_then(|p| std::path::Path::new(p).file_name())
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    // Windows asks for the files once the panel opens: they're looked up then
    let handler = TypedEventHandler::new(move |_: Ref<DataTransferManager>, args: Ref<DataRequestedEventArgs>| {
        let Some(args) = args.as_ref() else { return Ok(()) };
        let request = args.Request()?;
        let data = request.Data()?;
        data.Properties()?.SetTitle(&HSTRING::from(title.as_str()))?;
        let mut items: Vec<Option<IStorageItem>> = Vec::new();
        for p in &paths {
            let file = StorageFile::GetFileFromPathAsync(&HSTRING::from(p.replace('/', "\\")))?.join()?;
            items.push(Some(file.cast::<IStorageItem>()?));
        }
        data.SetStorageItemsReadOnly(&IIterable::<IStorageItem>::from(items))?;
        Ok(())
    });
    let token = manager.DataRequested(&handler).map_err(|e| e.message())?;
    *LAST.lock().unwrap() = Some((hwnd, token));
    unsafe { interop.ShowShareUIForWindow(window) }.map_err(|e| e.message())
}
