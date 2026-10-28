"""Account model catalog normalization, including each model's capabilities.

The account's ``GET /v1/models`` response is OpenAI Codex's
``ModelsResponse { models: Vec<ModelInfo> }``. SIWC documents only ``slug``,
``display_name`` and ``visibility``; the capability fields read here come from
Codex's ``ModelInfo`` schema (``supported_reasoning_levels``,
``default_reasoning_level``, ``input_modalities``).

Malformed capability fields never reject the catalog or the model: the field
is omitted, logged at WARNING, and the status line reports what is missing.
Effort names are stored as the catalog spells them, unfiltered; the host
vocabulary is applied when they are read (``capabilities.Capability``), so a
host vocabulary change needs no re-fetch.

Sources:
https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
https://github.com/openai/codex/blob/d556e68ff0a2d64cd2aaa6e00748cb977f511981/codex-rs/protocol/src/openai_models.rs
"""
import logging
import re

from .config import ChatGPTError

logger = logging.getLogger(__name__)

# One effort name as a wire token. Anything else is not stored, so a hostile or
# garbled catalog cannot turn an effort name into arbitrary text in status
# lines or request bodies.
_TOKEN = re.compile(r'[a-z][a-z0-9_-]{0,31}')


def _token(value) -> str | None:
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def _efforts(slug: str, levels) -> list[str] | None:
    """Effort names from ``supported_reasoning_levels``, in catalog order, deduped.

    None means the catalog did not list thinking options for this model.
    """
    if levels is None:
        return None
    if not isinstance(levels, list):
        logger.warning('ChatGPT catalog: model %r has a malformed supported_reasoning_levels; '
                       'it offers Server default only.', slug)
        return None
    efforts = []
    for index, item in enumerate(levels):
        effort = _token(item.get('effort')) if isinstance(item, dict) else None
        if effort is None:
            logger.warning('ChatGPT catalog: model %r thinking option #%d is not a valid effort '
                           'name; it was left out.', slug, index)
        elif effort not in efforts:
            efforts.append(effort)
    return efforts


def _default_effort(slug: str, value) -> str | None:
    if value is None:
        return None
    effort = _token(value)
    if effort is None:
        logger.warning('ChatGPT catalog: model %r has a malformed default_reasoning_level; '
                       'it was left out.', slug)
    return effort


def _images(slug: str, modalities) -> str:
    """``supported``/``unsupported`` from a valid ``input_modalities`` list, else ``unknown``.

    Codex defaults a missing list to text+image; that permissive default is
    deliberately not copied, so Auto never sends images on a guess.
    """
    if modalities is None:
        return 'unknown'
    if not isinstance(modalities, list) or not all(isinstance(m, str) for m in modalities):
        logger.warning('ChatGPT catalog: model %r has malformed input_modalities; '
                       'image support is unknown.', slug)
        return 'unknown'
    return 'supported' if 'image' in modalities else 'unsupported'


def normalize(payload) -> list[dict]:
    """Turn the account's raw catalog into the stored model list.

    Args:
        payload: The decoded JSON body of ``GET /v1/models``.

    Returns:
        One dict per ``visibility == 'list'`` model, in catalog order, first
        occurrence of each slug kept: ``slug``, ``display_name`` (the slug when
        blank), ``efforts`` (raw effort names, or None when not listed),
        ``default_effort`` (or None) and ``images`` (``supported``,
        ``unsupported`` or ``unknown``).

    Raises:
        ChatGPTError: The payload has no ``models`` list, or no listed model
            has a usable slug.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('models'), list):
        raise ChatGPTError('ChatGPT returned an invalid model catalog.')
    result, seen = [], set()
    for item in payload['models']:
        if not isinstance(item, dict) or item.get('visibility') != 'list':
            continue
        slug = item.get('slug')
        if not isinstance(slug, str) or not slug.strip() or slug in seen:
            continue
        seen.add(slug)
        name = item.get('display_name')
        result.append({
            'slug': slug,
            'display_name': name if isinstance(name, str) and name.strip() else slug,
            'efforts': _efforts(slug, item.get('supported_reasoning_levels')),
            'default_effort': _default_effort(slug, item.get('default_reasoning_level')),
            'images': _images(slug, item.get('input_modalities')),
        })
    if not result:
        raise ChatGPTError('No ChatGPT models are available for this account or workspace.')
    return result
