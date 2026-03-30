# Prompt Logic Validation Matrix: Agent Interaction, Delegation, and Scheduled Work

## 1. Purpose
This document defines the scenario matrix that prompt logic should be validated against. The goal is to stop reviewing prompts abstractly and instead evaluate them against concrete runtime situations, expected model behavior, required side effects, and forbidden behavior.

This matrix is intentionally split between:

*   **Prompt/Test Coverage Gaps:** The platform already has the basic runtime capability, but the prompt instructions and/or automated validation are incomplete.
*   **Platform Capability Gaps:** The scenario cannot be solved by prompts alone because the runtime, scheduler, or tool surface does not yet exist.

## 2. Validation Rules
Every scenario should eventually be validated at three layers:

1.  **Prompt Contract Review:** Do the prompt instructions explicitly allow the correct behavior and forbid the wrong behavior?
2.  **Prompt Bundle Review:** Does the full prompt bundle for the scenario stay coherent when system prompt, runtime contract, snapshot, trigger block, and internal follow-up prompts are combined?
3.  **Runtime Acceptance Test:** Does the scenario produce the expected state changes, messages, tasks, artifacts, and follow-up triggers?

Each scenario should record:

*   `Trigger type`
*   `Initial state / preconditions`
*   `Expected model behavior`
*   `Whether CLI/tool lookup is allowed`
*   `Expected runtime side effects`
*   `Forbidden behavior`
*   `Status: Covered | Partial | Missing | Requires Platform Capability`

### 2.1 Prompt Instruction Quality Gates
Every prompt instruction should also pass these quality checks before it is treated as acceptable:

*   **Non-ambiguous:** The instruction should be specific enough that the agent does not have to guess what it means.
*   **Operational:** If the instruction implies a next step, it should point to a concrete action, path, or command pattern when one exists.
*   **Context-backed:** If the instruction depends on workspace or runtime state, the required path/folder/fact should be present in the rendered prompt context.
*   **No abstract placeholders without grounding:** Words like "relevant", "appropriate", or "correct location" are not sufficient unless the prompt also provides the actual candidate path or the rule for deriving it.
*   **Clarify instead of guess:** If a required path, project, or fact is not known, the instruction should explicitly allow inspection or clarification rather than interpretation by guesswork.
*   **Short and testable:** Instructions should stay concise and be worded so they can be validated by prompt-render tests or runtime acceptance tests.
*   **No duplicate vague restatements:** Avoid pairing one concrete instruction with a second, looser version of the same rule that reintroduces ambiguity.

## 3. Highest-Priority Missing Scenarios
These are the most important additions beyond the current prompt audit work.

### A. Manager Delegation Chain
**Target flow:** You talk to a PM/product manager agent. That agent delegates work to employees. Employees report back to the PM. The PM reports back to you.

**Why this matters:** This is the first truly hierarchical coordination loop. It tests direct human chat, durable task creation, AI-to-AI delegation, requester/owner semantics, follow-up routing, and summary synthesis across multiple agents.

**Current status:** `Partial`

**What exists now:**

*   Human-to-agent work requests are supported.
*   Agent-to-agent task delegation is supported.
*   Workers can send peer follow-ups.
*   Ownership/requester semantics already exist in the task model.

**What is still missing for confidence:**

*   Prompt scenarios for:
    *   PM decides to delegate rather than do the work directly
    *   PM reassigns if worker declines or blocks

**Automated happy-path coverage now exists for:**

*   `human -> PM -> worker -> PM -> human` via
    `test_run_turn_end_to_end_manager_delegation_chain_reports_back_to_human`
*   worker clarification routing to PM rather than the human
*   worker completion routing back to PM

### B. Scheduled / Recurring Work
**Examples:** Post to social media every morning, check email periodically, weekly scrum, periodic check-in with you.

**Why this matters:** These are not just normal tasks. They require durable recurrence rules, trigger generation over time, and usually some external integration/tool surface.

**Current status:** `Requires Platform Capability`

**What exists now:**

*   There is active-task watchdog behavior.
*   There are meetings, channels, and direct chat flows.

**What is missing at the platform layer:**

*   A recurring schedule model
*   A scheduler/trigger generator for future activations
*   A way to distinguish recurring commitments from one-off tasks
*   External tools/integrations for things like email or social posting

**Conclusion:** Do not treat recurring-task coverage as a prompt-only gap. Prompt logic can only be validated here after the runtime supports scheduling and the relevant tool actions.

## 4. Comprehensive Scenario Matrix

### 4.1 Direct Human Interaction
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| H-01 | User says hello | Agent replies naturally, no task, no lookup | No | Covered |
| H-02 | User asks a simple question already answered by snapshot/context | Agent replies directly from bounded context | Usually no | Covered |
| H-03 | User asks ambiguous question | Agent asks a clarifying question | Optional | Covered |
| H-04 | User asks for a work artifact (“write a paper/report/doc”) | Agent accepts, creates durable work commitment, replies naturally | Optional during decision; expected during execution | Covered |
| H-05 | User asks for move/meeting/break | Agent accepts or clarifies without inventing work | No unless fact lookup needed | Covered |
| H-06 | User asks for unsupported/out-of-scope action | Agent declines or clarifies cleanly | Optional | Covered |
| H-07 | User asks for a factual status and snapshot is insufficient | Agent uses one or more CLI lookups, then final conversation decision | Yes | Covered |
| H-08 | User asks for current status mid-task | Agent answers in chat, then work resumes | Optional | Covered |
| H-09 | User changes scope mid-task | Agent clarifies whether to replace the active commitment or continue current work | Optional | Covered |
| H-10 | User asks about recently completed work vs current active work | Agent distinguishes active work from historical completed work | Optional | Covered |
| H-11 | User asks for a short retrospective summary of prior completed work | Agent answers from recent completed-task / artifact context when the bounded summary is sufficient | Optional | Covered |
| H-12 | User asks a content question about a prior saved deliverable | Agent retrieves the relevant artifact/report before answering when the summary context is insufficient | Yes when retrieval is needed | Covered |
| H-13 | User asks about prior completed work while a current task is active | Agent answers about the prior work without dropping, replacing, or confusing the active task | Optional | Covered |

### 4.2 Human Work Lifecycle
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| W-01 | Human requests a task, agent accepts | Task is created, activity starts, chat reply is sent | Optional | Covered |
| W-02 | Agent works on a long file deliverable | Agent uses managed writer / `write <path>` flow | Yes | Covered |
| W-03 | Agent completes a human-requested task | Agent marks done and sends a requester-facing completion message | Optional on done; often yes during work | Covered |
| W-04 | Agent attempts done before required file deliverable exists | Runtime should block completion and direct agent to save file first | Yes | Covered |
| W-05 | Agent becomes blocked mid-task | Agent uses blocked path and reports blocker naturally | Optional | Covered |
| W-06 | Human asks status while task is active | Agent answers and work resumes without losing task | Optional | Covered |
| W-07 | Human requests revisions after completion | Agent creates or resumes follow-up work on the existing deliverable | Optional | Covered |
| W-08 | Human deprioritizes or replaces a task mid-flight | Agent pauses the older task and replaces it with the new active commitment correctly | Optional | Covered |
| W-09 | Human cancels an active task outright | Agent abandons or otherwise closes the current commitment correctly without inventing replacement work | Optional | Covered |

### 4.3 Decision-Turn CLI and Tool Logic
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| C-01 | No lookup needed | Agent does not call CLI unnecessarily | No | Covered |
| C-02 | One authoritative lookup needed | Agent calls `cli`, then final conversation decision | Yes | Covered |
| C-03 | Multiple lookups needed | Agent chains multiple `cli` lookups before final decision | Yes | Covered |
| C-04 | First lookup is only to discover CLI usage/command path | Agent may use CLI discovery before the actual fact lookup | Yes | Covered |
| C-05 | Decision turn ends on CLI instead of final answer | Forbidden | Yes but not terminal | Covered |
| C-06 | Snapshot already has answer but agent still reaches for CLI | Should be discouraged by prompt logic | Optional but should usually not happen | Covered |

### 4.4 AI-to-AI Conversation
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| A-01 | One agent greets another | Conversational peer reply only | No | Covered |
| A-02 | One agent asks another for status | Peer reply only, no durable work creation | Optional | Covered |
| A-03 | Peer requests durable work informally in chat | Agent does not create durable work from peer chat alone | Optional | Covered |
| A-04 | One agent asks another to meet/move | Agent accepts conversationally and moves/joins as appropriate | Optional | Covered |
| A-05 | One agent sends a direct follow-up after work step | Message routes to explicit target agent | No | Covered |

### 4.5 Delegation and Reporting Hierarchies
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| D-01 | Agent delegates a task to another agent | Child task created with correct requester/owner lineage | Optional | Covered |
| D-02 | Assignee accepts delegated work | Assignee accepts existing assignment without inventing new task metadata | No in decision; yes later in execution | Covered |
| D-03 | Assignee clarifies delegated work with delegator | Clarifying message goes to delegator, not to original human requester | No | Covered |
| D-03b | Delegator answers an assignment clarification | Follow-up returns to the assignee as assignment context, not generic coworker chat | No | Covered |
| D-04 | Assignee defers delegated work | Deferred assignment preserves durable work commitment | No | Covered |
| D-05 | Assignee declines delegated work | Decline is communicated clearly to delegator/owner | No | Covered |
| D-06 | Worker completes and reports back to delegator | Completion routes to delegator/owner appropriately | Optional | Covered |
| D-07 | PM delegates to worker and then summarizes back to human | PM acts as aggregation/reporting layer | Optional | Covered |
| D-08 | Worker blocks and PM reassigns to another worker | Escalation and reassignment loop works end-to-end | Optional | Covered |
| D-09 | Human asks a PM to get another employee moving on work | PM accepts ownership of the coordination task, delegates internally, and avoids relaying internal coordination caveats back to the human | Optional | Covered |

### 4.6 Shared Channels and Meetings
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| M-01 | Shared channel message that does not require response | Agent observes only | No | Covered |
| M-02 | Shared channel message requiring response | Agent replies in shared context | Optional | Covered |
| M-03 | Meeting response turn | Agent replies in meeting transcript correctly | Optional | Covered |
| M-04 | Meeting invitation interrupts work | Existing task pauses/replaces correctly and agent joins meeting | Optional | Covered |
| M-05 | Meeting produces follow-up work | Agent captures action item as durable work instead of treating meeting talk as completion | Optional | Covered |
| M-06 | Daily scrum / recurring meeting | Requires scheduled trigger generation | No special prompt issue until scheduler exists | Requires Platform Capability |

### 4.7 File Deliverables and Artifact Visibility
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| F-01 | Single markdown deliverable | Agent uses `write <path>` managed authoring | Yes | Covered |
| F-02 | Multiple deliverables | Agent uses `bwrite` or equivalent bounded multi-file flow | Yes | Covered |
| F-03 | Saved file becomes visible in Desk | Artifact is registered and browsable | Yes | Covered |
| F-04 | Human can read the saved document after completion | Desk/API returns file contents | Yes | Covered |
| F-05 | Agent claims done but file is missing | Completion should be blocked and prompt should redirect to file save | Yes | Covered |
| F-06 | Human asks for summary plus file | Agent saves file and optionally sends summary separately | Yes | Covered |

### 4.8 Observability and Status
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| O-01 | View agent current task | Current task is available through CLI/runtime status | Yes | Covered |
| O-02 | View recent work artifacts | Recent artifacts are visible in status surfaces | Yes | Covered |
| O-03 | Watchdog pings quiet active task | Task gets status ping trigger | No | Covered |
| O-04 | Agent answers watchdog with useful status and continues work | Status is visible and task remains active/resumes | Optional | Covered |
| O-05 | Human sees completion reply and artifact together | Completion message and artifact visibility are aligned | Optional | Covered |
| O-06 | Prompt bundle exposes authoritative current local date/time | Agent can ground replies about now/today/tomorrow without guessing the clock | No | Covered |

### 4.9 Scheduled / Recurring / Periodic Work
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| S-01 | Daily social media posting | Scheduler creates recurring work trigger, agent executes external posting flow | Requires social-posting integration | Requires Platform Capability |
| S-02 | Periodic email triage | Scheduler creates recurring work trigger, agent uses email tool/inbox integration | Requires email integration | Requires Platform Capability |
| S-03 | Weekly/monthly recurring report | Scheduler creates task instance, agent produces artifact, reports completion | Yes once scheduler exists | Requires Platform Capability |
| S-04 | Periodic check-in with you | Scheduler creates direct human-facing status/check-in prompt or task | No/Optional | Requires Platform Capability |
| S-05 | Recurring scrum meeting | Scheduler creates meeting/session trigger for participants | Meeting support exists; recurrence does not | Requires Platform Capability |

### 4.10 Negative and Safety Scenarios
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| N-01 | Prompt tells model to use legacy act names | Lint should fail | N/A | Covered |
| N-02 | Prompt tells model to emit `thought` instead of `th` | Lint should fail | N/A | Covered |
| N-03 | Peer chat creates durable work without explicit assignment | Runtime/prompt should reject | Optional | Covered |
| N-04 | Decision response missing required reply text | Validation fails and repairs or errors | No | Covered |
| N-05 | Agent overuses CLI when snapshot suffices | Prompt should discourage; tests should assert bounded lookup behavior | Optional | Covered |
| N-06 | Agent invents active or completed work not present in runtime/history | Agent answers only from known state or clarifies / checks instead of fabricating work state | Optional | Covered |
| N-07 | Agent invents artifact existence, file path, or save success | Agent verifies the artifact exists before claiming it was created, saved, or readable | Yes when verification is needed | Covered |
| N-08 | Agent answers document-detail questions without evidence | Agent uses injected summary when sufficient or inspects the document before making detailed claims | Yes when details require retrieval | Covered |
| N-09 | Agent invents coworker / delegated-task progress or results | Agent reports only known upstream/downstream state or checks first | Optional | Covered |
| N-10 | Agent claims it performed unsupported external actions | Agent declines or states capability limits instead of pretending it emailed, posted, transferred money, etc. | Optional | Covered |
| N-11 | Agent fabricates CLI/tool output or command success | Responses must reflect actual tool results; failed or absent tool results stay failed or absent | Yes | Covered |
| N-12 | Agent invents meetings, schedules, or check-ins that do not exist | Agent answers from actual runtime/scheduler state only | Optional | Covered |

### 4.11 Prompt Budget and Context Discipline
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| P-01 | Representative prompt bundles stay under a bounded instruction/context budget | Standard decision and execution turns remain under 3k prompt tokens in preview/test fixtures | No | Covered |
| P-02 | Dynamic prompt blocks only appear when relevant | Conversation snapshot/envelope/file guidance and similar blocks render conditionally instead of appearing on every turn | No | Covered |

### 4.12 Workspace Conventions and Save/Lookup Defaults
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| SP-01 | Shared project deliverable is created without an explicit path | Agent defaults to the relevant `/projects/...` location instead of saving in an arbitrary folder | Optional | Covered |
| SP-02 | User asks about project details | Agent reviews the relevant project folder before guessing from memory or unrelated context | Optional | Covered |
| SP-03 | Personal report or self-owned working document is created without an explicit path | Agent defaults to `/me/...` and keeps the output in an organized folder structure | Optional | Covered |
| SP-04 | Target save location is ambiguous | Agent clarifies or inspects the likely workspace structure before saving | Optional | Covered |
| SP-05 | Project already has an established folder structure | Agent follows the existing structure instead of inventing a new parallel layout | Optional | Covered |
| SP-06 | User explicitly specifies a save/read path | Agent follows the explicit path instead of applying a default preference | Optional | Covered |

### 4.13 Runtime Failure Recovery and Anti-Stall Behavior
| ID | Scenario | Expected Behavior | CLI/Tools | Status |
| --- | --- | --- | --- | --- |
| R-01 | A turn fails while the agent still has work in motion | Dispatcher retries the trigger automatically up to the configured retry limit instead of dead-stopping immediately | No | Covered |
| R-02 | Retries are exhausted for a work-related turn | Trigger is marked failed, the task is marked stalled, runtime activities are cancelled, and the human sees a visible stuck message | No | Covered |
| R-03 | Failure comes from an exception path rather than a normal failed turn outcome | The same dispatcher retry supervisor handles it instead of using a separate ad hoc path | No | Covered |

## 5. What Else Should Be Added Beyond Your Two Big Scenarios?
Yes. The other big missing areas are:

*   **Revision / rework loops**
    *   Human reviews a finished deliverable and asks for changes
    *   PM reviews a worker deliverable and sends revision instructions back down

*   **Blocked / escalation chains**
    *   Worker blocks and notifies PM
    *   PM resolves or escalates to human
    *   Human redirects work or approves scope change

*   **Priority replacement**
    *   Human interrupts an active task with a higher-priority task
    *   PM interrupts a worker with a new critical assignment
    *   Previous work is paused/replaced cleanly

*   **Completion synthesis**
    *   Worker finishes task and gives PM raw result
    *   PM produces a human-ready summary instead of forwarding raw worker output unchanged

*   **Hallucination / evidence discipline**
    *   Agent does not invent tasks, artifacts, or teammate status
    *   Agent does not claim to have read or saved documents it did not actually inspect or create
    *   Agent does not pretend unsupported actions or integrations succeeded

*   **External-action realism**
    *   Anything like email, social posting, ticket updates, CRM notes, or calendar operations must be treated as tool/integration scenarios, not just prompt scenarios

## 6. Must-Pass Release Gate
Before calling the current prompt/runtime interaction model “ready,” the following suite should pass.

### 6.1 Manual Smoke Suite
These are the fastest high-signal end-to-end checks to run in the product.

| Smoke ID | Scenario IDs | Manual Flow | Pass Criteria | Current Evidence |
| --- | --- | --- | --- | --- |
| SM-01 | H-01, H-02 | Say hello to an idle agent and ask a simple direct question | Agent replies naturally, no accidental task creation, no strange lookup behavior | Direct human-chat lane covered by `test_run_turn_human_chat_chat_lane_uses_standard_decision_turn` and grounded-status tests |
| SM-02 | H-04, W-01 | Ask an agent to write a paper | Agent accepts, a task is created, work becomes active, reply is human-readable | `test_run_turn_human_chat_work_request_creates_task_and_accepts_assignment` |
| SM-03 | H-08, W-06 | While that task is active, ask “what’s the status?” | Agent answers in chat, then work resumes instead of losing the active task | `test_run_turn_status_reply_schedules_activity_resume_for_active_work` |
| SM-04 | O-01 | Check the agent’s current task from the product/runtime tools | Current task view matches the active work item | `test_execute_bm_cli_exposes_expanded_read_commands` |
| SM-05 | W-02, F-01, F-03, F-04, F-05 | Let the agent finish a file-backed document task and then open the document | File is saved, visible in Desk, readable in Desk, and tied to the task/artifact record; premature `done` is corrected in-turn | `test_work_completion_requires_requested_saved_file`, `test_activity_resumed_managed_writer_saves_long_file_and_commits_once`, `test_bm_cli_write_registers_artifact_and_desk_view_can_open_it` |
| SM-06 | W-03, O-05 | Confirm the agent sends a completion follow-up automatically to the human requester | Human sees a natural completion message and can access the deliverable | `test_work_completion_requires_requested_saved_file`, `test_complete_action_can_reply_to_human_requester_when_follow_up_message_is_provided` |
| SM-07 | A-02, A-05 | Have one agent message another directly | Target agent receives and answers peer communication cleanly | `test_run_turn_peer_message_grounded_question_uses_shared_communication_lane`, `test_message_action_routes_to_agent_by_explicit_id` |
| SM-08 | D-01, D-02, D-03, D-06, D-07 | Ask a PM/product-manager agent for work that should be delegated to a worker and reported back up | PM delegates correctly, worker accepts/clarifies/completes, PM reports back to human | `test_run_turn_end_to_end_manager_delegation_chain_reports_back_to_human` |

Automated gate command: [scripts/run_runtime_smoke_suite.sh](/home/jordan/Desktop/Projects/bossmodai/scripts/run_runtime_smoke_suite.sh)

### 6.2 Automated Gate
These automated checks should pass in CI before treating the prompt layer as validated for current features.

1.  Prompt-contract and bundle-consistency suite
    *   Runtime contract rendering tests
    *   Prompt health lint tests
    *   Prompt example round-trip parsing tests
    *   Representative prompt bundles stay under the instruction/context budget
    *   Dynamic prompt blocks render only when relevant to the turn

2.  Core interaction suite
    *   human work request acceptance
    *   mid-task status reply + resume
    *   peer message reply
    *   file save + Desk visibility
    *   completion follow-up to human requester
    *   system prompt renders current local date/time variables

3.  Delegation suite
    *   task delegation record creation
    *   assignee clarification routing
    *   owner/requester lineage preservation
    *   end-to-end delegator handoff and worker report-back
    *   PM aggregation/report-back to the human requester

## 7. Ownership Lanes
To make the backlog actionable, scenarios should be assigned to lanes rather than left as generic “missing.”

*   **Prompting/Contracts**
    *   owns runtime contract wording, prompt bundle coherence, prompt linting, and decision/execution turn semantics
*   **Agent Runtime**
    *   owns task/activity lifecycle, follow-up routing, delegation semantics, interruption rules, watchdog behavior
*   **Desk/UI**
    *   owns task visibility, document visibility, transcript visibility, operator-facing preview/diagnostic surfaces
*   **Scheduler/Integrations**
    *   owns recurring schedules, future trigger generation, email/social/calendar/inbox integrations
*   **QA/Acceptance**
    *   owns smoke suite execution, matrix status updates, and release-go/no-go recommendation

## 8. Prioritized Automation Backlog
This is the recommended implementation order for turning the matrix into executable coverage.

### 8.1 P0: Blockers Before Declaring Interaction Flows Ready
| Priority | Scenario IDs | Missing Test / Validation | Why It Matters | Owner Lane |
| --- | --- | --- | --- | --- |
| P0 | None currently open | Core interaction P0 runtime gaps are covered by executable tests | Keep the release gate focused on manual smoke + next P1 workflows | Agent Runtime + QA/Acceptance |

### 8.2 P1: High-Value Workflow Coverage After P0
| Priority | Scenario IDs | Missing Test / Validation | Why It Matters | Owner Lane |
| --- | --- | --- | --- | --- |
| P1 | None currently open | Focused workspace-convention gaps are covered; the next work is broader release confidence rather than another single workflow slice | Shifts effort from targeted behavior gaps to broader validation and release confidence | Prompting/Contracts + Agent Runtime + QA/Acceptance |

### 8.3 P2: Valuable But Not Immediate Release Blockers
| Priority | Scenario IDs | Missing Test / Validation | Why It Matters | Owner Lane |
| --- | --- | --- | --- | --- |
| P2 | None currently open | Focused scenario-matrix gaps are covered; the next work is broader release confidence and future platform epics | Shift from focused gap-closing to broader validation | Agent Runtime + Prompting/Contracts + QA/Acceptance |

## 9. Scheduled / Recurring Work Epic
Recurring tasks should be tracked as a separate product/runtime epic rather than mixed into prompt-only acceptance work.

### 9.1 Required Platform Additions
*   recurrence model for tasks/check-ins/meetings
*   trigger generator for future activations
*   scheduler state and retry rules
*   distinction between one-off tasks and recurring templates
*   external integration surfaces for email/social/calendar where applicable

### 9.2 Prompt Validation Work That Follows Later
Once scheduling exists, add prompt scenarios for:

*   agent recognizes the work as recurring rather than ad hoc
*   agent reports completion of one occurrence without marking the recurring program complete forever
*   agent escalates missed/blocked recurring work correctly
*   periodic human check-in messages stay conversational and concise

## 10. Immediate Execution Plan
Recommended next actions in order:

1.  Re-check broader release confidence now that the focused matrix, including anti-hallucination coverage, is in place.
2.  Decide whether to add a broader regression suite or a manual smoke gate for the covered scenarios.
3.  Keep scheduled / recurring work in its separate platform epic.

## 11. Bottom Line
The matrix is now actionable:

*   it defines the release-gate smoke suite
*   it assigns missing work to owner lanes
*   it separates prompt/test gaps from true platform capability gaps

The next concrete checkpoint should be:

*   **P0 scenarios all have executable tests**
*   **SM-01 through SM-08 are manually run and signed off**
*   **scheduled/recurring work is tracked as a separate platform epic, not hidden inside prompt QA**
