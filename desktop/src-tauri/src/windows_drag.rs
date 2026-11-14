//! Stenny.mcpb dragged out of Stenny's window on Windows, as from Explorer: into Claude's window, which shows its
//! Install window for it (one of the ways Claude's docs name). Claude can't be handed the file any other way while it
//! runs: a second start of Claude only brings its window up and drops the file (only a cold start reads it), and
//! Windows has no "open this file" message for a running app as macOS has. The shell's own drag (SHDoDragDrop) with the file's own data object, so
//! the drop looks to Claude exactly like one from Explorer.

use windows::core::HSTRING;
use windows::Win32::Foundation::HWND;
use windows::Win32::System::Com::IDataObject;
use windows::Win32::System::Ole::DROPEFFECT_COPY;
use windows::Win32::UI::Shell::{BHID_DataObject, IShellItem, SHCreateItemFromParsingName, SHDoDragDrop};

/// Drags this file until it's dropped (or Escape). On the window's (UI) thread, while the mouse button is still down:
/// the page asks for it on `dragstart`.
pub fn drag_file(hwnd: isize, path: &str) -> Result<(), String> {
    let path = HSTRING::from(path.replace('/', "\\"));
    unsafe {
        let item: IShellItem = SHCreateItemFromParsingName(&path, None).map_err(|e| e.message())?;
        let data: IDataObject = item.BindToHandler(None, &BHID_DataObject).map_err(|e| e.message())?;
        // no drop source of our own: the shell's default one (drag image, Escape cancels)
        SHDoDragDrop(Some(HWND(hwnd as *mut _)), &data, None, DROPEFFECT_COPY).map_err(|e| e.message())?;
    }
    Ok(())
}
