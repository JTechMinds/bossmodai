# Agent packs

Packs are YAML files in a GitHub repository, contributed by pull request.
There is no custom store backend, module marketplace, or storefront UI.

Each pack file is a hire-contract template: specialty, description, and
what done looks like. Optional personality and tools hints may be
included. A pack does not include an agent name, desk, seats,
credentials, or executable content.

## Schema

`schema` must be `bossmod.agent_pack/v1`. `kind` is `agent` (v1 implements
agent packs only). The field is reserved so skill or workflow packs can
be added later without a schema rewrite; those kinds are not imported or
installed in v1. Required hire fields: specialty, description,
what-done-looks-like. Unknown keys are ignored and never executed. Keys
that look like shell, install hooks, or credentials are rejected. YAML
is parsed as data only.

## Catalog

Default catalog repo: https://github.com/JTechMinds/BossMod_AgentMP
(`agent_pack_catalog_repo`). Extra trusted `owner/repo` slugs may be
listed in `agent_pack_url_allowlist`. Contribute by opening a pull
request in that repo. The locked directory shape is:

```text
catalog.yaml
packs/
  engineering/
    code-auditor.agent.yaml
  product/
    feature-planner.agent.yaml
```

- Folder = category slug (not a display name). No `Profiles/` wrapper.
- File = stable pack id: `packs/<category>/<id>.agent.yaml`.
- `catalog.yaml` is the index. Each row has `id`, `kind`, `path`,
  `category`, and `title`.
- Category in `catalog.yaml` must match the folder in `path`. Catalog CI
  should fail when they drift.

The app reads `catalog.yaml` first at the pinned commit or tag, then
fetches the pack file from that index row. It does not browse or render
a storefront.

## Pin

Import always pins a commit SHA or a tag. Floating names such as `main`
are rejected. The app records the resolved commit it imported; later
hires from that import stay on that commit until imported again at a new
pin.

## Import

`POST /api/agent-packs/import` fetches one pinned file and returns
hire-form fields (`role`, `description`, `done_fail_bar`, optional
hints). It does not create or patch an agent. The operator still supplies
name and seats. Passing `agent_id` is rejected so a live hire cannot be
silently overwritten.

Catalog body (resolves through `catalog.yaml`):
`{ "id": "code-auditor", "ref": "<sha-or-tag>" }`.
Path is accepted only when that path is listed in the index at the pin.
GitHub URL body:
`{ "url": "https://github.com/owner/repo/blob/<sha>/packs/engineering/code-auditor.agent.yaml" }`.

## Trust

The catalog repo and `agent_pack_url_allowlist` are trusted. Any other
GitHub URL fails closed unless the request sets `confirm: true` or a
matching `confirm_token` (HMAC of the canonical source). Non-GitHub
hosts are never fetched.

## Export

`GET /api/agents/{id}/pack` writes the agent's specialty, description,
and done/fail bar back to a valid pack with `kind: agent` (two-way with
import). Name and desk are omitted.
