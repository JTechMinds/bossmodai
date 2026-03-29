You are rewriting one existing markdown section in {{target_path}}.
Return only the rewritten body text for the target section.
Do not include the heading "{{section_heading}}" in the response.
Do not return JSON.
Do not add commentary or explanations.
Do not wrap the output in code fences.
Do not modify or mention other sections.
Document outline:
{{outline}}
Target section: {{section_heading}}
Rewrite goal: {{rewrite_goal}}
{{if previous_heading}}Previous section: {{previous_heading}}
{{end}}{{if next_heading}}Next section: {{next_heading}}
{{end}}{{if current_body}}Current section body:
{{current_body}}{{else}}Current section body: (empty){{end}}
