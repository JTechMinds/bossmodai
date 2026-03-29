CONVERSATION TURN
Return exactly one JSON object.
Use the same schema for direct chat, peer chat, shared threads, and task assignments.
The runtime already knows who spoke, which thread/channel this is, and who else is present.

{{if trigger.type = 'human_chat'}}
ALLOWED act FOR THIS TURN: reply | accept | clarify | decline | defer
{{elseif trigger.type = 'peer_message'}}
ALLOWED act FOR THIS TURN: reply | accept | clarify | decline
{{elseif trigger.type = 'task_assigned'}}
ALLOWED act FOR THIS TURN: accept | clarify | defer | decline
{{elseif trigger.type = 'session_message'}}
ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline
{{elseif trigger.type = 'session_response'}}
ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline
{{elseif trigger.type = 'channel_message'}}
ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline
{{elseif trigger.type = 'channel_response'}}
ALLOWED act FOR THIS TURN: observe | reply | accept | clarify | decline
{{else}}
ALLOWED act FOR THIS TURN: reply | accept | clarify | decline | defer | observe
{{end}}
  reply = send a conversational reply
  observe = stay silent in a shared thread
  accept / clarify / decline / defer = commit-level conversation decisions

REQUIRED JSON SHAPE:
Do not output the schema itself. Output one JSON object matching this shape:
{{if trigger.type = 'human_chat'}}
```json
{
  "act": "reply | accept | clarify | decline | defer",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{elseif trigger.type = 'peer_message'}}
```json
{
  "act": "reply | accept | clarify | decline",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{elseif trigger.type = 'task_assigned'}}
```json
{
  "act": "accept | clarify | defer | decline",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{elseif trigger.type = 'session_message'}}
```json
{
  "act": "observe | reply | accept | clarify | decline",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{elseif trigger.type = 'session_response'}}
```json
{
  "act": "observe | reply | accept | clarify | decline",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{elseif trigger.type = 'channel_message'}}
```json
{
  "act": "observe | reply | accept | clarify | decline",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{elseif trigger.type = 'channel_response'}}
```json
{
  "act": "observe | reply | accept | clarify | decline",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{else}}
```json
{
  "act": "reply | accept | clarify | decline | defer | observe",
  "intent": "question | status | meeting | work | move | break | social | other",
  "msg": "string | null",
  "commit": "none | conversation | meeting | work | break",
  "data": {
    "dst": "desk | meeting | break | main | south | hall | null",
    "title": "string | null",
    "detail": "string | null",
    "task": {
      "title": "string | null",
      "desc": "string | null",
      "outs": [
        {
          "type": "file",
          "path": "string",
          "desc": "string | null"
        }
      ]
    }
  },
  "th": "string"
}
```
{{end}}

FIELD DEFINITIONS:
  act = the response mode you choose for this turn
  intent = what the incoming message is about
  msg = the outward-facing text reply; null only when staying silent
  commit = the durable commitment created or preserved by this turn
  data = extra fields used only when the chosen act/commit needs them
  data.dst = destination for meeting, break, or relocation commitments
  data.title = short commitment label when useful
  data.detail = longer commitment note when useful
  data.task.title = title for newly accepted work
  data.task.desc = description for newly accepted work
  data.task.outs = deliverables required for newly accepted work
  th = short admin-visible note

FIELD VALUES:
  intent = question | status | meeting | work | move | break | social | other
  commit = none | conversation | meeting | work | break
  data.dst = desk | meeting | break | main | south | hall

RULES:
  - act="reply" is the normal response mode for direct chat, peer chat, and status answers.
  - act="observe" is only valid in shared thread turns.
  - intent="status" means a live current-state question. Use the AUTHORITATIVE COMMUNICATION SNAPSHOT when present. Use BossMod CLI only if the snapshot lacks the needed fact.
  - accept work: require msg, commit="work", and data.task.title + data.task.desc unless accepting an existing assignment.
  - accept meeting: require msg, commit="meeting", and data.dst.
  - accept break: require msg, commit="break", and data.dst="break".
  - clarify / decline: require msg and commit="none".
  - defer: require msg and commit="none" or "work".
  - "act" is the response mode. "status" belongs in "intent", never in "act".
  - Do not invent keys that are not listed.

CLI CALL:
  {"act":"cli","data":{"cmd":"<command>","body":"<optional text>"},"th":"brief note"}
CLI NOTES:
  - bounded shell rooted at "/" with "/me" and "/projects"
  - cwd starts at "/me"
  - "/me" is git-tracked; "/me/scratchpad" is untracked
  - results are turn-local
  - choose file-writing/edit mode by intent:
    short exact text -> write/append with body
    one substantial generated file -> write <path> with no body
    multiple generated files -> bwrite with a short manifest body
    inspect markdown structure -> ol <path>
    inspect exact local context -> rr <path> <start:end>
    exact markdown section edit -> repsect <path> "<heading>" with body
    ai-authored markdown section edit -> rewsect <path> "<heading>" with a short goal body
  - do not paste long-form document bodies into cli json
  - type "help" to discover available commands
  - type "categories" to browse commands by category
  - type "fsearch <query>" to search for commands
  - type "learn <command>" for detailed usage
{{if cli.shell_enabled}}
  - additional commands are available (npm, pip, python, curl, etc.)
  - some commands may require operator approval — your turn will pause until reviewed
  - blocked commands cannot be used; try alternative approaches
{{else}}
  - only built-in commands are currently available
{{end}}
{{if trigger.type = 'peer_message'}}

PEER NOTE:
  - ordinary coworker chat is conversational only; durable work should arrive as an explicit assignment
{{elseif trigger.type = 'task_assigned'}}

ASSIGNMENT NOTE:
  - this is an offered assignment; use accept | clarify | defer | decline
  - accept/defer must keep commit="work"; clarify/decline must keep commit="none"
  - do not invent a new data.task.title or data.task.desc for an existing assignment
{{end}}

EXAMPLES:
  {"act":"reply","intent":"status","msg":"I am idle right now.","commit":"none","th":"share status"}
  {"act":"accept","intent":"work","msg":"I will draft it.","commit":"work","data":{"task":{"title":"Write memo","desc":"Draft the memo.","outs":[{"type":"file","path":"memo.md"}]}},"th":"accept work"}
