# Requirements: Phase-1 Paste and Attach in BossMod Chat UI

**Status:** Locked  
**Owner:** Brad — Game Designer / Requirements Analyst  
**Project:** `bossmodai`  
**Artifact:** `/projects/bossmodai/docs/requirements-phase1-paste-attach.md`  
**Intended handoff:** Producer / Feature Planner after open questions are resolved

## 1. Problem statement

BossMod chat is currently text-first. Operators and agents can exchange messages, but sharing screenshots, logs, design notes, or other small files requires copying file paths or pasting raw text manually.

Phase 1 needs a reliable way to add content to chat messages:

- Paste text from the clipboard into the chat composer.
- Paste an image from the clipboard into the chat composer.
- Attach one or more local files from the desktop file picker.
- Send a chat message that includes the pasted text and/or attachments.
- View and retrieve sent attachments from the chat UI.

This requirement defines the capability and acceptance bar. It does not define the implementation.

## 2. Context

Current BossMod architecture includes:

- A Tauri desktop shell.
- A FastAPI backend process.
- A runtime worker process.
- A vanilla JS UI under `ui/`.
- SQLite-backed runtime state.
- Existing chat, task, meeting, and social messaging surfaces.
- Existing host-path and permission constraints for file access.

Chat messages are currently plain text. Any paste/attach capability must work inside the existing desktop UI without requiring a separate file-sharing service.

## 3. Actors

1. **Operator**
   - Uses the BossMod desktop chat UI.
   - Pastes text or images from the clipboard.
   - Attaches local files from disk.
   - Sends messages to agents, humans, task threads, or chat channels.
   - Reviews and downloads sent attachments.

2. **Agent**
   - Receives messages that may include attachment metadata.
   - May be able to reference or open attachments if permissions allow.
   - Is not required in Phase 1 to create attachments unless explicitly confirmed.

## 4. Scope in

Phase 1 is in scope when it delivers:

> **Surface coverage (locked):** Task threads and operator chat. Meeting room and social channels are out of scope for Phase 1.
> **Input mechanisms (locked):** Clipboard paste (text + image) and desktop file picker. Drag-and-drop is out of scope for Phase 1.

1. **Text paste**
   - The operator can paste plain text from the clipboard into the chat composer.
   - Pasted text appears in the composer before sending.
   - Sending the message preserves the pasted text.

2. **Image paste**
   - The operator can paste an image from the clipboard into the chat composer.
   - The composer shows an image preview before sending.
   - The operator can remove the pasted image before sending.
   - Sending the message includes the image as an attachment.

3. **File attach**
   - The operator can attach one or more files using the desktop file picker.
   - The composer shows each attached file's name and size before sending.
   - The operator can remove an attached file before sending.
   - Sending the message includes the selected files as attachments.

4. **Message display**
   - Sent messages display attachment indicators in the chat history.
   - Image attachments display a thumbnail or preview where supported.
   - Non-image attachments display at least the file name and file size.
   - The operator can retrieve the original file from the chat UI.

5. **Persistence**
   - Sent attachments remain available after the chat is reloaded or the desktop app is restarted.
   - Attachment metadata remains associated with the correct message and conversation.

6. **Failure handling**
   - Unsupported or oversized attachments produce a visible inline error.
   - A failed attachment does not create a broken or partially sent message.
   - The composer remains usable after an attachment error.

7. **Documentation**
   - The capability is documented for operators.
   - Documentation includes at least one successful paste example and one successful attach example.

8. **Agent image post**
   - An agent may post an image to a conversation it participates in.
   - The image must be at a readable local path within the allowed host-path roots.
   - The posted image appears as an image attachment in the conversation, with the same display and retrieval behavior as operator-sent images.
   - This is the only agent-side attachment capability in Phase 1. Agent arbitrary file creation remains out of scope.

## 5. Scope out

Phase 1 does not include:

- Rich text editing.
- Markdown preview editing.
- Inline code block formatting beyond existing chat behavior.
- Drag-and-drop attachment upload.
- Upload from remote URLs.
- Cloud storage or external file sharing.
- Collaborative file editing.
- Image editing, cropping, resizing, or annotation.
- OCR or automatic image understanding.
- PDF preview or document preview beyond basic file metadata.
- Virus scanning or malware analysis.
- Agent-side file creation or arbitrary attachment generation beyond the locked agent image post (see section 10).
- Large-file streaming or resumable upload.
- Mobile or web-only attachment flows.
- Cross-machine file synchronization.

## 6. Functional requirements

### 6.1 Composer paste behavior

- The chat composer must accept standard paste events from the desktop OS.
- Pasting plain text must insert text at the cursor position.
- Pasting an image must not insert binary image data as visible text.
- Pasting mixed content must not corrupt the composer state.
- The composer must remain editable after paste.

### 6.2 Composer attachment behavior

- The chat composer must provide a visible attach control.
- The attach control must open the native desktop file picker.
- The file picker must allow selecting multiple files.
- Selected files must appear in the composer as pending attachments.
- Pending attachments must show file name and size.
- Pending attachments must be removable individually.
- The send action must include all pending attachments with the message.

### 6.3 Send behavior

- A message with no text and at least one attachment must be sendable.
- A message with text and no attachments must continue to work as before.
- A message with text and attachments must store the text and attachments together.
- Sending must clear the composer and pending attachments after success.
- If sending fails, the composer must not silently lose the pending content.

### 6.4 Chat history behavior

- Chat history must display attachment metadata for sent messages.
- Image attachments must show a visual preview or thumbnail.
- Non-image attachments must show a file chip or equivalent indicator.
- The operator must be able to open or download an attachment from chat history.
- Attachment retrieval must return the same file content that was originally sent.

### 6.5 Persistence behavior

- Attachments must survive app restart.
- Attachment metadata must remain linked to the correct message.
- Deleted or failed attachments must not leave dangling message references.
- Attachment storage must be inspectable by the operator or a teammate with appropriate access.

### 6.6 Security and safety behavior

- Attachments must be treated as inert files by default.
- The UI must not execute attachment files automatically.
- File names must be sanitized for display and retrieval.
- Attachments from outside allowed host paths must not be silently accepted.
- If a file cannot be read, copied, or stored, the UI must show a clear error.

### 6.7 Agent image post behavior

- An agent may post an image to a conversation it participates in when the image is at a readable local path within the allowed host-path roots.
- The posted image must appear in the chat history as an image attachment with a thumbnail preview, identical in display to an operator-pasted image.
- The agent image post must respect the per-attachment size cap (default 10 MB, operator-configurable).
- If the image path is unreadable, outside allowed roots, or exceeds the size cap, the post must fail with a visible error and must not create a broken message.
- This is the only agent-side attachment capability in Phase 1. Agent arbitrary file creation is out of scope.

### 6.8 Context-dependent storage behavior

- Attachment storage path must be derived deterministically from the message context:
  - Thread-scoped message → thread's own attachments folder.
  - 1:1 direct chat → receiving agent's attachment folder.
  - Project-scoped channel → owning project, scoped by channel id.
  - Main/operator chat or unscoped social channel → shared default attachments root.
- Every attachment record must persist its absolute local path.
- Retrieval must return that exact stored path.
- Storage must never escape the allowed host-path roots.
- The exact directory layout is an implementation detail; the context-dependent, deterministic, stable-path behavior is the locked requirement.

### 6.9 Format tier preview behavior

- Tier 1 (image: PNG, JPEG, GIF, WebP): thumbnail preview in composer and chat history.
- Tier 2 (text-like: .txt, .log, .md, .csv, .json, .yaml, .yml, .xml, common source-code extensions): name + size chip, no inline preview.
- Tier 3 (document: .pdf, .doc/.docx, .xls/.xlsx, .ppt/.pptx): name + size chip, no preview.
- Tier 4 (other): name + size chip.
- Blocklist extensions (.exe, .msi, .bat, .cmd, .sh, .ps1, .app, .dmg, .deb, .rpm, .apk) must be rejected with a visible inline error and never silently accepted.

## 7. Acceptance criteria

### AC-1: Plain text paste

Given the operator has copied plain text to the clipboard, when the operator focuses a BossMod chat composer and pastes, the composer displays the pasted text. When the operator sends the message, the sent message contains the same text.

### AC-2: Image paste

Given the operator has copied an image to the clipboard, when the operator pastes into a BossMod chat composer, the composer displays an image preview and does not display raw binary text. When the operator sends the message, the sent message includes the image as an attachment.

### AC-3: Remove pasted image before sending

Given the operator has pasted an image into the composer, when the operator removes the pending image, the composer no longer shows that image. When the operator sends the message, the sent message does not include that image.

### AC-4: Attach file from picker

Given the operator opens the chat attach control, when the operator selects one local file, the composer displays the file name and file size. When the operator sends the message, the sent message includes that file as an attachment.

### AC-5: Attach multiple files

Given the operator opens the chat attach control, when the operator selects at least two local files, the composer displays each file name and size. When the operator sends the message, the sent message includes all selected files as attachments.

### AC-6: Remove attached file before sending

Given the operator has attached at least two files, when the operator removes one pending attachment, the composer no longer shows that file. When the operator sends the message, the sent message includes the remaining attachments but not the removed file.

### AC-7: Send attachment-only message

Given the composer has no text and at least one pending attachment, when the operator sends the message, the chat history displays a message with the attachment and no required text placeholder error.

### AC-8: Retrieve sent attachment

Given a sent message includes an attachment, when the operator uses the chat UI to open or download the attachment, the retrieved file has the same file name and content as the originally attached file.

### AC-9: Attachment persists after restart

Given a sent message includes an attachment, when the operator restarts the BossMod desktop app and opens the same conversation, the message still displays the attachment and the operator can retrieve the file.

### AC-10: Unsupported file rejection

Given an attachment type or size is outside the locked limits, when the operator attempts to attach or send it, the UI displays a visible error identifying the problem. No broken message is created, and the composer remains usable.

### AC-11: Failed file read rejection

Given a selected file cannot be read or copied, when the operator attempts to send the message, the UI displays a visible error for that file. The message is not sent as if the attachment succeeded.

### AC-12: Existing text chat remains working

Given a normal text-only chat message, when the operator sends the message without attachments, the message appears in chat history and behaves the same as before the paste/attach feature.

### AC-13: Attachment metadata correctness

Given a sent message includes multiple attachments, when the operator inspects the chat history, each attachment is associated with the correct message and conversation.

### AC-14: No automatic execution

Given a sent attachment is an executable or script file, when the operator views the attachment in chat history, the UI does not execute the file automatically.

### AC-15: Documentation exists

Given the feature is complete, when the operator follows the documented instructions, they can successfully paste text, paste an image, attach a file, send the message, and retrieve the attachment.

### AC-16: Agent image post success

An agent posts an image from a readable local path within allowed host-path roots to a conversation it participates in. The image appears in chat history with a thumbnail preview, identical in display to an operator-pasted image. The attachment record stores the absolute local path.

### AC-17: Agent image post failure

An agent attempts to post an image that is (a) unreadable, (b) outside allowed host-path roots, or (c) exceeds the configured size cap. The post fails with a visible error. No broken or partially sent message is created.

### AC-18: Thread-scoped attachment storage

A message sent in a task thread stores its attachment under the thread's own attachments folder. Retrieval returns the exact stored path. The path is stable across app restarts.

### AC-19: 1:1 chat attachment storage

A message sent in a 1:1 direct chat stores its attachment under the receiving agent's attachment folder. Retrieval returns the exact stored path.

### AC-20: Project channel attachment storage

A message sent in a project-scoped channel stores its attachment under the owning project, scoped by channel id. Retrieval returns the exact stored path.

### AC-21: Main/unscoped attachment storage

A message sent in main/operator chat or an unscoped social channel stores its attachment under the shared default attachments root. Retrieval returns the exact stored path.

### AC-22: Image tier preview

A Tier 1 image attachment (PNG, JPEG, GIF, WebP) displays a thumbnail preview in both the composer (before send) and chat history (after send).

### AC-23: Non-image tier preview

A Tier 2, Tier 3, or Tier 4 attachment displays a name + size chip only. No inline preview or thumbnail is shown.

### AC-24: Blocklist rejection

A file with a blocklisted extension (.exe, .msi, .bat, .cmd, .sh, .ps1, .app, .dmg, .deb, .rpm, .apk) is rejected at attach or paste time with a visible inline error. The file is never silently accepted or partially stored.

## 8. Constraints

- The capability must work inside the existing BossMod desktop UI.
- Phase 1 must remain local-first and must not require a new external file-sharing service.
- Attachments must respect existing host-path and permission rules.
- The UI must remain responsive during normal paste and attach operations.
- Image paste preview should appear quickly enough that the operator does not assume the paste failed.
- File attach and send should complete quickly for small local files on a normal development machine.
- The feature must not require manual file path entry for the common operator flow.
- Existing chat message history must not be corrupted by attachment metadata.

## 9. Non-goals

Phase 1 does not need to:

- Provide a full file manager.
- Provide cloud sync.
- Provide shared links.
- Provide preview for every file type.
- Provide image editing.
- Provide agent-side file creation.
- Provide automatic content analysis.
- Provide enterprise-level scanning or compliance.
- Replace task-thread file handoffs.
- Define exact UI layout or component implementation.

## 10. Locked defaults and configurable parameters

The following parameters are locked for Phase 1. The per-attachment size cap is the only parameter intended to be operator-tunable; all others are fixed unless the operator explicitly overrides.

- Maximum file size per attachment: **10 MB** (shipped default).
- Maximum attachments per message: 5.
- Default retention: attachments persist until the conversation or project is deleted.

**Context-dependent storage (locked default):**

The attachment storage path is derived deterministically from the message context it belongs to. The locked mapping is:

- A thread-scoped message stores under the thread own attachments folder (thread is a first-class storage entity).
- A 1:1 (direct) chat stores under the receiving agent attachment folder.
- A project-scoped channel stores under the owning project, scoped by channel id.
- Main/operator chat or an unscoped social channel stores under a shared default attachments root.

Every attachment record persists its absolute local path. Retrieval returns that exact path. Storage never escapes the allowed host-path roots. The exact directory layout is an implementation detail; the context-dependent, deterministic, stable-path behavior is the locked requirement.

**Format scope (locked default):**

Allowed: any local file within the size cap, minus the blocklist below. Preview behavior is tiered:

- **Tier 1 — Image** (PNG, JPEG, GIF, WebP): thumbnail preview.
- **Tier 2 — Text-like** (.txt, .log, .md, .csv, .json, .yaml, .yml, .xml, common source-code extensions): name + size chip, no inline preview.
- **Tier 3 — Document** (.pdf, .doc/.docx, .xls/.xlsx, .ppt/.pptx): name + size chip, no preview.
- **Tier 4 — Other**: name + size chip.

**Blocklist** (rejected with a visible error, never silently accepted): .exe, .msi, .bat, .cmd, .sh, .ps1, .app, .dmg, .deb, .rpm, .apk. The blocklist is a fixed Phase-1 default, not operator-tunable.

**Agent image post (locked default):**

Agents may post an image to a conversation they participate in when the image is at a readable local path within the allowed host-path roots. This is the only agent-side attachment capability in Phase 1. Agent arbitrary file creation remains out of scope.

**Configurable parameter:**

- The per-attachment size cap is configurable by the operator via a settings key (`bossmod.attach.max_size_mb` or equivalent). The shipped default is 10 MB. Operators may raise or lower this value; the UI and send pipeline must respect the configured value at send time. No other parameter is operator-tunable in Phase 1.

> **Amendment note:** The three defaults above — context-dependent storage mapping, format tiers + blocklist, and agent image post — were locked per the phase-1 amendment directive. The operator may override any of them with an explicit decision recorded in the open-questions log.


## 11. Open questions

1. Which chat surfaces must support paste/attach in Phase 1? — RESOLVED: Task threads and operator chat. Meeting room and social channels are out of scope for Phase 1.

2. Are agents allowed to create attachments in Phase 1, or only receive them? — RESOLVED: Agents may post an image to a conversation they participate in (locked default, see section 10). Agent arbitrary file creation remains out of scope.

3. What are the locked limits for file size and attachment count? — RESOLVED: 10 MB per-attachment default (operator-tunable via `bossmod.attach.max_size_mb`), 5 attachments per message. See section 10.

4. Which file types are allowed? — RESOLVED: Any local file within the size cap minus a fixed blocklist (executables, installers, scripts, packages). Preview behavior is tiered (image → thumbnail; text-like / document / other → name + size chip). See section 10.

5. Where should attachments be stored? — RESOLVED: Context-dependent storage mapping (task-thread → project scoped by task id; project channel → project scoped by channel id; main / unscoped → shared default root). See section 10.

6. Should attachments be visible to all participants in a conversation, or only the sender and operator? — RESOLVED: All attachments in a thread are visible to every participant in that thread. No per-agent read gate.

7. Should the UI support drag-and-drop in Phase 1, or is paste plus file picker enough? — RESOLVED: Paste plus file picker only. Drag-and-drop remains out of scope for Phase 1.

8. Should non-image files show a text preview, or only file name and size? — RESOLVED: Name + size chip only; no inline text preview for non-image files in Phase 1. See section 10 format tiers.

9. Should attachments from host paths outside the allowed roots require an explicit consent card? — RESOLVED: No consent card required. Source paths are unrestricted; the constraint is on storage destination only.

10. What is the expected retention and cleanup behavior? — RESOLVED: Attachments persist until the conversation or project is deleted. See section 10.

## 12. Done bar

Phase 1 is done when:

- The requirements above are locked with operator decisions recorded for all open questions.
- A named requirements artifact exists at `/projects/bossmodai/docs/requirements-phase1-paste-attach.md`.
- The acceptance criteria are individually checkable by QA or the operator.
- The handoff recipient can begin sequencing without inventing scope.
- Any remaining ambiguity is explicitly marked as an open question, not silently assumed.

## 13. Fail bar

Phase 1 fails if:

- Paste or attach behavior is defined only by UI screenshots without checkable behavior.
- Acceptance criteria depend on subjective judgment.
- Attachment storage, permissions, or limits are left undefined after lock.
- The feature requires a new external service without operator approval.
- Existing text chat is broken or unverified.
- Attachments can be sent without a reliable way to retrieve them.
