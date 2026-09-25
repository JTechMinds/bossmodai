# Requirements: Headless Frame-Dump + Image-Inspect for BossMod Toolkit

**Status:** Draft for lock  
**Owner:** Brad — Game Designer / Requirements Analyst  
**Project:** `bossmodai`  
**Artifact:** `/projects/bossmodai/docs/requirements-frame-dump.md`  
**Intended handoff:** Producer / Feature Planner after open questions are resolved

## 1. Problem statement

BossMod has a desktop UI that shows agents, tasks, world state, chat, and runtime activity. Visual and UI-state issues are currently hard to verify without a human opening the desktop app and watching the screen.

The team needs a repeatable way to:

- Run BossMod UI-related scenarios without a visible desktop window.
- Capture rendered UI frames as inspectable image artifacts.
- Inspect those frames for expected visual or textual evidence.
- Use the result in local verification, QA, and regression checks.

This requirement is about the capability and acceptance bar, not the implementation design.

## 2. Context

Current BossMod architecture includes:

- A Tauri desktop shell.
- A FastAPI backend process.
- A runtime worker process.
- A vanilla JS UI under `ui/`.
- SQLite-backed runtime state.
- Existing test and documentation workflows under `tests/` and `docs/`.

The UI is not a static page. It depends on runtime state, WebSocket events, agent activity, task state, and world simulation. Any frame-dump capability must therefore be able to run against a realistic BossMod runtime state, not only a static mock page.

## 3. Scope in

This work is in scope when it delivers:

1. **Headless UI execution**
   - A way to run BossMod UI scenarios without requiring a human-visible desktop window.
   - The scenario must be able to reach a meaningful UI state before capture.

2. **Frame capture**
   - A way to capture one or more rendered UI frames as image artifacts.
   - Captured frames must be saved to a named, inspectable path.
   - Captured frames must be attributable to the scenario that produced them.

3. **Image inspection**
   - A way to inspect captured frames for expected evidence.
   - Inspection must produce a machine-readable pass/fail result.
   - Failure output must identify the expected evidence and the captured artifact path.

4. **Reproducibility**
   - The same scenario must be runnable repeatedly.
   - Re-running the same scenario should produce comparable artifacts or comparable inspection results.

5. **Documentation**
   - Operators and teammates must be able to run the capability from a documented command or script.
   - The documentation must show at least one successful example and one intentional failure example.

6. **Test evidence**
   - At least one automated test or verification script must prove that:
     - a frame is captured,
     - the image artifact exists,
     - an image inspection check can pass,
     - an image inspection check can fail with a useful diagnostic.

## 4. Scope out

The following are out of scope for this requirement:

- Redesigning the BossMod UI.
- Replacing Tauri with another desktop framework.
- Building a full visual-regression platform.
- Cross-machine CI integration.
- Pixel-perfect visual diffing as the primary acceptance mechanism.
- Automated gameplay or agent-behavior verification beyond the UI frame evidence.
- Production monitoring or alerting.
- General screenshot tooling unrelated to BossMod UI verification.
- Level layout, milestone planning, TDDs, implementation code, or cert verdicts.

## 5. Acceptance criteria

Each criterion must be individually checkable.

### AC-1: Headless run does not require a visible desktop window

Given a documented headless scenario command, the command completes without requiring a human to interact with a visible BossMod desktop window.

Check:
- Run the documented command in a local environment.
- Confirm the process reaches completion.
- Confirm no manual desktop interaction is required.

### AC-2: A captured frame is saved to a named artifact path

Given a scenario that requests a frame dump, the process writes an image file to a named path.

Check:
- Run the scenario.
- Confirm the image file exists at the expected path.
- Confirm the file is a valid image file.

### AC-3: The captured frame is attributable to the scenario

Each captured frame must be associated with the scenario that produced it.

Check:
- The artifact path or accompanying metadata includes the scenario name or identifier.
- Two different scenarios do not overwrite each other’s artifacts in an ambiguous way.

### AC-4: The scenario reaches a meaningful UI state before capture

The captured frame must show a BossMod UI state relevant to the scenario, not only a blank page or loading state.

Check:
- The captured frame contains at least one expected UI element, label, panel, or state indicator defined by the scenario.
- The frame is not blank, all-white, all-black, or an error page unless the scenario explicitly expects that state.

### AC-5: Image inspection can verify expected text or UI evidence

The image-inspection capability can check whether a captured frame contains expected evidence.

Check:
- Run an inspection with expected evidence present.
- The result is pass.
- Run an inspection with expected evidence absent.
- The result is fail.

### AC-6: Inspection failure is actionable

When an image inspection fails, the output must help a teammate identify the problem.

Check:
- The failure output includes:
  - the expected evidence,
  - the inspected artifact path,
  - the scenario name or identifier,
  - a short reason for failure.

### AC-7: The capability can be run by an operator or teammate

A non-author teammate can run the capability using only the project documentation.

Check:
- A teammate follows the documented steps.
- The teammate obtains a captured frame and an inspection result.
- No undocumented environment step is required.

### AC-8: The capability produces test evidence

At least one automated test or verification script exercises the frame-dump and image-inspect flow.

Check:
- The test or script runs locally.
- It produces a captured frame artifact.
- It asserts that the artifact exists.
- It asserts that at least one inspection check passes.
- It demonstrates at least one intentional failure case with a useful diagnostic.

### AC-9: The documentation is committed

The required documentation is committed in the project.

Check:
- A documentation file or section exists under the project docs.
- It includes:
  - purpose,
  - prerequisites,
  - example command,
  - artifact location,
  - pass example,
  - fail example.

## 6. Constraints

- The capability must work with the existing BossMod project layout.
- The capability must not require a full host mount.
- Artifacts must stay inside allowed project or host workspace paths.
- The capability should not require a human to watch the UI.
- The capability should not depend on a specific developer’s machine-specific paths.
- The capability should be usable for local verification before broader CI work.
- The capability must not make visual correctness claims stronger than the evidence supports.
- Pixel-perfect equality is not required unless a future requirement explicitly locks it.

## 7. Non-goals

- This requirement does not define the exact implementation.
- This requirement does not choose the rendering engine, screenshot library, OCR tool, or visual-diff tool.
- This requirement does not define the full test matrix.
- This requirement does not define performance budgets beyond “repeatable local run.”
- This requirement does not define visual design improvements.
- This requirement does not define production observability.

## 8. Open questions

These questions must be resolved before the design bible is fully locked.

1. **Headless surface**
   - Should the headless capture use the existing Tauri UI, a webview-based render of the same UI, or a test-only UI harness?

2. **Runtime state source**
   - Should frame-dump scenarios run against:
     - a real local BossMod runtime,
     - a seeded test runtime,
     - a mocked API/WebSocket runtime,
     - or all of the above depending on scenario type?

3. **Inspection fidelity**
   - What is the minimum inspection fidelity for v1?
     - Text/label presence only?
     - Region-based visual presence?
     - OCR-based text detection?
     - Pixel-diff comparison?

4. **Artifact storage**
   - Where should frame artifacts be stored by default?
     - Under `/projects/bossmodai/artifacts/`?
     - Under a test output directory?
     - Under a task-specific shared path?

5. **Dependency policy**
   - Are new Python or JS dependencies allowed for screenshot capture and image inspection?
   - Are system-level browser or webview dependencies acceptable?

6. **Determinism bar**
   - What is the required determinism bar?
     - Same pass/fail result?
     - Same artifact filename?
     - Similar visual content?
     - Exact pixel match?

7. **Failure classification**
   - Should a failed image inspection be treated as:
     - a test failure,
     - a QA diagnostic,
     - or both?

## 9. Done bar for this requirement

This requirement is done when:

- This document is committed at `/projects/bossmodai/docs/requirements-frame-dump.md`.
- The scope in/out list is locked.
- The acceptance criteria are individually checkable.
- Open questions are either resolved or explicitly accepted by the operator.
- The document is handed off to the Producer / Feature Planner for sequencing.
