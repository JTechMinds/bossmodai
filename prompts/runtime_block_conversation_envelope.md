CONVERSATION ENVELOPE:
current_agent: {{agent_name}}
speaker: {{conversation.speaker_name}} ({{conversation.speaker_type}})
speaker_id: {{conversation.speaker_id}}
channel_kind: {{conversation.channel_kind}}
channel_name: {{conversation.channel_name}}
turn_purpose: {{conversation.turn_purpose}}
audience_mode: {{conversation.audience_mode}}
{{if conversation.audience_targets}}
audience_targets: {{conversation.audience_targets}}
{{else}}
audience_targets: none
{{end}}
{{if conversation.participants}}
participants: {{conversation.participants}}
{{else}}
participants: none
{{end}}
Use this envelope to understand who is speaking, who else is present, and whether this is direct or shared conversation.
Do not restate these runtime facts unless they matter to your actual reply.
