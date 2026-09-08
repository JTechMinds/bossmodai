"""GitHub pack location, pin rules, trust gate, and fetch source.

Import always pins a commit SHA or a tag. Floating branch names such as
``main`` are rejected. Only github.com hosts are fetched. Non-allowlisted
repos require an explicit confirm flag or matching confirm token.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import unquote, urlparse

import httpx

from core.agent_pack.catalog import validate_catalog_pack_path
from core.agent_pack.schema import AgentPackError

CATALOG_REPO_SETTING = "agent_pack_catalog_repo"
CATALOG_PATH_SETTING = "agent_pack_catalog_path"
CATALOG_PIN_SETTING = "agent_pack_catalog_pin"
ALLOWLIST_SETTING = "agent_pack_url_allowlist"
DEFAULT_CATALOG_REPO = "JTechMinds/BossMod_AgentMP"
DEFAULT_CATALOG_PATH = "packs"
# Browse and default catalog import pin a commit, never a floating branch.
# Short SHAs are resolved through GitHub to the full commit.
DEFAULT_CATALOG_PIN = "3c1e0a6"

FLOATING_REFS = frozenset({
    "main",
    "master",
    "head",
    "develop",
    "trunk",
    "default",
    "latest",
})
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_SHA_PREFIX_RE = re.compile(r"^[0-9a-f]{7,39}$", re.IGNORECASE)
_TAG_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]{0,127}$")
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_FILE_RE = re.compile(
    r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:blob|raw)/(?P<ref>[^/]+)/(?P<path>.+)$"
)
_RAW_FILE_RE = re.compile(
    r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<ref>[^/]+)/(?P<path>.+)$"
)
_USER_AGENT = "BossMod-AgentPack/1.0"


@dataclass(frozen=True)
class PackLocation:
    """Pinned GitHub file location for one pack import."""

    owner: str
    repo: str
    path: str
    requested_ref: str
    commit_sha: str | None = None
    from_catalog: bool = False

    @property
    def repo_slug(self) -> str:
        return f"{self.owner}/{self.repo}".lower()

    def canonical_source(self) -> str:
        return f"github.com/{self.owner}/{self.repo}/{self.path}@{self.requested_ref}".lower()

    def with_sha(self, sha: str) -> PackLocation:
        return PackLocation(
            owner=self.owner,
            repo=self.repo,
            path=self.path,
            requested_ref=self.requested_ref,
            commit_sha=sha.lower(),
            from_catalog=self.from_catalog,
        )


class PackSource(Protocol):
    """Fetch a pack file at a pinned GitHub ref. Injected in tests."""

    def resolve_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        """Return the full commit SHA for a tag or SHA prefix."""

    def fetch_file(self, owner: str, repo: str, path: str, sha: str) -> str:
        """Return UTF-8 file contents at ``sha``."""


class GitHubPackSource:
    """Fetches pack files from github.com / raw.githubusercontent.com only."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def resolve_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        if _FULL_SHA_RE.fullmatch(ref):
            return ref.lower()
        url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
        status, body, sha = self._get_json_sha(url)
        if status == 404 or not sha:
            raise AgentPackError(
                f"GitHub ref {ref!r} could not be resolved to a commit.",
                code="pin_unresolved",
            )
        if status >= 400:
            raise AgentPackError(
                "GitHub ref lookup failed.",
                code="fetch_failed",
                status=502,
            )
        return sha.lower()

    def fetch_file(self, owner: str, repo: str, path: str, sha: str) -> str:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}"
        status, text = self._get_text(url)
        if status == 404:
            raise AgentPackError("Pack file was not found at the pinned ref.", code="fetch_failed")
        if status >= 400:
            raise AgentPackError(
                "GitHub pack fetch failed.",
                code="fetch_failed",
                status=502,
            )
        return text

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=10.0,
                follow_redirects=False,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "application/vnd.github+json",
                },
            )
        return self._client

    def _get_text(self, url: str) -> tuple[int, str]:
        try:
            response = self._http().get(url)
        except httpx.HTTPError as exc:
            raise AgentPackError(
                "GitHub pack fetch failed.",
                code="fetch_failed",
                status=502,
            ) from exc
        return response.status_code, response.text

    def _get_json_sha(self, url: str) -> tuple[int, str, str | None]:
        try:
            response = self._http().get(url)
        except httpx.HTTPError as exc:
            raise AgentPackError(
                "GitHub ref lookup failed.",
                code="fetch_failed",
                status=502,
            ) from exc
        sha = None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            raw_sha = payload.get("sha")
            if isinstance(raw_sha, str) and _FULL_SHA_RE.fullmatch(raw_sha):
                sha = raw_sha
        return response.status_code, response.text, sha


def parse_github_pack_url(raw: str) -> PackLocation:
    """Parse a GitHub file URL. Rejects non-GitHub hosts and missing paths."""
    url = (raw or "").strip()
    if not url:
        raise AgentPackError("Pack URL is required.", code="invalid_source")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise AgentPackError("Pack URL must use https.", code="invalid_source")
    if parsed.username or parsed.password:
        raise AgentPackError("Pack URL must not include userinfo.", code="invalid_source")
    host = (parsed.hostname or "").lower().rstrip(".")
    path = unquote(parsed.path or "")
    if host in {"github.com", "www.github.com"}:
        match = _GITHUB_FILE_RE.fullmatch(path)
        if match is None:
            raise AgentPackError(
                "GitHub pack URL must point at a file (/blob/<ref>/path or /raw/<ref>/path).",
                code="invalid_source",
            )
    elif host == "raw.githubusercontent.com":
        match = _RAW_FILE_RE.fullmatch(path)
        if match is None:
            raise AgentPackError(
                "raw.githubusercontent.com URL must be /owner/repo/<ref>/path.",
                code="invalid_source",
            )
    else:
        raise AgentPackError(
            "Pack import only fetches github.com or raw.githubusercontent.com.",
            code="invalid_source",
        )
    owner = match.group("owner")
    repo = match.group("repo")
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    ref = match.group("ref")
    file_path = match.group("path").lstrip("/")
    _validate_owner_repo(owner, repo)
    validate_pin_ref(ref)
    return PackLocation(
        owner=owner,
        repo=repo,
        path=_validate_pack_path(file_path),
        requested_ref=ref,
    )


def catalog_pack_location(*, catalog_repo: str, path: str, ref: str) -> PackLocation:
    """Build a location for a catalog pack path already resolved from catalog.yaml."""
    owner, repo = parse_catalog_repo(catalog_repo)
    validate_pin_ref(ref)
    cleaned, _category, _pack_id = validate_catalog_pack_path(path)
    return PackLocation(
        owner=owner,
        repo=repo,
        path=cleaned,
        requested_ref=ref,
        from_catalog=True,
    )


def parse_catalog_repo(raw: str) -> tuple[str, str]:
    value = (raw or "").strip()
    if not value:
        raise AgentPackError(
            "Agent pack catalog repo is not configured.",
            code="catalog_unconfigured",
            status=409,
        )
    if "://" in value or value.startswith("github.com/"):
        parsed = urlparse(value if "://" in value else f"https://{value}")
        host = (parsed.hostname or "").lower()
        if host not in {"github.com", "www.github.com"}:
            raise AgentPackError(
                "Catalog repo must be an owner/repo slug or github.com URL.",
                code="invalid_source",
            )
        parts = [segment for segment in (parsed.path or "").split("/") if segment]
        if len(parts) < 2:
            raise AgentPackError(
                "Catalog repo must be an owner/repo slug or github.com URL.",
                code="invalid_source",
            )
        owner, repo = parts[0], parts[1]
    else:
        if not _OWNER_REPO_RE.fullmatch(value):
            raise AgentPackError(
                "Catalog repo must be an owner/repo slug.",
                code="invalid_source",
            )
        owner, repo = value.split("/", 1)
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    _validate_owner_repo(owner, repo)
    return owner, repo


def validate_pin_ref(ref: str) -> None:
    """Reject floating branch names. Accept a commit SHA or a tag."""
    value = (ref or "").strip()
    if not value:
        raise AgentPackError(
            "Pack import requires a pinned commit SHA or tag.",
            code="floating_ref",
        )
    if value.casefold() in FLOATING_REFS:
        raise AgentPackError(
            f"Ref {value!r} is a floating branch. Pin a commit SHA or tag.",
            code="floating_ref",
        )
    if _FULL_SHA_RE.fullmatch(value) or _SHA_PREFIX_RE.fullmatch(value):
        return
    if _TAG_RE.fullmatch(value):
        return
    raise AgentPackError(
        "Pack ref must be a commit SHA or a tag, not a floating branch.",
        code="floating_ref",
    )


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """Parse extra ``owner/repo`` slugs. Empty is deny-all besides the catalog."""
    slugs: set[str] = set()
    for token in (raw or "").split(","):
        item = token.strip().strip("/")
        if not item:
            continue
        if "github.com/" in item.lower():
            parsed = urlparse(item if "://" in item else f"https://{item}")
            parts = [segment for segment in (parsed.path or "").split("/") if segment]
            if len(parts) >= 2:
                slugs.add(f"{parts[0]}/{parts[1].removesuffix('.git')}".lower())
            continue
        if _OWNER_REPO_RE.fullmatch(item):
            owner, repo = item.split("/", 1)
            slugs.add(f"{owner}/{repo.removesuffix('.git')}".lower())
    return frozenset(slugs)


def is_allowlisted(location: PackLocation, *, catalog_repo: str, extra_allowlist: str | None) -> bool:
    """Catalog repo is always trusted. Extra slugs come from settings."""
    if location.from_catalog:
        return True
    allowed = set(parse_allowlist(extra_allowlist))
    try:
        owner, repo = parse_catalog_repo(catalog_repo)
        allowed.add(f"{owner}/{repo}".lower())
    except AgentPackError:
        pass
    return location.repo_slug in allowed


def confirm_token_for(location: PackLocation, secret: str) -> str:
    """HMAC over the canonical source. Operator must present this or confirm=true."""
    digest = hmac.new(
        (secret or "").encode("utf-8"),
        location.canonical_source().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:24]


def assert_trust(
    location: PackLocation,
    *,
    catalog_repo: str,
    extra_allowlist: str | None,
    confirm: bool,
    confirm_token: str | None,
    confirm_secret: str,
) -> None:
    """Fail closed unless the repo is allowlisted or confirm is explicit."""
    if is_allowlisted(location, catalog_repo=catalog_repo, extra_allowlist=extra_allowlist):
        return
    if confirm:
        return
    expected = confirm_token_for(location, confirm_secret)
    provided = (confirm_token or "").strip()
    if provided and len(provided) == len(expected) and secrets.compare_digest(provided, expected):
        return
    raise AgentPackError(
        "Non-allowlisted pack URL requires confirm=true or a matching confirm_token.",
        code="trust_required",
        status=403,
    )


def _validate_owner_repo(owner: str, repo: str) -> None:
    if not _OWNER_REPO_RE.fullmatch(f"{owner}/{repo}"):
        raise AgentPackError("GitHub owner/repo is invalid.", code="invalid_source")
    if owner in {".", ".."} or repo in {".", ".."}:
        raise AgentPackError("GitHub owner/repo is invalid.", code="invalid_source")


def _validate_pack_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/"):
        raise AgentPackError("Pack path is invalid.", code="invalid_source")
    parts = [part for part in cleaned.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise AgentPackError("Pack path must not contain '..' segments.", code="invalid_source")
    if len(cleaned) > 256:
        raise AgentPackError("Pack path is too long.", code="invalid_source")
    lowered = cleaned.lower()
    if not (lowered.endswith(".yaml") or lowered.endswith(".yml")):
        raise AgentPackError("Pack path must be a .yaml or .yml file.", code="invalid_source")
    return "/".join(parts)
