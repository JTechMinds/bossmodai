//! Pure Needs → OS-chrome mapping.
//!
//! The only input is the length of the Needs queue — the same number the bell
//! paints. Titles, commands, paths, and tokens never enter this module, which
//! is what keeps the taskbar overlay and the tray tooltip from becoming a
//! second place secrets leak.

/// Native OS toasts are parked for v1: too noisy beside the in-app toast host.
/// Flip nothing here until a later cut explicitly opts in.
pub const OS_TOAST_ENABLED: bool = false;

/// Windows overlay / tray-badge raster size. ITaskbarList3 overlay icons are
/// small by contract; a full app-icon composite is the wrong asset.
pub const OVERLAY_SIZE: u32 = 16;

const RED: [u8; 4] = [220, 38, 38, 255];
const WHITE: [u8; 4] = [255, 255, 255, 255];

/// 3×5 glyphs, row-major, 1 = ink. Count-only; no letterforms.
const DIGITS: [[u8; 15]; 10] = [
    [1, 1, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 1, 1], // 0
    [0, 1, 0, 1, 1, 0, 0, 1, 0, 0, 1, 0, 1, 1, 1], // 1
    [1, 1, 1, 0, 0, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1], // 2
    [1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 1, 1], // 3
    [1, 0, 1, 1, 0, 1, 1, 1, 1, 0, 0, 1, 0, 0, 1], // 4
    [1, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 1], // 5
    [1, 1, 1, 1, 0, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1], // 6
    [1, 1, 1, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 1, 0], // 7
    [1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1], // 8
    [1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 0, 1, 1, 1, 1], // 9
];

/// Dock / Unity badge count. `None` clears the badge when the queue is empty.
pub fn badge_count(needs_len: u32) -> Option<i64> {
    if needs_len == 0 {
        None
    } else {
        Some(i64::from(needs_len))
    }
}

/// Tray tooltip. Count and product name only — never a need title or body.
pub fn tray_tooltip(needs_len: u32) -> String {
    match needs_len {
        0 => "BossMod AI".to_string(),
        1 => "BossMod AI — 1 thing needs you".to_string(),
        n => format!("BossMod AI — {n} things need you"),
    }
}

/// Linux tray title: the numeral when Needs > 0, cleared otherwise.
pub fn tray_title(needs_len: u32) -> Option<String> {
    if needs_len == 0 {
        None
    } else {
        Some(needs_len.to_string())
    }
}

/// Windows taskbar overlay raster. `None` means "clear the overlay".
pub fn overlay_rgba(needs_len: u32) -> Option<(u32, u32, Vec<u8>)> {
    if needs_len == 0 {
        return None;
    }
    let shown = needs_len.min(99);
    let mut buf = vec![0u8; (OVERLAY_SIZE * OVERLAY_SIZE * 4) as usize];
    fill_circle(&mut buf, OVERLAY_SIZE, RED);
    blit_number(&mut buf, OVERLAY_SIZE, shown, WHITE);
    Some((OVERLAY_SIZE, OVERLAY_SIZE, buf))
}

fn fill_circle(buf: &mut [u8], size: u32, color: [u8; 4]) {
    let cx = size as f32 / 2.0;
    let cy = size as f32 / 2.0;
    let r = size as f32 / 2.0 - 0.5;
    for y in 0..size {
        for x in 0..size {
            let dx = x as f32 + 0.5 - cx;
            let dy = y as f32 + 0.5 - cy;
            if dx * dx + dy * dy <= r * r {
                put(buf, size, size, x, y, color);
            }
        }
    }
}

fn blit_number(buf: &mut [u8], size: u32, n: u32, color: [u8; 4]) {
    let tens = n / 10;
    let ones = n % 10;
    if tens == 0 {
        blit_digit(buf, size, 6, 5, ones as u8, color);
        return;
    }
    blit_digit(buf, size, 4, 5, tens as u8, color);
    blit_digit(buf, size, 8, 5, ones as u8, color);
}

fn blit_digit(buf: &mut [u8], size: u32, origin_x: u32, origin_y: u32, digit: u8, color: [u8; 4]) {
    let glyph = &DIGITS[(digit as usize) % 10];
    for gy in 0..5u32 {
        for gx in 0..3u32 {
            if glyph[(gy * 3 + gx) as usize] == 0 {
                continue;
            }
            put(buf, size, size, origin_x + gx, origin_y + gy, color);
        }
    }
}

fn put(buf: &mut [u8], width: u32, height: u32, x: u32, y: u32, color: [u8; 4]) {
    if x >= width || y >= height {
        return;
    }
    let i = ((y * width + x) * 4) as usize;
    if i + 3 >= buf.len() {
        return;
    }
    buf[i..i + 4].copy_from_slice(&color);
}

/// Paint a count badge onto a copy of an existing icon (tray). The source icon
/// is RGBA; the badge sits in the bottom-right so the brand mark stays visible.
pub fn composite_badge(
    rgba: &[u8],
    width: u32,
    height: u32,
    needs_len: u32,
) -> Option<Vec<u8>> {
    if needs_len == 0 {
        return None;
    }
    if rgba.len() != (width as usize) * (height as usize) * 4 {
        return None;
    }
    let mut out = rgba.to_vec();
    let badge = width.max(16) / 2;
    let origin_x = width.saturating_sub(badge);
    let origin_y = height.saturating_sub(badge);
    let Some((_, _, overlay)) = overlay_rgba(needs_len) else {
        return Some(out);
    };
    blit_scaled(
        &mut out,
        width,
        height,
        origin_x,
        origin_y,
        badge,
        badge,
        &overlay,
        OVERLAY_SIZE,
        OVERLAY_SIZE,
    );
    Some(out)
}

fn blit_scaled(
    dest: &mut [u8],
    dw: u32,
    dh: u32,
    dx: u32,
    dy: u32,
    d_w: u32,
    d_h: u32,
    src: &[u8],
    sw: u32,
    sh: u32,
) {
    if d_w == 0 || d_h == 0 {
        return;
    }
    for y in 0..d_h {
        for x in 0..d_w {
            let sx = x * sw / d_w;
            let sy = y * sh / d_h;
            let si = ((sy * sw + sx) * 4) as usize;
            if si + 3 >= src.len() {
                continue;
            }
            if src[si + 3] == 0 {
                continue;
            }
            put(
                dest,
                dw,
                dh,
                dx + x,
                dy + y,
                [src[si], src[si + 1], src[si + 2], src[si + 3]],
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_queue_clears_every_surface() {
        assert_eq!(badge_count(0), None);
        assert_eq!(tray_title(0), None);
        assert!(overlay_rgba(0).is_none());
        assert_eq!(tray_tooltip(0), "BossMod AI");
        assert!(!OS_TOAST_ENABLED);
    }

    #[test]
    fn count_mirrors_queue_length() {
        assert_eq!(badge_count(1), Some(1));
        assert_eq!(badge_count(12), Some(12));
        assert_eq!(tray_title(3).as_deref(), Some("3"));
        assert_eq!(tray_tooltip(1), "BossMod AI — 1 thing needs you");
        assert_eq!(tray_tooltip(4), "BossMod AI — 4 things need you");
    }

    #[test]
    fn overlay_is_a_red_marker_with_digits_not_custom_art() {
        let (_, _, one) = overlay_rgba(1).expect("count 1 paints");
        let (_, _, twelve) = overlay_rgba(12).expect("count 12 paints");
        assert_ne!(one, twelve, "different counts must not share a raster");
        assert!(one.chunks(4).any(|px| px == RED), "badge is the red marker");
        assert!(one.chunks(4).any(|px| px == WHITE), "count ink is white");
        // No third hue: this is a marker, not a star or brand glyph.
        let hues: std::collections::HashSet<_> = one
            .chunks(4)
            .filter(|px| px[3] > 0)
            .map(|px| (px[0], px[1], px[2]))
            .collect();
        assert!(hues.len() <= 2, "overlay introduced extra art hues: {hues:?}");
    }

    #[test]
    fn copy_cannot_carry_a_need_body() {
        // The functions only take a count. A secret in a need title has nowhere
        // to go; this pins the signatures so a later "richer tooltip" cannot
        // land without rewriting the test.
        let tooltip = tray_tooltip(2);
        assert!(!tooltip.to_lowercase().contains("token"));
        assert!(!tooltip.contains("sk-"));
        assert!(!tooltip.contains("ghp_"));
        assert!(!tooltip.contains("secret"));
    }

    #[test]
    fn composite_clears_when_empty_and_badges_when_not() {
        let w = 32;
        let h = 32;
        let base = vec![0u8, 80, 160, 255].repeat((w * h) as usize);
        assert!(composite_badge(&base, w, h, 0).is_none());
        let badged = composite_badge(&base, w, h, 2).expect("badge paints");
        assert_ne!(badged, base);
        assert!(badged.chunks(4).any(|px| px == RED));
    }
}
