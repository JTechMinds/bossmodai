You are authoring section {{section_index}} of {{section_count}} for {{target_path}}.
Write only the body content for this section.
Do not include the heading "{{section.heading}}" in the response.
Do not include other sections.
Do not return JSON.
Do not add commentary or explanations.
Do not wrap the output in code fences.
Document outline:
{{outline}}
Current section heading: {{section.heading}}
Current section goal: {{section.goal}}
{{if batch.is_batch}}This file is {{batch.file_index}} of {{batch.file_count}} in a batch deliverable.
{{end}}{{if file_goal}}File goal: {{file_goal}}{{end}}
