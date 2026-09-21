//! Glanceable Needs: taskbar/dock badge + system tray.
//!
//! One command, one tray, one mapping (`needs_map`). The webview sends the
//! length of `store.needs` — the same list the bell reads — and this module
//! paints every OS surface from that number. Clicking the tray focuses the
//! window and asks the shell to open the Needs popover.
//!
//! Native OS toasts stay parked (`needs_map::OS_TOAST_ENABLED`).

use crate::needs_map;
use tauri::image::Image;
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{App, AppHandle, Emitter, Manager, WebviewWindow};

const TRAY_ID: &str = "needs";
const FOCUS_EVENT: &str = "needs-focus";
const WINDOW_LABEL: &str = "main";

/// Left-click (and the Windows double-click that some trays still fire) is
/// the one gesture: focus the app on Needs. No menu, no toast.
fn is_open_gesture(event: &TrayIconEvent) -> bool {
    matches!(
        event,
        TrayIconEvent::Click {
            button: MouseButton::Left,
            button_state: MouseButtonState::Up,
            ..
        } | TrayIconEvent::DoubleClick {
            button: MouseButton::Left,
            ..
        }
    )
}

fn focus_needs(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
    if let Err(err) = app.emit(FOCUS_EVENT, ()) {
        eprintln!("[BossMod] needs-focus emit: {err}");
    }
}

fn owned_image(rgba: Vec<u8>, width: u32, height: u32) -> Image<'static> {
    Image::new_owned(rgba, width, height)
}

fn tray_icon_for(app: &AppHandle, count: u32) -> Option<Image<'static>> {
    let base = app.default_window_icon()?;
    if count == 0 {
        return Some(base.clone());
    }
    let rgba = needs_map::composite_badge(base.rgba(), base.width(), base.height(), count)?;
    Some(owned_image(rgba, base.width(), base.height()))
}

#[cfg(windows)]
fn apply_window_badge(window: &WebviewWindow, count: u32) {
    // Windows has no dock count; ITaskbarList3 overlay is the native badge.
    match needs_map::overlay_rgba(count) {
        Some((width, height, rgba)) => {
            if let Err(err) = window.set_overlay_icon(Some(owned_image(rgba, width, height))) {
                eprintln!("[BossMod] needs overlay: {err}");
            }
        }
        None => {
            if let Err(err) = window.set_overlay_icon(None) {
                eprintln!("[BossMod] needs overlay clear: {err}");
            }
        }
    }
}

#[cfg(not(windows))]
fn apply_window_badge(window: &WebviewWindow, count: u32) {
    // macOS dock badge; Linux Unity/KDE best-effort. Unsupported platforms
    // return an error we swallow so a missing desktop feature cannot take
    // the window down.
    if let Err(err) = window.set_badge_count(needs_map::badge_count(count)) {
        eprintln!("[BossMod] needs badge: {err}");
    }
}

fn apply(app: &AppHandle, count: u32) {
    if needs_map::OS_TOAST_ENABLED {
        // Parked. A later cut that opts in must still feed only tray_tooltip
        // (count copy) — never a need title or body.
    }

    if let Some(window) = app.get_webview_window(WINDOW_LABEL) {
        apply_window_badge(&window, count);
    }

    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    if let Err(err) = tray.set_tooltip(Some(needs_map::tray_tooltip(count))) {
        eprintln!("[BossMod] needs tray tooltip: {err}");
    }
    #[cfg(target_os = "linux")]
    {
        if let Err(err) = tray.set_title(needs_map::tray_title(count)) {
            eprintln!("[BossMod] needs tray title: {err}");
        }
    }
    if let Some(icon) = tray_icon_for(app, count) {
        if let Err(err) = tray.set_icon(Some(icon)) {
            eprintln!("[BossMod] needs tray icon: {err}");
        }
    }
}

/// Called from the webview with `store.needs.length`. Count is the only
/// argument on purpose: a title has nowhere to go.
#[tauri::command]
pub fn sync_needs_attention(app: AppHandle, count: u32) {
    apply(&app, count);
}

/// Install the tray. Failure is best-effort: the window still opens.
pub fn install(app: &App) {
    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip(needs_map::tray_tooltip(0))
        .on_tray_icon_event(|tray, event| {
            if is_open_gesture(&event) {
                focus_needs(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    if let Err(err) = builder.build(app) {
        eprintln!("[BossMod] needs tray unavailable: {err}");
    }
}
