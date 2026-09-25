# TDD: Phase-1 Paste and Attach — BossMod Chat UI

**Artifact:** `/projects/bossmodai/docs/spec-paste-attach.md`
**Author:** Brian — Tech Spec Author
**Upstream:** `/projects/bossmodai/docs/requirements-phase1-paste-attach.md` (locked, all open questions resolved)
**Status:** Ready for Build Engineer
**Handoff:** Charles (Build Engineer) → Sarah (QA Cert Lead)

---

## 1. Scope and Upstream Bar

This TDD covers the full Phase-1 paste/attach capability: text paste, image paste, file attach, send with attachments, display in chat history, persistence, failure handling, and operator documentation.

**Entry dependency:** The requirements doc is locked. All 15 ACs are the acceptance bar. No new scope is invented here.

**Locked parameters carried from requirements §10:**

| Parameter | Value |
|---|---|
| Max file size per attachment | 10 MB (operator-tunable via `bossmod.attach.max_size_mb`) |
| Max attachments per message | 5 |
| Retention | Persist until conversation/project deleted |
| Blocklist | `.exe .msi .bat .cmd .sh .ps1 .app .dmg .deb .rpm .apk` |
| Preview tiers | T1 image → thumbnail; T2/T3/T4 → name+size chip |
| Agent image post | Allowed (readable local path within host roots) |
| Storage mapping | Context-dependent (thread → project/task; 1:1 → agent folder; channel → project/channel; unscoped → shared root) |

**Out of scope (do NOT build):** drag-and-drop, remote URL upload, cloud storage, image editing, OCR, PDF preview, virus scanning, agent arbitrary file creation, large-file streaming, mobile/web-only flows.

---

## 2. Contracts

### 2.1 Database — New `attachments` table

**File:** `db/schema.sql` (append after last table)

```sql
CREATE TABLE IF NOT EXISTS attachments (
    id              VARCHAR PRIMARY KEY DEFAULT (gen_random_uuid()),
    message_id      VARCHAR NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    file_name       VARCHAR NOT NULL,
    file_size       BIGINT  NOT NULL,
    mime_type       VARCHAR NOT NULL,
    storage_path    VARCHAR NOT NULL,
    preview_tier    VARCHAR NOT NULL CHECK (preview_tier IN ('image', 'text', 'document', 'other')),
    created_at      TIMESTAMP DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_attachments_message_id ON attachments(message_id);
```

**Invariants:**
- `file_name` is sanitized (no path separators, no null bytes, max 255 chars).
- `storage_path` is an absolute local path inside an allowed host root.
- `file_size` is the byte count at write time.
- `mime_type` is detected from file extension (not content sniffing in Phase 1).
- `preview_tier` is derived from extension per the tier table in §2.4.
- A message with zero attachments has zero rows; a message with N attachments has exactly N rows.

### 2.2 Database — CRUD module

**File:** `db/attachments.py` (new)

```python
"""BossMod AI — Attachment CRUD."""

def create_attachment(
    message_id: str,
    file_name: str,
    file_size: int,
    mime_type: str,
    storage_path: str,
    preview_tier: str,
) -> Attachment: ...

def get_attachments_for_message(message_id: str) -> list[Attachment]: ...

def get_attachment_by_id(attachment_id: str) -> Attachment | None: ...

def delete_attachments_for_message(message_id: str) -> int: ...
```

`Attachment` is a Pydantic model in `core/models/attachment.py` (new) with fields matching the table columns.

### 2.3 API — Attachment endpoints

**File:** `api/routes/attachments.py` (new), registered in `api/routes/__init__.py`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/attachments/upload` | Receive a file (multipart), validate, store, return metadata JSON |
| `GET` | `/api/attachments/{attachment_id}` | Retrieve a stored file (stream download) |
| `GET` | `/api/attachments/{attachment_id}/preview` | Return image thumbnail for T1 files (optional, 204 if not image) |

**Upload contract (`POST /api/attachments/upload`):**

Request: `multipart/form-data`
- `file` — the file blob
- `message_context` — JSON string: `{"type": "thread"|"direct"|"channel"|"unscoped", "id": "<context_id>"}`
- `original_name` — the file name as shown in the picker

Response `201`:
```json
{
  "id": "<uuid>",
  "file_name": "sanitized_name.ext",
  "file_size": 12345,
  "mime_type": "image/png",
  "storage_path": "/abs/path/to/stored/file",
  "preview_tier": "image"
}
```

Response `413` (size exceeded), `415` (blocklisted), `400` (bad context):
```json
{"error": "<human-readable reason>", "code": "SIZE_EXCEEDED"|"BLOCKLISTED"|"BAD_CONTEXT"}
```

**Download contract (`GET /api/attachments/{id}`):**
- `200` with `Content-Disposition: attachment; filename="<original_name>"`, `Content-Type` from stored mime.
- `404` if not found or file missing on disk.

### 2.4 Storage path derivation

**File:** `core/attachments.py` (new) — pure logic, no I/O.

```python
def derive_storage_root(context_type: str, context_id: str, base_dir: str) -> str:
    """
    Returns the absolute directory where attachments for this context live.
    - thread   → {base_dir}/projects/{context_id}/attachments
    - direct   → {base_dir}/agents/{context_id}/attachments
    - channel  → {base_dir}/projects/{project_id}/channels/{channel_id}/attachments
    - unscoped → {base_dir}/shared/attachments
    """
```

`base_dir` is the company data root (same root that holds `db/schema.sql`). The function must create the directory if it does not exist (the caller handles this; the function just returns the path).

**File naming on disk:** `{uuid}_{sanitized_name}` to avoid collisions.

### 2.5 Validation rules (enforced at upload)

| Check | Failure code |
|---|---|
| `file_size > max_size_mb * 1024 * 1024` | `SIZE_EXCEEDED` |
| Extension in blocklist | `BLOCKLISTED` |
| Attachment count for the message already = 5 | `TOO_MANY` |
| `context_type` not one of the four valid values | `BAD_CONTEXT` |
| File cannot be read/copied (I/O error) | `READ_FAILED` |
| File name contains path separators or null bytes | `INVALID_NAME` |

`max_size_mb` is read from `settings` table key `bossmod.attach.max_size_mb`, default `10`.

### 2.6 Message send — attachment linkage

The existing send path (WebSocket or REST, whichever the UI currently uses) must be extended:

- The send payload gains an optional `attachment_ids: list[str]` field.
- On successful message insert, the server links each attachment row's `message_id` to the new message.
- If any attachment link fails, the entire send is rolled back (transactional).
- The WebSocket broadcast for the new message includes `attachments: [{id, file_name, file_size, mime_type, preview_tier}]`.

### 2.7 UI — Composer extension

**File:** `ui/static/js/conversation/composer.js`

The `createComposer` factory gains two new optional deps:

```javascript
@param {(files: File[], context: object) => Promise<Array<object>>} deps.onAttach
  Uploads files to the server; resolves with metadata array. Rejects on any failure.
@param {() => object} deps.getContext
  Returns the current message context {type, id} for the active conversation.
```

New public methods on the returned object:

```javascript
addPendingAttachment(meta)     // show a chip in the composer
removePendingAttachment(id)    // remove one chip
getPendingAttachments()        // return array of pending metadata
clearPendingAttachments()      // called after successful send
```

New UI elements inside the composer:
- An **attach button** (paperclip icon) that triggers a hidden `<input type="file" multiple>`.
- A **pending attachments strip** above the textarea showing each file's name + size + remove (×) button.
- A **paste handler** on the textarea:
  - If `event.clipboardData` contains an image (`Files` entry with `type` starting `image/`), intercept and route to `onAttach`.
  - If plain text, let the default paste proceed.
  - If mixed, handle text via default, images via `onAttach`.

**Send gate:** A message is sendable when `text is non-empty OR pendingAttachments.length > 0`. The send action includes `attachment_ids` from pending attachments.

### 2.8 UI — Chat history display

**File:** `ui/static/js/conversation/message.js` (extend)

When rendering a message that has `attachments` array:
- **T1 (image):** render an `<img>` thumbnail (max-height 120px, object-fit contain). Click opens full-size in a new tab via the download endpoint.
- **T2/T3/T4:** render a file chip: `[icon] file_name (size)` — clickable, triggers download via `GET /api/attachments/{id}`.
- Multiple attachments render as a horizontal wrap row below the message text.
- A message with no text but attachments renders the attachment row with no empty text bubble.

### 2.9 Settings key

**File:** `db/settings.py` (extend or seed)

Key: `bossmod.attach.max_size_mb`
- Type: integer
- Default: `10`
- Read at upload time; not cached.

### 2.10 Operator documentation

**File:** `docs/OPERATOR_PASTE_ATTACH.md` (new)

Must include:
- One worked example: paste text → send → see in history.
- One worked example: attach a file → send → download from history.
- One worked example: paste an image → send → see thumbnail.
- The blocklist and size cap.
- How to change the size cap via settings.

---

## 3. Step Sequence

Each step is atomic. A junior dev should be able to execute them in order without guessing.

### Step 1 — Database schema

1. Open `db/schema.sql`.
2. Append the `attachments` table DDL and index (from §2.1) after the last existing table.
3. Run the migration: apply the new DDL to the dev database (the project's existing migration mechanism — check `db/connection.py` for how schema is applied on startup).
4. Verify: `SELECT * FROM attachments LIMIT 1;` returns empty without error.

**File touch:** `db/schema.sql`

### Step 2 — Core model

1. Create `core/models/attachment.py`:
   - Pydantic model `Attachment` with fields: `id: str`, `message_id: str`, `file_name: str`, `file_size: int`, `mime_type: str`, `storage_path: str`, `preview_tier: str`, `created_at: datetime`.
2. Add `from core.models.attachment import Attachment` to `core/models/__init__.py` if one exists.

**File touch:** `core/models/attachment.py` (new), `core/models/__init__.py` (edit)

### Step 3 — Storage path logic

1. Create `core/attachments.py`:
   - `derive_storage_root(context_type, context_id, base_dir) -> str` per §2.4.
   - `sanitize_file_name(name: str) -> str`: strip path separators, replace null bytes, truncate to 255 chars.
   - `detect_preview_tier(extension: str) -> str`: return `'image'`, `'text'`, `'document'`, or `'other'` per the tier table.
   - `detect_mime_type(extension: str) -> str`: map extension to MIME (use `mimetypes` stdlib as fallback).
   - `BLOCKLIST: frozenset[str]` with the 10 extensions.
   - `is_blocklisted(extension: str) -> bool`.
2. Write unit tests in `tests/test_attachments_core.py` covering:
   - All four context types produce correct paths.
   - `sanitize_file_name` strips `../`, null bytes, truncates long names.
   - `detect_preview_tier` for each tier's extensions.
   - `is_blocklisted` for all 10 blocklisted extensions and a few allowed ones.

**File touch:** `core/attachments.py` (new), `tests/test_attachments_core.py` (new)

### Step 4 — DB CRUD

1. Create `db/attachments.py` with the four functions from §2.2.
2. Use the existing `db/crud.py` helpers (`execute`, `fetch_all`, `insert_returning`, `query_one`).
3. Write tests in `tests/test_attachments_db.py`:
   - `create_attachment` inserts and returns the row.
   - `get_attachments_for_message` returns rows in insertion order.
   - `get_attachment_by_id` returns None for missing id.
   - `delete_attachments_for_message` removes all rows for a message.
   - FK cascade: deleting a message removes its attachments.

**File touch:** `db/attachments.py` (new), `tests/test_attachments_db.py` (new)

### Step 5 — API upload endpoint

1. Create `api/routes/attachments.py`:
   - `router = APIRouter(prefix="/api/attachments", tags=["attachments"])`.
   - `POST /upload`:
     - Parse multipart form.
     - Read `message_context` JSON, validate `type` is one of the four.
     - Call `derive_storage_root`.
     - Check blocklist, size cap (read from settings), name validity.
     - Write file to disk: `{storage_root}/{uuid}_{sanitized_name}`.
     - Insert DB row via `db.attachments.create_attachment` (message_id is a placeholder `"pending"` until the message is created — see Step 7).
     - Return 201 with metadata.
   - `GET /{attachment_id}`:
     - Look up row. If missing or file gone → 404.
     - Stream file with correct headers.
   - `GET /{attachment_id}/preview`:
     - If `preview_tier != 'image'` → 204.
     - Else stream the image.
2. Register the router in `api/routes/__init__.py`.
3. Write integration tests in `tests/test_attachments_api.py`:
   - Upload a small PNG → 201, file exists on disk, DB row exists.
   - Upload a `.exe` → 415 BLOCKLISTED.
   - Upload a file over the size cap → 413 SIZE_EXCEEDED.
   - Upload with bad context type → 400.
   - Download a previously uploaded file → 200, content matches.
   - Download a non-existent id → 404.
   - Preview on a non-image → 204.

**File touch:** `api/routes/attachments.py` (new), `api/routes/__init__.py` (edit), `tests/test_attachments_api.py` (new)

### Step 6 — Settings seed

1. In the settings initialization path (check `db/settings.py` or `main.py` startup), seed `bossmod.attach.max_size_mb = 10` if not present.
2. Verify the upload endpoint reads this value (not a hardcoded constant).

**File touch:** `db/settings.py` or `main.py` (edit, minimal)

### Step 7 — Message send extension

1. In the existing message-send code path (identify: likely in `api/websocket.py` handler or a REST route that calls `db.messages.create_message`):
   - Accept optional `attachment_ids: list[str]` in the payload.
   - After inserting the message row, update each attachment's `message_id` from `"pending"` to the new message id (single transaction).
   - If the update fails for any attachment, roll back the message insert.
   - Include the attachment metadata list in the WebSocket broadcast.
2. Write tests in `tests/test_message_attachments.py`:
   - Send a message with 2 attachment_ids → both linked, broadcast includes them.
   - Send a message with 0 attachments → works as before (AC-12).
   - Send with an attachment_id that doesn't exist → 400, no message created.
   - Send with 6 attachments → 400 TOO_MANY.

**File touch:** whichever file owns the send path (likely `api/websocket.py` or a route file), `tests/test_message_attachments.py` (new)

### Step 8 — UI: Composer paste and attach

1. In `ui/static/js/conversation/composer.js`:
   - Add `deps.onAttach` and `deps.getContext` to the factory signature.
   - Add a hidden `<input type="file" multiple>` element.
   - Add an attach button (paperclip SVG or unicode) next to the send button.
   - On attach button click → trigger the file input's `click()`.
   - On file input `change` → call `onAttach(files, context)` → for each resolved metadata, call `addPendingAttachment(meta)`.
   - On `onAttach` rejection → show an inline error strip below the composer (file name + reason), keep composer usable.
   - Add a `paste` event listener on the textarea:
     - Check `event.clipboardData.files` for image entries.
     - If found, `event.preventDefault()`, call `onAttach(imageFiles, context)`.
     - If not, let default paste proceed.
   - Implement `addPendingAttachment`, `removePendingAttachment`, `getPendingAttachments`, `clearPendingAttachments`.
   - Render pending attachments as a strip of chips above the textarea.
   - Update the send gate: sendable if text non-empty OR pending attachments > 0.
   - On successful send: call `clearPendingAttachments()`.
   - On send failure: keep pending attachments (do not clear).
2. Wire the new deps at the call site (wherever `createComposer` is invoked — likely `conversation.js` or `chrome.js`):
   - `onAttach`: `async (files, ctx) => { const results = []; for (const f of files) { const meta = await apiClient.uploadAttachment(f, ctx); results.push(meta); } return results; }`
   - `getContext`: return the current conversation's context type and id.

**File touch:** `ui/static/js/conversation/composer.js` (edit), `ui/static/js/conversation/conversation.js` or `chrome.js` (edit, wire deps), `ui/static/js/api-client.js` (add `uploadAttachment` method)

### Step 9 — UI: Chat history attachment display

1. In `ui/static/js/conversation/message.js`:
   - When rendering a message, check for `message.attachments` array.
   - If present, render an attachment row below the text:
     - T1: `<img src="/api/attachments/{id}/preview" class="max-h-[120px] object-contain" onclick="window.open('/api/attachments/{id}')">`
     - T2/T3/T4: `<a href="/api/attachments/{id}" class="file-chip">[icon] {file_name} ({humanSize})</a>`
   - If message text is empty and attachments exist, render only the attachment row (no empty bubble).
2. Add minimal CSS for `.file-chip` and the attachment strip in the existing CSS file.

**File touch:** `ui/static/js/conversation/message.js` (edit), `ui/static/css/` (edit or new file)

### Step 10 — Operator documentation

1. Create `docs/OPERATOR_PASTE_ATTACH.md` with the three worked examples and reference info per §2.10.

**File touch:** `docs/OPERATOR_PASTE_ATTACH.md` (new)

### Step 11 — Full test run and evidence

1. Run the full test suite: `uv run pytest tests/ -v`
2. All existing tests must pass (no regressions).
3. All new tests must pass.
4. Record the output as evidence.

---

## 4. File Touch List

| File | Action |
|---|---|
| `db/schema.sql` | Edit — append `attachments` table + index |
| `core/models/attachment.py` | New — Pydantic model |
| `core/models/__init__.py` | Edit — export `Attachment` |
| `core/attachments.py` | New — storage path, sanitize, tier, blocklist logic |
| `db/attachments.py` | New — CRUD functions |
| `api/routes/attachments.py` | New — upload, download, preview endpoints |
| `api/routes/__init__.py` | Edit — register attachments router |
| `db/settings.py` or `main.py` | Edit — seed `bossmod.attach.max_size_mb` |
| Message send path (websocket or route) | Edit — accept `attachment_ids`, link, broadcast |
| `ui/static/js/conversation/composer.js` | Edit — paste handler, attach button, pending strip, new deps |
| `ui/static/js/conversation/conversation.js` (or `chrome.js`) | Edit — wire new composer deps |
| `ui/static/js/api-client.js` | Edit — add `uploadAttachment` |
| `ui/static/js/conversation/message.js` | Edit — render attachments in history |
| `ui/static/css/` (existing file) | Edit — file-chip and strip styles |
| `docs/OPERATOR_PASTE_ATTACH.md` | New — operator doc |
| `tests/test_attachments_core.py` | New |
| `tests/test_attachments_db.py` | New |
| `tests/test_attachments_api.py` | New |
| `tests/test_message_attachments.py` | New |

---

## 5. Test Plan

### 5.1 Unit tests (`tests/test_attachments_core.py`)

| Test name | Covers |
|---|---|
| `test_derive_storage_root_thread` | §2.4 thread path |
| `test_derive_storage_root_direct` | §2.4 direct path |
| `test_derive_storage_root_channel` | §2.4 channel path |
| `test_derive_storage_root_unscoped` | §2.4 unscoped path |
| `test_sanitize_file_name_strips_traversal` | `../../etc/passwd` → `etc_passwd` |
| `test_sanitize_file_name_strips_null` | `file\x00.txt` → `file.txt` |
| `test_sanitize_file_name_truncates` | 300-char name → 255 chars |
| `test_detect_preview_tier_image` | `.png`, `.jpg`, `.gif`, `.webp` → `image` |
| `test_detect_preview_tier_text` | `.txt`, `.log`, `.md`, `.json` → `text` |
| `test_detect_preview_tier_document` | `.pdf`, `.docx`, `.xlsx` → `document` |
| `test_detect_preview_tier_other` | `.bin`, `.dat` → `other` |
| `test_blocklist_rejects_exe` | `.exe` → True |
| `test_blocklist_rejects_all` | All 10 extensions → True |
| `test_blocklist_allows_common` | `.png`, `.txt`, `.pdf` → False |

### 5.2 DB tests (`tests/test_attachments_db.py`)

| Test name | Covers |
|---|---|
| `test_create_attachment` | Insert + return |
| `test_get_attachments_for_message` | Multiple rows, order |
| `test_get_attachment_by_id_missing` | Returns None |
| `test_delete_attachments_for_message` | Removes all |
| `test_fk_cascade_on_message_delete` | Deleting message removes attachments |

### 5.3 API integration tests (`tests/test_attachments_api.py`)

| Test name | Covers | AC |
|---|---|---|
| `test_upload_png_success` | 201, file on disk, DB row | AC-4 |
| `test_upload_exe_rejected` | 415 BLOCKLISTED | AC-10 |
| `test_upload_oversize_rejected` | 413 SIZE_EXCEEDED | AC-10 |
| `test_upload_bad_context` | 400 | — |
| `test_download_returns_file` | 200, content match | AC-8 |
| `test_download_missing_404` | 404 | — |
| `test_preview_non_image_204` | 204 | — |
| `test_preview_image_200` | 200, image content | — |

### 5.4 Message attachment tests (`tests/test_message_attachments.py`)

| Test name | Covers | AC |
|---|---|---|
| `test_send_with_two_attachments` | Both linked, broadcast correct | AC-5, AC-13 |
| `test_send_text_only_no_attachments` | Works as before | AC-12 |
| `test_send_with_nonexistent_attachment_id` | 400, no message | — |
| `test_send_with_six_attachments` | 400 TOO_MANY | AC-10 |
| `test_send_attachment_only_no_text` | Message created, no text error | AC-7 |

### 5.5 Edge cases (manual / QA checklist, not automated)

- Paste an image from clipboard → preview appears in < 500 ms.
- Paste mixed text + image → text goes to composer, image becomes attachment.
- Attach 5 files, then try to attach a 6th → error, composer still usable.
- Remove one of 3 pending attachments → only 2 sent.
- Send fails (simulate network drop) → pending attachments remain in composer.
- Restart app → attachments still visible and downloadable (AC-9).
- Attach a `.sh` file → rejected with visible error (AC-10).
- Attach a file that is deleted before send → 400 READ_FAILED on send (AC-11).
- View a `.py` script attachment in history → no auto-execution (AC-14).

---

## 6. Evidence the Build Crew Must Produce

1. **Test run output:** `uv run pytest tests/ -v` — full pass, no failures, no errors. Paste the tail of the output (summary line with counts).
2. **New test count:** Report the number of new tests added and their names.
3. **Diff scope:** `git diff --stat` showing files touched. Must match the File Touch List in §4 (no unexpected files).
4. **Manual smoke evidence:** A short note confirming the three operator-doc examples were exercised in the desktop app (screenshot or description).
5. **No regression:** All pre-existing tests pass. If any pre-existing test required modification, document why in the PR/commit message.

---

## 7. Constraints and Notes for the Build Crew

- **Do not** add a new external service, CDN, or cloud dependency.
- **Do not** implement drag-and-drop.
- **Do not** modify the `messages` table schema. Attachments live in their own table.
- **Do not** cache the size-cap setting. Read it from the DB at each upload.
- **Do not** trust client-side file size. The server re-checks `file_size` from the received blob.
- **Do not** store file content in the DB. Only metadata. The file lives on the local filesystem at `storage_path`.
- **Transaction safety:** Message insert + attachment link must be atomic. If the message insert succeeds but an attachment link fails, roll back both.
- **Path safety:** `storage_path` must always resolve inside the company data root. Never follow symlinks outside that root.
- **The `message_id = "pending"` pattern:** Uploads happen before the message exists. The attachment is created with a placeholder `message_id`. On send, it is updated. If the send is abandoned, the orphan attachment can be cleaned up (GC is out of scope for Phase 1; note it for Phase 2).

---

## 8. Open Items for the Build Crew to Resolve

These are implementation details the requirements intentionally left open. The build crew decides and documents in the commit:

1. **MIME detection:** Use `mimetypes.guess_type` stdlib or a small hand-rolled map. Either is fine; be consistent.
2. **Thumbnail generation:** Phase 1 can serve the original image as the "preview" (no resize). If performance is a concern for large images, add a resize step — but this is optional.
3. **File input accept attribute:** Use `accept` on the `<input type="file">` to hint the picker, but do NOT rely on it for validation (server-side blocklist is authoritative).
4. **Orphan attachment cleanup:** Out of scope for Phase 1. Add a TODO comment.

---

*End of spec. Handoff to Charles (Build Engineer). Cert against this spec + the 15 ACs in the requirements doc. Do not stamp your own spec.*
