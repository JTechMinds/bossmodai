EXECUTION TURN
Return exactly one JSON object.
Use the same schema for all resumed/internal actions.

ALLOWED act VALUES:
  cli | work | socialmsg | taskmsg | assign | walk | mtg | idle | wait | done | block | deleg | drop
  cli=BossMod CLI, socialmsg=send social/non-task message, taskmsg=write on an existing task thread, assign=delegate task,
  mtg=join/start a meeting, done=finish the current commitment,
  block=report blocked state, deleg=report a handoff, drop=abandon the current commitment

REQUIRED JSON SHAPE:
- Do not output any schema, markdown fences, or extra text.
- Output exactly one JSON object with this minimal shape:
  {"act":"...","data":{...},"th":"..."}
- Include ONLY the keys required for the chosen act.

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
  - socialmsg: require data.to and data.msg; require data.aid only when data.to="agent"
  - taskmsg: require data.tid and data.msg; include data.kind so the runtime knows whether this is a passive update or a response request
  - assign: require data.aid plus data.task.title and data.task.desc; data.task.outs optional
  - walk: require data.dst
  - mtg: require data.mode; use mode="room" for in-person Meeting Room joins and mode="remote" for remote meetings
  - mtg + mode="remote": require data.aids (list of exactly one teammate); NEVER use data.aid
  - mtg + mode="room": if the goal is to run a meeting with teammates, include data.aids (list, even for one); NEVER use data.aid
  - idle: use when there is no active work and there is no useful next execution step in this turn
  - wait: require data.why; use it when the current task stays open but is waiting on another person, review, or external dependency
  - done: require data.sum; include data.msg when you should report completion back to the requester/owner now
  - block / drop: require data.why; include data.msg when you should report the problem back now
  - deleg: require data.aid; include data.msg when you should report the handoff back now
  - use socialmsg for ordinary coworker chat that does not create or continue task work
  - use assign to create new delegated work
  - use taskmsg to continue an existing task thread
  - when you use taskmsg, choose the kind that matches what you need:
    note = passive comment or acknowledgement
    status = progress or completion update that does not need a reply
    question = you need an answer back on the task
    review = you need the other person to review, approve, or make a decision
  - notes and status updates stay on the task thread without waking the other agent
  - questions and review requests ask the runtime to create one response-required task turn
  - during work execution, choose assign for new delegated work and taskmsg for an existing task thread; leave socialmsg for ordinary coworker chat
  - assign is for the accountable task owner/coordinator; if you are only the assignee on a delegated task, do not assign more work—use taskmsg (question/review) to the task owner instead
  - use the task id from Task Board when you continue an existing delegated task thread
  - before you use assign, scan Task Board for an existing child task (open or recently completed) that matches the same workstream; do not create duplicate delegated tasks
  - if you need more detail on a task thread first, inspect it with `task <id>`
  - if an open delegated child task owns the deliverable, keep the parent task on coordination/status work; do not finish the parent task until the child task is resolved
  - when a delegated child task reports completion or a blocker, do not re-delegate the same work; review the child update/deliverable, then either (a) continue the parent task, (b) delegate a new clearly different child task, or (c) complete the parent task and report back
  - for delegated work that someone else must review, save the deliverable under /projects/... (use /projects/shared/<task-id>/... when no project is set). Use /me only for private scratch drafts.
  - requester-facing progress updates belong in accept / reply / done / block / deleg / drop, not inside assign
  - if the current task stays open but is waiting on delegated work or another dependency, use wait instead of idle
  - idle is not valid while a task is still active
  - if work is location-bound, walk first and work second
  - if current deliverables require files, satisfy them with cli before done
  - human-requested or manager-requested tasks should usually include a short natural data.msg when you wait, finish, block, delegate, or abandon them
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
  {"act":"socialmsg","data":{"to":"human","msg":"Got it. I'll take a look and report back soon."},"th":"send a non-task social message"}
  {"act":"assign","data":{"aid":"agent-123","task":{"title":"Review API logs","desc":"Inspect failures and summarize the root cause."}},"th":"delegate follow-up"}
  {"act":"taskmsg","data":{"tid":"task-123","kind":"review","msg":"Please tighten the summary and send it back when ready."},"th":"continue the existing task thread"}
  {"act":"wait","data":{"why":"Waiting on Taylor's delegated findings before summarizing."},"th":"pause the task until Taylor reports back"}
  {"act":"mtg","data":{"mode":"room","aids":["agent-123","agent-456"],"topic":"Planning"},"th":"invite participants and join the meeting room"}
  {"act":"mtg","data":{"mode":"remote","aids":["agent-123"],"topic":"Quick sync"},"th":"start a remote meeting"}
  {"act":"done","data":{"sum":"Draft saved.","msg":"Finished the draft and saved it. Want a short summary too?"},"th":"complete and report back"}
