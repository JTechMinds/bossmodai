# ChatGPT connection extension

## User experience

1. Open **Extensions**, enable **ChatGPT**, and click **Continue with ChatGPT**.
2. Sign in in the system browser and authorize BossMod to use your eligible ChatGPT plan.
3. After a model-access check completes, BossMod creates one **Chat GPT** connection.
4. Select **Chat GPT** in the existing AI Connection dropdown when creating or editing any agent.

The extension offers account registration/reauthorization, model selection, cancellation,
and sign-out. Changing its model updates all agents using the shared connection.
Signing out or disabling the extension preserves that connection's ID and agent links;
requests stop until the extension is enabled and a valid account is connected again.
Existing API-key connections continue to work normally. The managed connection is
configured through the extension rather than the ordinary endpoint/API-key form.

The extension card includes BossMod's commercial-use notice and JtechMinds contact.
The existing repository license remains authoritative. This notice does not change
OpenAI's eligibility requirements or terms. Plan access does not import ChatGPT
conversation history, memories, or every feature of the ChatGPT app.

## Extension boundary

All ChatGPT-specific behavior is in this folder. The host has no imports, route
names, vendor URLs, connection-name checks, or UI text tied to ChatGPT.

The optional `model_provider` manifest capability gives an extension four methods:
`provider_status`, `provider_action`, `provider_complete`, and `provider_complete_sync`.
The loader validates the contract and injects `ProviderServices` through the existing
`ExtensionContext`. Those services can register this extension's connection, check
whether it is enabled, and wrap/unwrap its own stored credentials. This extension
never imports the database, global application settings, or the LLM client.

The host uses `extension://<extension-id>` as an internal routing marker, registers
connection rows, enforces call budgets/deadlines, and dispatches generic operator
actions. A provider raises the contract's `ProviderUsageLimitError` when the account's
plan usage is exhausted, and the host stops that work without retrying it. Provider
status contains typed action/choice descriptors, so the normal Extensions dialog
renders the provider's controls without provider-specific JS.
Another provider can implement this capability by adding its own extension folder;
no additional routing, endpoint, or UI branches are needed. Disabling/removing the
folder fails closed for its managed connections; ordinary connections still work.

The files in this folder have distinct responsibilities:

- `manifest.json` declares the connection display name, dependency checks, protocol
  endpoints/scopes, app name, and timeouts.
- `__init__.py` is the composition root and thin adapter to the host protocol. It owns
  provider-specific operator labels and action semantics.
- `auth.py` owns an instance-scoped PKCE/OIDC session. HTTP clients, JWT key lookup,
  identity decoding, browser opening, clock, credential store, connection publication,
  and the model-access probe are injected. No module-global login state is used.
- `store.py` owns atomic encrypted persistence and a cross-process refresh lock, using
  the data directory and crypto functions supplied by the host.
- `provider.py` owns the Responses transport. Sync and async calls share message
  conversion, option validation, error mapping, and one response accumulator. Both
  client factories are injectable. Only a completed response becomes an agent reply.
  The access probe requires successful completion without requiring reply text.
  Agent calls still require nonblank text, reconstructed from streaming text events
  when it is absent from the terminal response; repeated snapshots do not duplicate it.

BossMod keeps its personalities, tasks, history, and local command execution. This
extension supplies model responses instead of introducing another agent runtime.
It uses the official public Responses endpoint with `store=false` and `stream=true`.
Unsupported ChatGPT plan parameters (including temperature and output-token caps)
are omitted by this adapter; generic provider requests still carry them for providers
that support them. Extra body settings accept only `reasoning` and `text`. Image
input (attachments and Browser Vision) follows each model's catalog-reported support
and its per-model policy; see Model capabilities.

## Model capabilities

Models, thinking tiers and image support all come from the signed-in account's own
catalog (`GET /v1/models`), fetched at sign-in and on **Refresh models**. Nothing in
this folder names a model. SIWC documents only `slug`, `display_name` and `visibility`;
the response follows OpenAI Codex's `ModelInfo` schema, and three more of its fields
are read:

- `supported_reasoning_levels[].effort`: the model's thinking tiers. `none` publishes
  as BossMod's Off; other names keep their name when BossMod's thinking vocabulary has
  them. Names BossMod doesn't know (for example `ultra`) are left out and listed in the
  status as "Not available in BossMod yet".
- `default_reasoning_level`: shown as what ChatGPT uses for Server default.
- `input_modalities`: a list containing `image` means images are supported; a list
  without it means unsupported; a missing or malformed list means unknown, so Auto
  sends no images. (Codex's own text+image default for a missing list is not copied.)

Effort names are stored as the catalog sends them and filtered when read, so adding a
level to BossMod's vocabulary applies without a re-fetch. A malformed field is left
out and logged at WARNING; it never rejects the catalog or the model.

If the catalog stops carrying these fields, every model drops to Server default only
with image support unknown, and the status says "ChatGPT didn't list thinking options
for this model" and "Thinking options loaded for 0 of N models". A catalog stored
before this version shows "Refresh models to load thinking options." until the next
refresh. Capabilities never expire on a timer.

If a model's image input is set to Enabled and a later refresh reports that the model
doesn't accept images, the model is published with images off, the stored choice is
kept, and the status names the conflict. Enabled can't be chosen for such a model.
Under Auto, a model whose catalog lists image input receives attachments and Browser
Vision images, which uses plan usage; set the model to Disabled to stop that.

Schema reference: https://github.com/openai/codex/blob/d556e68ff0a2d64cd2aaa6e00748cb977f511981/codex-rs/protocol/src/openai_models.rs

## Install

The host must support the optional model-provider contract introduced by this PR.
After installing these source changes, run `uv sync` and restart BossMod. No database
migration or shared developer key is required. Subsequent model-provider extensions
use this same host contract. The manifest declares the Python modules it requires;
the shipped runtime includes their dependencies through the existing uv dependency
policy. This is a native BossMod extension, not a plugin installed in ChatGPT.

## Validation and remaining acceptance check

Automated tests cover PKCE setup, callback validation, signed ID-token verification,
credential redaction, refresh rotation/concurrency, managed connection identity,
disabling/sign-out, Responses history conversion, stream completion/errors/timeouts,
System AI, and extension UI sign-in/status polling. Additional contract tests install
two unrelated providers and exercise discovery, scoped connection registration,
operator actions, agent/System AI dispatch, missing/disabled providers, and malformed
capabilities. Boundary checks prevent vendor logic from moving back into the host.

A real OpenAI account sign-in has **not** been performed in this development session.
Before release, test the desktop browser round trip, authorization/consent, model
catalog, actual agent task execution, restart, reauthorization, switching accounts,
and sign-out on supported desktop platforms. Account/workspace eligibility, limits,
and evolving preview behavior still apply. The initial connection check uses a small
amount of the user's authorized plan usage. If remote sign-out revocation cannot be
confirmed, the UI explains that the user can disconnect BossMod in ChatGPT Settings.

## Official protocol references

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
