//! Allowlist for URLs the desktop may hand to the system opener.
//!
//! Chat (and the composer) render `http://` / `https://` as ordinary anchors.
//! The Tauri webview does not follow `target="_blank"`, so a click looks live
//! and does nothing. One command (`open_external_url`) is the only path out:
//! this module decides whether the string is an http(s) URL, and the shell
//! launches it with the platform opener.
//!
//! javascript:, file:, data:, mailto:, and anything without an http(s) scheme
//! return `None` here. Those stay on whatever opener already owns them — this
//! tip is http(s) only.
//!
//! GTK-free and Tauri-free so `rustc --test` can run it alone.

use std::process::{Command, Stdio};

/// An http(s) URL the desktop opener may launch.
///
/// The raw string is returned unchanged so the opener sees what was clicked,
/// not a reconstructed form. Interior whitespace and control characters are
/// refused so a payload cannot ride a URL into a shell.
pub fn allowed_http_url(raw: &str) -> Option<&str> {
    let raw = raw.trim();
    if raw.is_empty() || raw.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return None;
    }
    let rest = if let Some(rest) = strip_http_prefix(raw) {
        rest
    } else {
        return None;
    };
    // `http:///etc/passwd` is a file-shaped path with an http scheme. A host
    // that is missing, or that starts with a slash, is not a URL we open.
    if rest.is_empty() || rest.starts_with('/') || rest.starts_with('\\') {
        return None;
    }
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    if host.is_empty() {
        return None;
    }
    Some(raw)
}

fn strip_http_prefix(raw: &str) -> Option<&str> {
    if raw.len() >= 8 && raw[..7].eq_ignore_ascii_case("http://") {
        Some(&raw[7..])
    } else if raw.len() >= 9 && raw[..8].eq_ignore_ascii_case("https://") {
        Some(&raw[8..])
    } else {
        None
    }
}

/// Open `raw` with the system default browser. Refuses non-http(s) schemes.
pub fn open_http_url(raw: &str) -> Result<(), String> {
    let url = allowed_http_url(raw).ok_or_else(|| "refused URL scheme".to_string())?;
    spawn_opener(url).map_err(|err| err.to_string())?;
    Ok(())
}

fn spawn_opener(url: &str) -> std::io::Result<()> {
    let mut cmd = opener_command(url)?;
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    Ok(())
}

fn opener_command(url: &str) -> std::io::Result<Command> {
    #[cfg(target_os = "macos")]
    {
        let mut cmd = Command::new("open");
        cmd.arg(url);
        Ok(cmd)
    }
    #[cfg(target_os = "windows")]
    {
        // The empty title is required: `start` treats the first quoted arg as
        // the window title, and a URL there would never launch.
        let mut cmd = Command::new("cmd");
        cmd.args(["/C", "start", "", url]);
        Ok(cmd)
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let mut cmd = Command::new("xdg-open");
        cmd.arg(url);
        Ok(cmd)
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows", unix)))]
    {
        let _ = url;
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "no system opener on this platform",
        ))
    }
}

/// Same-origin loads of the FastAPI UI may stay in the webview. External
/// http(s) must not replace the app. Non-http schemes (Tauri IPC, `tauri:`)
/// are not this tip and are left alone.
pub fn allow_webview_navigation(raw: &str) -> bool {
    let Some((scheme, rest)) = raw.split_once("://") else {
        return !raw.to_ascii_lowercase().starts_with("javascript:");
    };
    let scheme = scheme.to_ascii_lowercase();
    if scheme != "http" && scheme != "https" {
        return scheme != "javascript" && scheme != "file" && scheme != "data";
    }
    if scheme == "https" {
        return false;
    }
    let hostport = rest.split(['/', '?', '#']).next().unwrap_or("");
    hostport == "127.0.0.1:38471" || hostport == "localhost:38471"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn https_and_http_are_allowed() {
        assert_eq!(
            allowed_http_url("https://github.com/org/repo/compare/main...branch"),
            Some("https://github.com/org/repo/compare/main...branch")
        );
        assert_eq!(
            allowed_http_url("http://example.com/docs"),
            Some("http://example.com/docs")
        );
        assert_eq!(
            allowed_http_url("HTTPS://EXAMPLE.COM/X"),
            Some("HTTPS://EXAMPLE.COM/X")
        );
    }

    #[test]
    fn javascript_file_data_and_mailto_are_refused() {
        assert_eq!(allowed_http_url("javascript:alert(1)"), None);
        assert_eq!(allowed_http_url("JAVASCRIPT:alert(1)"), None);
        assert_eq!(allowed_http_url("file:///etc/passwd"), None);
        assert_eq!(allowed_http_url("file://localhost/etc/passwd"), None);
        assert_eq!(allowed_http_url("data:text/html,hi"), None);
        assert_eq!(allowed_http_url("mailto:ops@example.com"), None);
        assert_eq!(allowed_http_url("blob:https://example.com/id"), None);
    }

    #[test]
    fn relative_and_hash_paths_are_not_this_opener() {
        assert_eq!(allowed_http_url("/files"), None);
        assert_eq!(allowed_http_url("#settings"), None);
        assert_eq!(allowed_http_url("settings"), None);
        assert_eq!(allowed_http_url(""), None);
        assert_eq!(allowed_http_url("http:///etc/passwd"), None);
        assert_eq!(allowed_http_url("https://"), None);
        assert_eq!(allowed_http_url("http://"), None);
    }

    #[test]
    fn control_characters_are_refused() {
        assert_eq!(allowed_http_url("https://example.com/a\nb"), None);
        assert_eq!(allowed_http_url("https://example.com/a b"), None);
    }

    #[test]
    fn webview_stays_on_the_local_ui() {
        assert!(allow_webview_navigation("http://127.0.0.1:38471/"));
        assert!(allow_webview_navigation("http://127.0.0.1:38471/chat"));
        assert!(allow_webview_navigation("http://localhost:38471/#x"));
        assert!(allow_webview_navigation("ipc://localhost"));
        assert!(allow_webview_navigation("tauri://localhost"));
        assert!(!allow_webview_navigation(
            "https://github.com/org/repo/compare/main...branch"
        ));
        assert!(!allow_webview_navigation("http://example.com/"));
        assert!(!allow_webview_navigation("https://127.0.0.1:38471/"));
        assert!(!allow_webview_navigation("file:///etc/passwd"));
        assert!(!allow_webview_navigation("javascript:alert(1)"));
    }
}
