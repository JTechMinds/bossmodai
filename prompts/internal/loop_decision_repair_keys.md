Return one JSON object, no fences/markdown.

Minimal allowed envelope for this turn (no Board change):
{"say":"string","actions":[]}

Allowed keys only:
- `say` (alias `msg`) is the operator-visible chat text
- `actions` is optional; empty is valid for a 1:1 status update with no Board/CLI work
- `work_commit` is an optional boolean. True only when `say` commits to doing the work on this turn. Omit it for status. It is not Done.
- `act` is the response mode when you are not using the say-only envelope
- `intent` is the topic
- `commit` is the commitment kind when the contract allows it
- `data` is the payload for that act
- `th` is a short admin-visible note

Use `status` only in `intent`, never in `act`.
`say` alone is not Done, Blocked, or CLEAR.
Do not invent Board status or a fake Done.

Never add additional top-level keys beyond what the current contract shape allows.
