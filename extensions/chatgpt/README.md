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
actions. Provider status contains typed action/choice descriptors, so the normal
Extensions dialog renders the provider's controls without provider-specific JS.
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
that support them. Extra body settings accept only `reasoning` and `text`. Browser
Vision continues to use BossMod's existing per-model image-capability flag.

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
