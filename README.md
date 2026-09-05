# BossMod AI

### AI is powerful. But it shouldn't be this hard.

Most AI tools are built for developers, prompt engineers, and people who already know what "temperature 0.7" means. We think that's backwards. AI should be something anyone can pick up, experiment with, and actually get value from — without reading a research paper first.

**BossMod is a virtual AI office that runs on your computer.** You hire AI agents, give them personalities and roles, assign them real work, and manage them like a team — all through a visual interface that feels more like a game than a command line.

No cloud accounts. No monthly fees. No PhD required.

Created by Jordan Gonzales of [JtechMinds LLC](https://jtechminds.com).

---

## What You Can Do With BossMod

Imagine having a team of AI workers that you can actually see, talk to, and manage:

🏢 **Build your team** — Hire agents and give them roles like Researcher, Software Engineer, Marketer, Designer, or anything you dream up

🗺️ **Watch them work** — A live office map shows your agents moving around, collaborating, and getting things done in real time

💬 **Talk to them** — Chat directly with any agent, set up team channels, or put them in meetings together

📋 **Assign real work** — Give agents tasks and watch how they plan, respond, and deliver results

🎛️ **Customize everything** — Edit personalities, tweak how agents think and make decisions, all without touching code

🛑 **Stay in control** — Full diagnostics when something goes wrong, and a big red pause button when you need it

## Getting Started

You can be up and running in about 2 minutes.

### You'll Need

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (Python package manager)
- Rust + Cargo (for the desktop shell)
- A bash-compatible shell (Mac/Linux terminal, WSL on Windows)

### Let's Go

```bash
git clone <your-repo-url>
cd bossmodai
./run.sh
```

The script handles everything — installs dependencies, builds the app, and opens it up.

### Your First 5 Minutes

Once the app opens, here's the fastest path to seeing something cool:

1. **Connect a brain** — Go to Settings and add an AI provider (OpenAI, Anthropic, a local model, whatever you've got)
2. **Pick a personality** — We ship 9 ready-made personalities like Software Engineer, Growth Marketer, and QA Engineer so you don't have to write prompts from scratch
3. **Hire your first agent** — Give them a name, a role, and a personality
4. **Say hi** — Send them a message or assign a task and see what happens

If an agent starts going off the rails, hit the red **Emergency Pause** button at the top. It stops everything instantly without losing your work.

## Works With Any AI Model

BossMod doesn't lock you into one provider. Connect whatever models you want:

- **Cloud providers** — OpenAI, Anthropic, Google, and more
- **Local models** — Ollama, LM Studio, vLLM, or anything with an OpenAI-compatible API
- **Mix and match** — Use a fast cheap model for casual chat and a powerful one for deep work

Use a local model and your data never leaves your computer. Everything stays on your machine.

## Built for Tinkerers (And Everyone Else)

Here's what makes BossMod different from other AI tools: **you're in the driver's seat.**

Most AI apps hide their instructions deep in source code where you can't touch them. BossMod puts everything in your hands:

- **Personalities** — Shape how your agents think and communicate
- **System prompts** — Control the master instructions that guide every agent
- **Decision contracts** — Define how agents decide what to do when they're asked something
- **Execution contracts** — Define how agents carry out work step by step

Everything supports live editing with conditional templates. Change how your agents behave based on context — no coding, no restarts. Just tweak, save, and see the results.

## Your Data Stays With You

BossMod runs 100% on your machine. That's not a feature we bolted on — it's how the whole thing was designed.

- Your API keys, agent data, and conversation history never leave your computer
- Diagnostics and prompt traces are stored locally
- Connect a local model and the entire workflow is completely offline

The only time data leaves your machine is if you connect to a cloud AI provider — and that's your choice.

## Where We're At

BossMod is **early, active, and evolving fast.** The foundation is solid — agents work, tasks route, conversations flow, diagnostics trace everything — but we're still building and improving every day.

We'd love for you to try it, break it, and tell us what you think.

## Under the Hood

For the technically curious:

| Layer | Tech |
|-------|------|
| Desktop shell | Tauri |
| Backend | FastAPI + asyncio |
| Database | SQLITE |
| Frontend | Vanilla JS + Tailwind |
| Model routing | litellm |

## Development

```bash
uv sync                  # install dependencies
uv run pytest -q         # run tests
uv run python main.py    # start the backend directly
./run.sh                 # full desktop app launch
```

### Local API authentication

The FastAPI backend generates a `local_api_token` on first run and requires it on every `/api` request (REST and WebSocket). This is a localhost desktop gate, not multi-tenant auth.

| Client | How to send the token |
| --- | --- |
| Desktop UI | Injected into the index page and attached automatically as `X-BossMod-Token` (WebSocket uses `?token=`) |
| Scripts / curl | `X-BossMod-Token: <token>` or `Authorization: Bearer <token>` |
| Tests / automation | Set `BOSSMOD_LOCAL_API_TOKEN` to a known value |

`GET /health` and the HTML/static UI stay unauthenticated. Secret fields such as `telegram_bot_token` and connection `api_key` are redacted on list/get responses (`has_*` + last-4 only), even for authenticated callers.

Telegram is fail-closed: if the bot is enabled with an empty allowlist, it will not start, and no Telegram user is authorized.

## License

BossMod AI is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE).

**What that means:**

- **Free for personal use** — learning, experimenting, hobby projects, research
- **Free for nonprofits and educators** — charities, schools, public institutions
- **Commercial use requires a license** — if you're making money with BossMod, [let's talk](mailto:jordan@jtechminds.com)

See [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for copyright details.

## Get Involved

We're building BossMod in the open and we'd love your help.

- **Found a bug?** Open an issue and tell us what happened
- **Have an idea?** We want to hear it
- **Want to contribute code?** PRs are welcome — by submitting a PR you agree to our [Contributor License Agreement](CLA.md)

The most helpful reports include what you were trying to do, what you expected, and what actually happened. Screenshots and diagnostics are a bonus.

---

*BossMod AI — Making AI work for everyone, not just engineers.*
