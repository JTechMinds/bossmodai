{{if trigger.type = 'channel_message'}}
CURRENT SHARED CHANNEL MESSAGE FROM [{{trigger.from_name}}]: {{trigger.content}}
{{elseif trigger.type = 'channel_response'}}
YOUR TURN TO RESPOND IN THE SHARED CHANNEL after [{{trigger.from_name}}] said: {{trigger.content}}
{{elseif trigger.type = 'session_message'}}
CURRENT MEETING MESSAGE FROM [{{trigger.from_name}}]: {{trigger.content}}
{{elseif trigger.type = 'session_response'}}
YOUR TURN TO RESPOND IN THE MEETING after [{{trigger.from_name}}] said: {{trigger.content}}
{{elseif trigger.type = 'human_chat'}}
CURRENT REQUEST FROM [{{trigger.from_name}}]: {{trigger.content}}
{{elseif trigger.type = 'peer_message'}}
CURRENT REQUEST FROM [{{trigger.from_name}}]: {{trigger.content}}
{{elseif trigger.type = 'task_assigned'}}
{{if turn.contract_kind = 'decision'}}
[{{trigger.from_name}}] assigned you a task: "{{trigger.task_title}}". Decide whether to accept it, ask a clarifying question, defer it, or decline it.
{{else}}
You have an accepted task commitment: "{{trigger.task_title}}".
{{end}}
{{if trigger.task_description}}
Task description: {{trigger.task_description}}
{{end}}
{{elseif trigger.type = 'activity_resumed'}}
{{if trigger.content}}
{{trigger.content}}
{{else}}
You should continue the current {{trigger.activity_kind}}.
{{end}}
{{elseif trigger.type = 'social'}}
You're idle and nearby: {{trigger.nearby_names}}. Consider a brief social interaction.
{{elseif trigger.type = 'watchdog_status_ping'}}
System watchdog status check: you have been quiet on "{{trigger.task_title}}". Provide a concise current status update about the active task. After your reply, the runtime will resume the work turn automatically.
{{else}}
You have been activated.
{{end}}
