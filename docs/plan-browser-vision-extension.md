# Browser-Vision Extension — M1 Milestone Plan

Project: `browser-vision-extension`
Plan owner: Harley (Feature Planner)
Status: active planning artifact
Scope source: operator + Brad locked scope
Next pipeline step: Brian (Tech Spec Author)

## 1. M1 Goal

Deliver a self-contained browser-vision extension POC that proves the agent can:

- load as a BossMod extension,
- register four tools with the BM host,
- capture a headless-browser view,
- use a coordinate-grid protocol with precision tiers,
- perform `click`, `type`, and `scroll` actions,
- take a pre-click re-screenshot before every click,
- appear in the install surface: `Add Agent / Agent Marketplace / Extensions (flat dropdown, three items)`.

M1 is a proof-of-concept slice. It is not a production security model and not a full desktop experience.

## 2. Locked Scope

In scope for M1:

- Plugin/extension architecture, not core BM feature work.
- Extension exposes four tools:
  - `view`
  - `click`
  - `type`
  - `scroll`
- BM host exposes:
  - extension load,
  - lifecycle hooks,
  - tool registration.
- Coordinate-grid protocol with precision tiers.
- Pre-click re-screenshot step.
- Install surface entry under `Add Agent / Agent Marketplace / Extensions (flat dropdown, three items)`.
- Headless-browser capture and action execution.

Out of scope for M1:

- M2: Xvfb desktop swap.
- M3: security model.
- Production hardening.
- Full marketplace management.
- Real desktop GUI validation.
- Inventing new acceptance criteria or changing the locked bar.

## 3. Build Order

| Step | Name | Goal | Depends On | Suggested Owner | Support |
|---|---|---|---|---|---|
| M1.1 | Extension scaffold and host contract | Create a loadable extension package with lifecycle hooks and tool-registration skeleton. | None | Charles (Build Engineer) | Brian (Tech Spec Author) |
| M1.2 | Coordinate-grid protocol baseline | Implement the locked coordinate-grid protocol and precision-tier metadata in tool payloads. | M1.1 | Brian (Tech Spec Author) | Charles (Build Engineer) |
| M1.3 | `view` tool and headless capture | Make `view` return a headless-browser screenshot plus coordinate-grid metadata. | M1.2 | Charles (Build Engineer) | Brian (Tech Spec Author) |
| M1.4 | Action tools with pre-click re-screenshot | Implement `click`, `type`, and `scroll`; require a fresh screenshot before every click. | M1.3 | Charles (Build Engineer) | Brian (Tech Spec Author) |
| M1.5 | Install surface entry | Make the extension discoverable under `Add Agent / Agent Marketplace / Extensions (flat dropdown, three items)`. | M1.1 | Charles (Build Engineer) | Brad (Game Designer / Requirements Analyst) |
| M1.6 | M1 evidence package and gate review | Collect M1 evidence and prepare the M1 exit gate. | M1.4, M1.5 | Sarah (QA Cert Lead) | Harley (Feature Planner) |

## 4. Dependency Chain

```text
M1.1 Extension scaffold
  -> M1.2 Coordinate-grid protocol
      -> M1.3 view + headless capture
          -> M1.4 click/type/scroll + pre-click re-screenshot
              -> M1.6 evidence package

M1.1 Extension scaffold
  -> M1.5 install surface entry
      -> M1.6 evidence package
```

M1.5 may run in parallel with M1.2 through M1.4 once M1.1 is stable.

## 5. Evidence Checkpoints and Gates

### G1 — Host + Capture Vertical Slice

Pipeline pause point after M1.3.

Goal: prove the extension is real enough to load, register, and capture.

Evidence needed:

- BM host loads the extension.
- Extension lifecycle starts and stops cleanly.
- `view`, `click`, `type`, and `scroll` are registered with the host.
- `view` returns a headless-browser screenshot.
- Screenshot includes coordinate-grid metadata using the locked precision tiers.
- Test output or artifact path is available for review.

Gate owner: Harley (Feature Planner)
Evidence reviewer: Sarah (QA Cert Lead)

Green bar:

- All G1 evidence items exist and are openable.
- No M2/M3 work is required to make G1 pass.

Red bar:

- Extension cannot load.
- Tool registration fails.
- `view` cannot produce a screenshot with coordinate-grid metadata.
- The slice requires Xvfb desktop or security-model work to pass.

### G2 — Action Vertical Slice

Pipeline pause point after M1.4.

Goal: prove the agent can act in the headless browser using the protocol.

Evidence needed:

- `click` executes against a coordinate-grid target.
- `type` executes into a target field.
- `scroll` changes the visible browser state.
- A fresh screenshot is captured immediately before every click.
- Action log shows the pre-click re-screenshot step.
- Test output or artifact path is available for review.

Gate owner: Harley (Feature Planner)
Evidence reviewer: Sarah (QA Cert Lead)

Green bar:

- All three action tools work in the headless POC.
- Pre-click re-screenshot is demonstrably present for click actions.
- Coordinate-grid precision tiers are visible in the evidence.

Red bar:

- Any action tool cannot execute in the headless POC.
- Click can occur without the pre-click re-screenshot step.
- Evidence relies on manual inspection with no artifact or test output.

### G3 — Install Surface

Pipeline pause point after M1.5.

Goal: prove the extension is discoverable through the required install surface.

Evidence needed:

- Extension appears under `Add Agent / Agent Marketplace / Extensions (flat dropdown, three items)`.
- Screenshot, CLI output, or artifact path proves the entry exists.
- Entry name and scope are clear enough for an operator to identify the browser-vision extension.

Gate owner: Harley (Feature Planner)
Evidence reviewer: Brad (Game Designer / Requirements Analyst)

Green bar:

- The extension is visible in the required install surface.
- Evidence is openable and unambiguous.

Red bar:

- Extension is missing from the required surface.
- Evidence is only a chat claim with no artifact.
- Install surface work expands into full marketplace scope.

### G4 — M1 Exit

Final M1 gate after G1, G2, and G3.

Goal: confirm the POC is complete enough to hand off to M2 planning.

Evidence needed:

- G1, G2, and G3 are all green.
- M1 evidence package is saved under the project.
- No M2 or M3 work has been pulled into M1.
- Handoff note to M2 is ready.

Gate owner: Harley (Feature Planner)
Evidence reviewer: Sarah (QA Cert Lead)

Green bar:

- M1 POC evidence is complete and openable.
- M2 can begin from a stable M1 baseline.

Red bar:

- Any M1 gate remains red.
- M1 evidence depends on future M2/M3 work.
- The POC cannot be reopened from saved artifacts.

## 6. Risk Callouts

### Coordinate precision risk

Coordinate-grid targets may be ambiguous if precision tiers are mismatched between screenshot metadata and action payloads.

Mitigation:

- Lock protocol metadata early in M1.2.
- Use deterministic test pages with known targets.
- Require pre-click re-screenshot before every click.
- Keep precision-tier values in the locked design; do not invent new tiers in M1.

### Headless-browser timing risk

Screenshots or actions may run before the page state is stable.

Mitigation:

- Use deterministic POC pages.
- Keep timing behavior inside the M1 TDD, not the plan.
- Capture action logs so timing failures can be reviewed.

### Host contract drift risk

The extension may assume BM host behavior that is not actually exposed.

Mitigation:

- Validate load, lifecycle, and tool registration in M1.1.
- Keep host changes limited to the locked extension contract.
- Stop at G1 if the host contract is not sufficient.

### Install surface scope creep risk

The marketplace surface may expand beyond M1 discoverability.

Mitigation:

- M1 only proves the extension appears under `Add Agent / Agent Marketplace / Extensions (flat dropdown, three items)`.
- Full marketplace management is out of scope.

### Security scope risk

M1 is a POC and does not include the M3 security model.

Mitigation:

- Keep M1 local/headless and self-contained.
- Do not treat M1 evidence as production security proof.
- Defer security-model work to M3.

### Desktop scope risk

Xvfb desktop swap may be pulled into M1 accidentally.

Mitigation:

- M1 uses headless-browser capture only.
- Xvfb desktop swap is M2.
- Any need for real desktop validation is a red gate, not an M1 expansion.

## 7. Suggested Ownership Summary

- Harley (Feature Planner): milestone sequencing, gate ownership, scope protection, handoff coordination.
- Brad (Game Designer / Requirements Analyst): locked-scope confirmation, install-surface clarity, scope-creep review.
- Brian (Tech Spec Author): translate M1 steps into junior-executable TDDs, especially protocol and tool behavior.
- Charles (Build Engineer): extension scaffold, host integration, headless-browser tools, install-surface entry.
- Sarah (QA Cert Lead): evidence collection, audit readiness, gate evidence review.
- Jimothy (Gameplay Engineer): available support if interaction-feel or action-loop issues appear, but not the primary M1 owner.

## 8. Handoff

Next pipeline owner: Brian (Tech Spec Author)

Brian should translate the M1 steps into junior-executable TDDs.

Recommended TDD order:

1. M1.1 Extension scaffold and host contract.
2. M1.2 Coordinate-grid protocol baseline.
3. M1.3 `view` tool and headless capture.
4. M1.4 Action tools with pre-click re-screenshot.
5. M1.5 Install surface entry.
6. M1.6 Evidence package and gate review.

The locked bar should not be re-litigated during TDD authoring. If a TDD reveals that the locked scope cannot be met, stop and return to Harley and Brad for a scope decision.
