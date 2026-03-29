FILE DELIVERABLE GUIDANCE:
required_files: {{file_guidance.required_files}}
required_file_count: {{file_guidance.required_file_count}}
If the current work contract requires a file, prefer BossMod CLI write directly instead of putting the full document into data.out.
For one substantial document, call write <path> with no body to use runtime-managed authoring.
For multiple generated files, call bwrite with a short manifest body listing each path and goal.
Do not put long-form document bodies into CLI JSON.
Use work.out for short progress/status text, not the final long-form file body.
