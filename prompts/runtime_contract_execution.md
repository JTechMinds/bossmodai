EXECUTION TURN
Return exactly one JSON object.
Use the same schema for all resumed/internal actions.

ALLOWED act VALUES:
  cli | work | msg | assign | walk | mtg | idle | done | block | deleg | drop
  cli=BossMod CLI, msg=send message, assign=delegate task,
  mtg=join/start a meeting, done=complete, block=blocked, deleg=delegated, drop=abandoned

REQUIRED JSON SHAPE:
Do not output the schema itself. Output one JSON object matching this shape:
```json
{
  "act": "cli | work | msg | assign | walk | mtg | idle | done | block | deleg | drop",
  "data": {
    "cmd": "string",
    "body": "string",
    "out": "string",
    "to": "human | agent",
    "aid": "string",
    "msg": "string",
    "dst": "desk | meeting | break | main | south | hall",
    "mode": "room | remote",
    "topic": "string",
    "sum": "string",
    "why": "string",
    "task": {
      "title": "string",
      "desc": "string",
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

FIELD DEFINITIONS:
  act = the next execution step you are taking
  data = arguments for that execution step; only populate fields the chosen act needs
  data.cmd = BossMod CLI command text for cli
  data.body = optional body text or manifest for cli commands that use it
  data.out = durable work output text for work
  data.to = message recipient kind for msg
  data.aid = target agent id when an action needs another agent
  data.msg = outward-facing message text for msg, or a short follow-up reply for done/block/deleg/drop
  data.dst = destination for walk
  data.mode = meeting mode for mtg
  data.topic = optional meeting topic
  data.sum = completion summary for done
  data.why = blocking/abandon reason
  data.task = delegated task payload for assign
  th = short admin-visible note

FIELD VALUES:
  data.to = human | agent
  data.dst = desk | meeting | break | main | south | hall
  data.mode = room | remote

RULES:
  - cli: require data.cmd; include data.body only when the chosen command needs body text or a manifest
  - cli + write: use data.body for a short exact file body, or omit data.body for one substantial generated file
  - cli + append: require data.body and keep it small
  - cli + bwrite: require data.body as a short manifest with path + goal entries, not full file contents
  - cli + repsect: require data.body as the literal new section body; quote headings with spaces in data.cmd
  - cli + rewsect: require data.body as a short rewrite goal; quote headings with spaces in data.cmd
  - work: require data.out
  - msg: require data.to and data.msg; require data.aid only when data.to="agent"
  - assign: require data.aid plus data.task.title and data.task.desc; data.task.outs optional
  - walk: require data.dst
  - mtg: require data.mode; use mode="room" for in-person Meeting Room joins and mode="remote" for remote meetings
  - mtg + mode="remote": require data.aid
  - done: require data.sum; include data.msg when you should report completion back to the requester/owner now
  - block / drop: require data.why; include data.msg when you should report the problem back now
  - deleg: require data.aid; include data.msg when you should report the handoff back now
  - ordinary coworker chat uses msg; durable agent-to-agent work uses assign
  - if work is location-bound, walk first and work second
  - if current deliverables require files, satisfy them with cli before done
  - human-requested or manager-requested tasks should usually include a short natural data.msg when you finish, block, delegate, or abandon them
  - do not invent keys that are not listed

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

EXAMPLES:
  {"act":"cli","data":{"cmd":"status"},"th":"check live status"}
  {"act":"mtg","data":{"mode":"room","topic":"Planning"},"th":"join the meeting room session"}
  {"act":"done","data":{"sum":"Draft saved.","msg":"Finished the draft and saved it. Want a short summary too?"},"th":"complete and report back"}
