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


def for_model(slug: str) -> Capability:
    return _EXACT.get(slug, _UNKNOWN)
