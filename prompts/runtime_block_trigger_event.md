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
{{elseif trigger.type = 'task_follow_up'}}
A task needs your response on "{{trigger.task_title}}".
Current task status: {{trigger.task_status}}
{{if trigger.task_description}}
Task description: {{trigger.task_description}}
{{end}}
{{if trigger.attention_kind = 'question'}}
Reason: the latest task-thread note asks you a direct question.
{{elseif trigger.attention_kind = 'review_request'}}
Reason: the latest task-thread note needs your review or decision.
{{elseif trigger.attention_kind = 'completion_report'}}
Reason: someone reported completion and you need to handle the next step.
{{elseif trigger.attention_kind = 'blocker'}}
Reason: someone reported a blocker and needs a decision or help.
{{elseif trigger.attention_kind = 'handoff'}}
Reason: someone reported a handoff and you need to decide what happens next.
{{elseif trigger.attention_kind = 'abandoned'}}
Reason: someone reported that the task was abandoned and you need to respond.
{{elseif trigger.attention_kind = 'clarification_requested'}}
Reason: the other person needs clarification from you before the task can move.
{{elseif trigger.attention_kind = 'decision_needed'}}
Reason: the task is waiting on your decision.
{{end}}
{{if trigger.content}}
Latest note from [{{trigger.from_name}}]: {{trigger.content}}
{{end}}
{{if trigger.task_party = 'assignee'}}
{{if trigger.task_status = 'pending'}}
This task already exists and is still waiting on your decision.
{{else}}
Respond within the existing task thread for this task.
{{end}}
{{else}}
Respond within the existing task thread for this task.
{{end}}
{{elseif trigger.type = 'task_assigned'}}
{{if turn.contract_kind = 'decision'}}
[{{trigger.from_name}}] assigned you a task: "{{trigger.task_title}}".
This task already exists on your task board.
Decide whether to accept it, ask a clarifying question, defer it, or decline it.
{{else}}
You have an accepted task commitment: "{{trigger.task_title}}".
{{end}}
{{if trigger.task_description}}
Task description: {{trigger.task_description}}
{{end}}
{{if trigger.content}}
Latest note from [{{trigger.from_name}}]: {{trigger.content}}
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
