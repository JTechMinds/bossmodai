Action executed: {{detail}}.

You are in a meeting commitment. Continue in a way that is grounded in the current session state and produces outcomes.

Rules:
- A meeting is not progress. Do not claim “the meeting happened” or “they completed assignments” unless you have evidence in the task thread / meeting transcript / current participants list.
- Check the CURRENT MEETING SESSION participants in your snapshot:
  - If participants are only you (or none), you have not met with anyone yet. Invite participants first.
    - In-person: use `mtg` with `data.mode="room"` and include `data.aids` (list) to invite (even for one), and/or send a `socialmsg` asking them to join the Meeting Room.
    - Remote: from a workspace/desk, use `mtg` with `data.mode="remote"` + `data.aids` (list of exactly one).
- During the meeting: ask for status/blockers, turn decisions into explicit action items, and write any key outcomes back to the relevant task thread(s).
- End the meeting (use `idle`) only after decisions/action items are captured and the next steps are clear.
