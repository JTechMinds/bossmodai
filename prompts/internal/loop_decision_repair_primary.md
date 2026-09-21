Your last response did not fit this conversation turn: {{parsed_error}}.

Return one JSON object, no fences/markdown.

Read the current turn instructions again and return that one corrected JSON object only.

Hard rules:
- Output must be valid JSON (double quotes), with no surrounding text.
- Do not include markdown, code fences, or explanations.
- Do not invent keys. Only include keys that the contract explicitly allows for this turn and this `act`.
- If the contract for this turn only allows `observe`, return the minimal `observe` object and omit `msg`, `commit`, and `data`.
- Do not invent Board status. Do not mark the task Done, Blocked, or CLEAR.
