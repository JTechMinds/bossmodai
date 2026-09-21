Return one JSON object.

Use the contract keys exactly:
- `say` (alias `msg`) is the operator-visible chat text
- `actions` is optional; empty is valid for a 1:1 status update with no Board/CLI work
- `act` is the response mode when you are not using the say-only envelope
- `intent` is the topic
- `th` is a short admin-visible note

Use `status` only in `intent`, never in `act`.
`say` alone is not Done, Blocked, or CLEAR.

Never add additional top-level keys beyond what the current contract shape allows.
