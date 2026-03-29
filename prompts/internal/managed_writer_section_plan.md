You are planning a managed BossMod file for {{target_path}}.
Return strict JSON only.
Use this schema: {"sections":[{"heading":"# Heading","goal":"what this section must cover"}]}
Plan between 2 and {{max_sections}} sections.
Each heading must be the exact markdown heading that should appear in the final file.
Keep each goal concise and specific.
Do not include section body prose.
Do not wrap the JSON in code fences.
{{if batch.is_batch}}This file is {{batch.file_index}} of {{batch.file_count}} in a batch deliverable.
{{end}}{{if file_goal}}File goal: {{file_goal}}{{end}}
