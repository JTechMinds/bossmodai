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

## Install these source changes

This integration includes host changes as well as `extensions/chatgpt/`; copying only
that folder into an older installation is insufficient. Apply the supplied patch to
the matching BossMod checkout, run `uv sync`, and restart BossMod. No database migration
or shared developer key is required. The package is a native BossMod extension, not a
plugin installed in ChatGPT.

The implementation was based on repository commit
`ed8b4eb729b1d22b5dc90b6ee0619fe3ae003b9d`.

## Implementation

- `core/chatgpt/auth.py`: official local-app authorization code + PKCE/OIDC flow.
  A temporary loopback listener binds only `127.0.0.1`; it starts before the system
  browser opens. Attempts expire after ten minutes and callbacks are consumed once.
  State, nonce, signature, issuer, audience, expiration, subject, and plan scope are
  checked before use. The BossMod `/api` authentication gate is unchanged.
- `core/chatgpt/store.py`: stable per-installation host ID and separate account/client
  registrations. Credentials are wrapped using BossMod's existing secret store and
  written atomically with owner-only file permissions. Cross-process file locking
  serializes rotating token refreshes between the API and runtime worker.
- `core/chatgpt/provider.py`: official public Responses API with `store=false` and
  `stream=true`. No ChatGPT private endpoints, browser cookies, or borrowed CLI tokens.
  System/developer instructions and full relevant history are sent with each request.
  A response is successful only after `response.completed`; partial output and
  subscription-limit failures are errors. Normal agent calls retain budget lanes,
  cancellation, stall detection, and the absolute timeout.
- The agent completion path and separate System AI helper both recognize the managed
  `chatgpt://local` routing marker. That marker is internal, not a network endpoint;
  OAuth credentials never appear in an AI connection row or API response.
- The first account-visible model is the initial default; a completed minimal inference
  check verifies access before the connection appears. A later model selection is also
  probed before changing the connection. Reauthorization preserves the existing model
  where it is still offered. Account changes affect the shared connection.

BossMod still owns its personalities, tasks, histories, and local command execution.
This adapter supplies model responses, rather than introducing a second agent runtime.
Preview-unsupported parameters (including temperature and output-token caps) are not
sent to the ChatGPT plan route. Existing per-agent temperature/token settings therefore
do not constrain this route. Extra body settings, if added programmatically, accept
only `reasoning` and `text`. Image inputs are translated, but Browser Vision still
requires the model-capability flag that BossMod normally uses; this integration does
not assume that every account-visible model supports images.

## Validation and remaining acceptance check

Automated tests cover PKCE setup, callback validation, signed ID-token verification,
credential redaction, refresh rotation/concurrency, managed connection identity,
disabling/sign-out, Responses history conversion, stream completion/errors/timeouts,
System AI, and extension UI sign-in/status polling.

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
