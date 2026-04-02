Execution response format:
- Output MUST be a single JSON object.
- Required keys: "act", "th"
- Optional key: "data"
- Allowed "act" values: cli | work | socialmsg | taskmsg | assign | walk | mtg | idle | wait | done | block | deleg | drop
- Do not include any extra top-level keys besides: act, data, th
