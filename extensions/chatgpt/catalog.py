"""Account catalog normalization. Undocumented capability fields are not trusted.

Source: https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
Reviewed 2026-10-24 in the accepted specification. No authorized capability
fixture is available: unknown slugs offer server default only, Auto images off.
"""
from .config import ChatGPTError


def normalize(payload):
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
        result.append({'slug': slug, 'display_name': name if isinstance(name, str) and name.strip() else slug})
    if not result:
        raise ChatGPTError('No ChatGPT models are available for this account or workspace.')
    return result
