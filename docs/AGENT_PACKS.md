# Agent packs

Packs are YAML files in a GitHub repository, contributed by pull request.
There is no custom store backend, module marketplace, or storefront UI.

Each file is a hire-contract template: specialty, description, and what
done looks like. Optional personality and tools hints may be included. A
pack does not include an agent name, desk, seats, credentials, or
executable content.

## Schema

`schema` must be `bossmod.agent_pack/v1`. `kind` is `agent` (v1 implements
agent packs only). The field is reserved so skill or workflow packs can
be added later without a schema rewrite; those kinds are not imported or
installed in v1. Required hire fields: specialty, description,
what-done-looks-like. Unknown keys are ignored and never executed. Keys
that look like shell, install hooks, or credentials are rejected. YAML
is parsed as data only.

## Catalog

The catalog is git: YAML files under `packs/` in the configured GitHub
repo (`agent_pack_catalog_repo`, default `JTechMinds/bossmodai`). Extra
trusted `owner/repo` slugs may be listed in `agent_pack_url_allowlist`.
Contribute a pack by opening a pull request that adds a `.yaml` file.

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

Catalog body: `{ "path": "software-engineer.yaml", "ref": "<sha-or-tag>" }`.
GitHub URL body: `{ "url": "https://github.com/owner/repo/blob/<sha>/packs/example.yaml" }`.

## Trust

The catalog repo and `agent_pack_url_allowlist` are trusted. Any other
GitHub URL fails closed unless the request sets `confirm: true` or a
matching `confirm_token` (HMAC of the canonical source). Non-GitHub
hosts are never fetched.

## Export

`GET /api/agents/{id}/pack` writes the agent's specialty, description,
and done/fail bar back to a valid pack with `kind: agent` (two-way with
import). Name and desk are omitted.
