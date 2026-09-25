# Operator Guide: Paste and Attach (Phase 1)

This guide covers the Phase-1 paste and attach capability in the BossMod chat UI. It supports plain-text paste, image paste, file attachment, sending messages with attachments, and downloading attachments from chat history.

## Limits

- Maximum file size per attachment: **10 MB** by default.
- Maximum attachments per message: **5**.
- Retention: attachments persist until the conversation or project is deleted.
- Attachments are stored for download and preview. They are **not** executed automatically.

## Blocklist

The following file extensions are rejected at upload:

`.exe`, `.msi`, `.bat`, `.cmd`, `.sh`, `.ps1`, `.app`, `.dmg`, `.deb`, `.rpm`, `.apk`

If a file is blocked, the composer shows an inline error and the file is not uploaded.

## Changing the Size Cap

The size cap is controlled by the setting:

```text
bossmod.attach.max_size_mb
```

- Type: integer
- Default: `10`
- Unit: megabytes
- Read at upload time; changes take effect without restarting the upload endpoint.

To change it, update the setting in the BossMod settings store or settings UI under the advanced/system settings area. For example, to allow 25 MB uploads, set the value to `25`.

## Worked Example 1: Paste Text and Send

1. Open a conversation, thread, or channel.
2. Paste plain text into the composer.
3. Press **Send** or use the normal send action.
4. The message appears in the chat history with the pasted text.

Expected result: the text message is visible in the transcript and persists after the page is reloaded.

## Worked Example 2: Attach a File and Download It

1. Open a conversation, thread, or channel.
2. Click the **attach** button in the composer.
3. Select one or more allowed files from the file picker.
4. Each uploaded file appears as a pending attachment chip above the composer.
5. Optionally add text to the message.
6. Send the message.
7. In the chat history, click the file chip.

Expected result: the file downloads from the attachment endpoint and matches the uploaded file.

To remove a file before sending, click the **×** on its pending attachment chip.

## Worked Example 3: Paste an Image and See the Thumbnail

1. Copy an image to the clipboard.
2. Open a conversation, thread, or channel.
3. Paste the image into the composer.
4. The image upload starts automatically.
5. The pending image appears as an attachment chip.
6. Send the message.
7. In the chat history, the image renders as a thumbnail.
8. Click the thumbnail to open the full-size image in a new tab.

Expected result: the image is stored, displayed as a thumbnail in history, and can be opened or downloaded.

## Failure Behavior

- If an upload fails, the composer shows an inline error and remains usable.
- If a message send fails, pending attachments are kept so the operator can retry.
- If a file exceeds the size cap, upload is rejected with a size error.
- If a file extension is blocklisted, upload is rejected with a blocklist error.
- If a file name is invalid, upload is rejected with an invalid-name error.
- If more than 5 attachments are added, the extra attachment is rejected.
