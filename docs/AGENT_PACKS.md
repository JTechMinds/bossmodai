# Agent packs

Packs are YAML files in a GitHub repository, contributed by pull request.
There is no custom store backend: the catalog is that repo. The app
browses it read-only in a marketplace view and keeps what it installs in
a local template library.

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

Optional `pack_author` is additive on v1:

```yaml
pack_author:
  name: Example Studio
  url: https://github.com/example
```

`name` is required when `pack_author` is present. `url` is optional and
must be an `http` or `https` URL. Import stores author on the pack
object returned by `POST /api/agent-packs/import`; it is not a hire
field and does not create an agent. Export (`GET /api/agents/{id}/pack`)
fills `pack_author` from the company name (and company URL when set),
and omits it when the company is unknown.

Optional `tools_hint` must be a YAML list of short tool names
(`cli`, `work`). Prose strings are rejected.

## Senior quality

Bare schema still accepts a short hire snapshot (export of a casual
live agent). Catalog import and pack CI require structured senior
sections so a pack is not a one-liner.

Required sections, either as labeled headings inside `description` /
`what_done_looks_like` or as additive fields (`mission`, `in_scope`,
`out_of_scope`, `handoff`, `fail_examples`) that fold into those two
hire strings:

- Mission
- In scope
- Out of scope
- Handoff (who gets what next)
- What done looks like, including **Fail examples**

Canonical form is labeled text in `description` and
`what_done_looks_like`, so hydrate stays on `role` / `description` /
`done_fail_bar`. Mission and the done success bar have minimum length
floors; Fail examples must name concrete bad outcomes, not only
"empty done does not count."

```yaml
description: |
  Hire when work claims done and needs evidence-backed CLEAR.
  Mission: Review claims against a checkable allow/deny bar in this workspace.
  In scope: Named artifacts and tests the operator can open from the desk.
  Out of scope: Live production deploys, credentials, and host-wide scans.
  Handoff: Operator receives the allow/deny note plus evidence paths.
what_done_looks_like: |
  A checkable allow/deny exists with a named artifact or tests path.
  Fail examples: "Looks good" with no path; a vibe check; done with no evidence.
```

Unlabeled lead-in on `description` is the When-to-hire preamble. Hydrate
keeps it on the hire description.

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
  `category`, `title`, and optional `summary` (one-line When-to-hire).
- Marketplace cards and the pack detail both open on When-to-hire. The
  pack description's own preamble is the source of truth and is shown in
  full; catalog `summary` stands in only for a pack whose description
  opens straight on a heading and so has no preamble. It is not a
  fallback for a pack that could not be read: `list_catalog` withholds
  that row, so it never becomes a card at all.
- Category in `catalog.yaml` must match the folder in `path`. Catalog CI
  should fail when they drift.

The app reads `catalog.yaml` first at the pinned commit or tag, then
fetches the pack file from that index row. `GET /api/agent-packs`
returns that index at the pin as browse cards; each card carries the
fields, the labeled sections and the content hash read from the one pack
fetch it already made. Nothing is mirrored or hosted locally.

Each listed pack goes through the same parse and senior-quality gate that
install runs, so a card in `categories` is always installable. A row that
is not read, or that is read and fails either check, is withheld — absent
from `categories` and from the category counts — and reported in
`withheld` as
`{ "id", "path", "category", "title", "kind", "code", "message" }`, so a
catalog maintainer sees what their own repo shipped instead of watching a
pack vanish.

`kind` is the fact, and it is decided by which call failed rather than by
reading `code` back:

- `"refused"` — the pack file was read and this app rejected its content
  (`pack_quality`, `invalid_yaml`, `invalid_schema`, `missing_field`,
  `unsupported_kind`, `dangerous_key`, `pack_too_large`). The maintainer
  has to change the pack.
- `"unavailable"` — the pack file could not be read at all
  (`fetch_failed`). Nothing is known about its content; the move is to
  retry.

Both stay out of the grid, because you cannot install what you cannot
read. A catalog whose every pack is invalid is an empty `categories` plus
a full `withheld`, not an error.

## Pin

Import always pins a commit SHA or a tag. Floating names such as `main`
are rejected. The app records the resolved commit it imported; later
hires from that import stay on that commit until imported again at a new
pin.

## Import

`POST /api/agent-packs/import` fetches one pinned file and returns
hire-form fields (`role`, `description`, `done_fail_bar`, optional
hints) plus the pack mapping (including `pack_author` when present).
It does not create or patch an agent. The operator still supplies
name and seats. Passing `agent_id` is rejected so a live hire cannot be
silently overwritten. Catalog imports must pass senior quality.

Catalog body (resolves through `catalog.yaml`):
`{ "id": "code-auditor", "ref": "<sha-or-tag>" }`.
Path is accepted only when that path is listed in the index at the pin.
GitHub URL body:
`{ "url": "https://github.com/owner/repo/blob/<sha>/packs/engineering/code-auditor.agent.yaml" }`.

## Template library

Installing writes one `agent_templates` row: a local, pinned snapshot of
the pack file — specialty, description, what done looks like, hints,
author, the commit it was read at, and the sha256 of its canonical YAML.
That row is what pre-fills the create-agent form. Installing still never
creates an agent.

`GET /api/agent-templates` lists installed rows; it is a local read with
no fetch, and each row carries its description and done bar split into
their labeled sections. `POST /api/agent-templates` installs one by
catalog `id` or by GitHub `url`, through the same `import_pack` pipeline
as above — pin validation, the allowlist, the trust gate, senior
quality, and the refusal of `agent_id` are inherited unchanged, and
there is no second import path. Re-installing updates the row instead of
duplicating it. `DELETE /api/agent-templates/{id}` removes one row;
agents already created from it are untouched, because a template is a
snapshot and not a live link.

Staleness is decided by comparing content hashes, never commit SHAs: the
catalog pin is repo-wide, so a SHA comparison would mark every installed
template stale on any pin bump, including packs whose file never
changed.

## Trust

The catalog repo and `agent_pack_url_allowlist` are trusted. Any other
GitHub URL fails closed unless the request sets `confirm: true` or a
matching `confirm_token` (HMAC of the canonical source). Non-GitHub
hosts are never fetched.

## Export

`GET /api/agents/{id}/pack` writes the agent's specialty, description,
and done/fail bar back to a valid pack with `kind: agent` (two-way with
import). Name and desk are omitted. `pack_author` is filled from company
settings when available. Casual live hires may export a schema-valid
pack that does not yet meet senior quality; catalog contribution still
requires the structured sections above.
