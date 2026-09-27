// BossMod AI — native clipboard image read for the chat composer.
//
// In the WebKitGTK webview a pasted screenshot reaches the page's `paste`
// event as neither a file nor text, so the composer asks the shell instead.
// The image is PNG-encoded here and returned as raw bytes: a 1080p RGBA frame
// is ~8 MB, and as a JSON number array it would be several times that.

use tauri::ipc::Response;
use tauri_plugin_clipboard_manager::ClipboardExt;

/// Encode an RGBA8 frame as PNG.
///
/// `rgba` must hold exactly `width * height * 4` bytes, row-major, one byte
/// per channel.
///
/// Returns the PNG file bytes, or an error when the buffer length does not
/// match the dimensions or the encoder fails.
pub fn encode_png(width: u32, height: u32, rgba: &[u8]) -> Result<Vec<u8>, String> {
    let expected = (width as usize)
        .checked_mul(height as usize)
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| format!("Image size {width}x{height} is too large"))?;
    if rgba.len() != expected {
        return Err(format!(
            "Image buffer is {} bytes, expected {expected} for {width}x{height} RGBA",
            rgba.len()
        ));
    }
    let mut out = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut out, width, height);
        encoder.set_color(png::ColorType::Rgba);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().map_err(|e| e.to_string())?;
        writer.write_image_data(rgba).map_err(|e| e.to_string())?;
        writer.finish().map_err(|e| e.to_string())?;
    }
    Ok(out)
}

/// Read the system clipboard's image and return it as PNG bytes.
///
/// Async on purpose: the plugin warns that `read_image` can deadlock on
/// Linux when called on the main thread, and synchronous commands run there.
///
/// Returns the PNG as a binary IPC response (an `ArrayBuffer` in the page),
/// or an error string starting "The clipboard holds no image" when there is
/// no image to read, followed by the underlying reason.
#[tauri::command]
pub async fn read_clipboard_image_png(app: tauri::AppHandle) -> Result<Response, String> {
    let image = app
        .clipboard()
        .read_image()
        .map_err(|e| format!("The clipboard holds no image: {e}"))?;
    let png_bytes = encode_png(image.width(), image.height(), image.rgba())?;
    Ok(Response::new(png_bytes))
}

#[cfg(test)]
mod tests {
    use super::encode_png;

    #[test]
    fn round_trip_decodes_to_the_same_size_and_pixels() {
        let (width, height) = (3u32, 2u32);
        let rgba: Vec<u8> = (0..(width * height * 4)).map(|i| (i * 7 % 256) as u8).collect();

        let bytes = encode_png(width, height, &rgba).expect("encodes");

        let decoder = png::Decoder::new(bytes.as_slice());
        let mut reader = decoder.read_info().expect("valid png");
        let mut buf = vec![0; reader.output_buffer_size()];
        let info = reader.next_frame(&mut buf).expect("one frame");
        assert_eq!((info.width, info.height), (width, height));
        assert_eq!(info.color_type, png::ColorType::Rgba);
        assert_eq!(&buf[..info.buffer_size()], rgba.as_slice());
    }

    #[test]
    fn wrong_buffer_length_is_an_error() {
        let err = encode_png(2, 2, &[0u8; 15]).expect_err("15 bytes is not 2x2 RGBA");
        assert_eq!(err, "Image buffer is 15 bytes, expected 16 for 2x2 RGBA");
    }
}
