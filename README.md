# BossMod AI

BossMod AI is a local-first desktop app for running a team of AI agents inside a visual 2D office.

Instead of chatting with one assistant in one window, you can create multiple agents, give them roles, watch them move around a shared office, assign work, inspect their desks, and tune how they think and respond in real time.

This project is intended to run on your own machine. It is not a hosted SaaS product.

BossMod AI is created and maintained by Jordan Gonzales of JtechMinds LLC.

## What It Feels Like

BossMod is built for people who want AI coworkers, not just AI chats.

With BossMod you can:

- Create multiple agents with different roles and personalities
- Watch them move through a shared office map
- Give them tasks and see how they respond
- Edit system prompts and runtime contracts live in the app
- Test prompts, change them, and test again without restarting
- Inspect diagnostics when an agent makes a bad decision
- Emergency-pause the whole runtime if things start going sideways

## How It Runs

BossMod runs locally on your computer as a desktop app.

At startup, the Tauri shell launches a local Python backend and opens the desktop UI. The app stores its local state in a DuckDB database on your machine.

In plain English:

- The app runs on your computer
- Your settings and agent state are stored locally
- If you connect a cloud model provider, prompt/response traffic goes to that provider
- If you use a local model endpoint, you can keep the whole workflow local

## Who This Is For

BossMod is a good fit if you want to:

- Experiment with multi-agent workflows
- Build a local AI operations desk on your own computer
- Tune prompts and runtime behavior visually instead of editing source code
- Use cloud or local models behind a desktop-first interface

It is probably not the right tool if you want:

- A managed hosted product
- A polished enterprise admin console
- A hardened multi-user server deployment

## Privacy And Safety

BossMod is designed as a local desktop app.

Important expectations:

- API keys are stored locally on your machine
- The app persists prompts, diagnostics, and runtime state locally
- Detailed diagnostics can include rendered prompts and model outputs
- The local backend is meant to stay on the same machine as the desktop app
- Do not expose the backend to your LAN or the public internet unless you know exactly what you are doing

If you use a remote model provider, that provider will receive the prompts and context you send through it.

## Current Status

BossMod is usable, but still early.

What is already here:

- Desktop shell with local backend startup
- Editable agents, personalities, settings, prompts, and runtime contracts
- Task routing and direct/shared conversation flows
- Diagnostics and traceability
- Emergency pause / resume for the full AI runtime

What to expect:

- Fast iteration
- Occasional rough edges
- A product shape that is still evolving

## Quick Start

The easiest way to understand BossMod is to run it locally from source.

### What You Need

- Python 3.12+
- `uv`
- Rust + Cargo
- A bash-compatible shell

### Run It

```bash
git clone <your-repo-url>
cd bossmodai
./run.sh
```

What `run.sh` does:

1. Installs Python dependencies with `uv`
2. Builds the desktop shell if needed
3. Starts the local app

## First Use

Once the app opens:

1. Add at least one AI connection in Settings
2. Create a personality if you want reusable prompt behavior
3. Create an agent
4. Open the Runtime Contracts and System Prompt sections if you want to tune behavior
5. Send the agent a direct message or assign a task

If agents start behaving badly, use the red `Emergency Pause` button in the top bar. That stops the runtime without destroying your local data.

## Using Your Own Models

BossMod is model-provider flexible through `litellm`.

That means you can point it at:

- OpenAI-compatible APIs
- Self-hosted endpoints
- Local model runtimes that expose an OpenAI-style interface

Connections are configured inside the app. Agents can then use different models for different kinds of work.

## Why The Prompt Editing Matters

One of BossMod's main ideas is that runtime instructions should be editable by operators, not frozen in source code.

You can now edit:

- The system prompt wrapper
- Personality prompts
- Decision contracts
- Execution contracts

These prompt surfaces support simple conditional templating, so you can change how instructions are written for different trigger types without rebooting the app.

## Technical Overview

If you are technical, the stack is straightforward:

- Desktop shell: Tauri
- Local backend: FastAPI + asyncio
- Database: DuckDB
- Frontend: vanilla JavaScript + Tailwind via CDN
- Model routing: `litellm`

At a high level:

- the desktop shell launches the local backend
- the backend owns agent orchestration, simulation, diagnostics, and persistence
- the frontend shows the office, agent state, prompts, diagnostics, and settings

## Development Notes

Useful commands:

```bash
uv sync
uv run pytest -q
uv run python main.py
```

The main desktop launcher remains:

```bash
./run.sh
```

## Open Source License

BossMod AI is licensed under the Apache License 2.0.

That means:

- free to use
- free to modify
- free to distribute
- commercial use is allowed

See [LICENSE](LICENSE) for the full text.

## Contributing

Contributions, bug reports, and usability feedback are welcome.

If you open an issue or PR, the most helpful reports usually include:

- what you were trying to do
- what you expected to happen
- what actually happened
- screenshots or diagnostics if available

## Plain-English Summary

BossMod is a local AI office on your computer.

You create agents, give them roles, assign work, tune their instructions, watch what they do, and keep control with diagnostics and an emergency pause switch.
