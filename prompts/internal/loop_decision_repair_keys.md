Return one JSON object, no fences/markdown.

Minimal allowed envelope for this turn (no Board change):
{"say":"string","actions":[],"work_commit":false}

Allowed keys only:
- `say` (alias `msg`) is the operator-visible chat text
- `actions` is optional; empty is valid for a 1:1 status update with no Board/CLI work
- `work_commit` is a required boolean on every reply (including the say-only envelope). `false` for status, questions, and reports. `true` only to continue work already active on your Board; to start new work, use act `accept` with commit `work` instead. It is not Done.
- `act` is the response mode when you are not using the say-only envelope
- `intent` is the topic
- `commit` is the commitment kind when the contract allows it
- `data` is the payload for that act
- `th` is a short admin-visible note

Use `status` only in `intent`, never in `act`.
`say` alone is not Done, Blocked, or CLEAR.
Do not invent Board status or a fake Done.

Never add additional top-level keys beyond what the current contract shape allows.
