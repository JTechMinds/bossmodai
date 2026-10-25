"""Official exact-slug capabilities; never infer entitlement or match prefixes.

Provenance and public-page snapshots: docs/chatgpt-exact-model-evidence.md.
Unknown slugs intentionally remain default-only with unknown image support.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    efforts: tuple[str, ...] = ()
    images: str = "unknown"
    source: str | None = None
    verified: str | None = None

    def thinking_levels(self):
        return {("off" if effort == "none" else effort): {"reasoning": {"effort": effort}}
                for effort in self.efforts}

    def images_enabled(self, policy):
        return policy == "enabled" or (policy == "auto" and self.images == "supported")


_GPT5 = Capability(("minimal", "low", "medium", "high"), "supported",
                   "https://developers.openai.com/api/docs/models/gpt-5", "2026-10-24")
_GPT51 = Capability(("none", "low", "medium", "high"), "supported",
                    "https://developers.openai.com/api/docs/models/gpt-5.1", "2026-10-24")
_EXACT = {"gpt-5": _GPT5, "gpt-5-2025-08-07": _GPT5,
          "gpt-5.1": _GPT51, "gpt-5.1-2025-11-13": _GPT51}
_UNKNOWN = Capability()


# Exact identifiers only. Official model references document these Responses
# efforts; catalog visibility continues to determine account entitlement.
for _slug, _efforts in (
    ('gpt-5.2', ('none', 'low', 'medium', 'high', 'xhigh')),
    ('gpt-5.2-2025-12-11', ('none', 'low', 'medium', 'high', 'xhigh')),
    ('gpt-5.4', ('none', 'low', 'medium', 'high', 'xhigh')),
    ('gpt-5.4-2026-03-05', ('none', 'low', 'medium', 'high', 'xhigh')),
    ('gpt-5.2-codex', ('low', 'medium', 'high', 'xhigh')),
    ('gpt-5.3-codex', ('low', 'medium', 'high', 'xhigh')),
):
    _reference = {'gpt-5.2-2025-12-11': 'gpt-5.2',
                  'gpt-5.4-2026-03-05': 'gpt-5.4'}.get(_slug, _slug)
    _EXACT[_slug] = Capability(_efforts, 'supported',
        'https://developers.openai.com/api/docs/models/' + _reference,
        '2026-10-24')

# OpenAI's pinned Codex catalog explicitly documents this exact identifier.
_EXACT['gpt-5.4-mini'] = Capability(('low', 'medium', 'high', 'xhigh'),
    'supported', 'https://github.com/openai/codex/blob/'
    'd556e68ff0a2d64cd2aaa6e00748cb977f511981/codex-rs/core/models.json',
    '2026-10-24')


def for_model(slug: str) -> Capability:
    return _EXACT.get(slug, _UNKNOWN)
