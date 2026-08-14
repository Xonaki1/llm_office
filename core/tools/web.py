"""Network tools.

These are the only tools that reach outside the process, so they carry the
security weight. A model can be talked into fetching a URL by anything it reads
— a search result, a file, a user message — which makes server-side request
forgery the realistic threat, not a theoretical one.

The guard is applied at connect time, per hop:

  * only http and https;
  * the hostname is resolved and every resolved address is checked against the
    private, loopback, link-local, multicast and reserved ranges;
  * redirects are followed manually so each new location is re-checked — a
    public host that redirects to 169.254.169.254 is the classic bypass;
  * responses are capped and time-bounded, because the body is fed back into
    the next prompt and billed as input tokens.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from core.config import get_settings
from core.tools.base import (
    SideEffect,
    Tool,
    ToolContext,
    ToolError,
    ToolResult,
    optional_int,
    require_str,
)

log = structlog.get_logger(__name__)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud metadata endpoints, which are reachable and unauthenticated from inside
# most hosting environments. Blocked by IP range too; named for clarity in logs.
_METADATA_HOSTS = frozenset({"metadata.google.internal", "metadata.goog"})


class UnsafeUrl(ToolError):
    pass


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"could not resolve host {host!r}") from exc
    return [str(info[4][0]) for info in infos]


async def assert_safe_url(url: str) -> str:
    """Validate a URL and its resolved addresses. Returns the normalised URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrl(f"only http and https URLs are allowed, got {parsed.scheme!r}")
    if not parsed.hostname:
        raise UnsafeUrl("the URL has no host")

    host = parsed.hostname.lower()
    if host in _METADATA_HOSTS:
        raise UnsafeUrl("that host is not allowed")

    settings = get_settings()
    blocked = {h.lower() for h in settings.tool_blocked_hosts}
    if host in blocked or any(host.endswith(f".{b}") for b in blocked):
        raise UnsafeUrl(f"{host} is on the deny list")

    allowed = {h.lower() for h in settings.tool_allowed_hosts}
    if allowed and not (host in allowed or any(host.endswith(f".{a}") for a in allowed)):
        raise UnsafeUrl(f"{host} is not on this deployment's allow list")

    for address in await _resolve(host):
        if not _is_public(address):
            # Do not echo the address back: on a blind SSRF probe the error
            # message itself is the oracle the attacker is after.
            raise UnsafeUrl(f"{host} resolves to a non-public address")

    return url


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text. A full parser would be better, but the model only
    needs readable prose and this cannot execute anything it reads."""

    _SKIP = frozenset({"script", "style", "noscript", "svg", "head"})
    _BREAK = frozenset({"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        # The title is checked before the skip guard: <title> lives inside
        # <head>, which is skipped wholesale, so the guard would swallow it.
        if self._in_title and self.title is None:
            self.title = data.strip()
            return
        if self._skip_depth:
            return
        self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        return re.sub(r"\n\s*\n\s*\n+", "\n\n", joined).strip()


def html_to_text(html: str) -> tuple[str, str | None]:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text(), parser.title


class WebFetch(Tool):
    name = "web_fetch"
    description = (
        "Fetch a public web page or API response and return it as text. Use it to "
        "read a specific URL you already know. HTML is converted to readable text."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http or https URL."},
            "max_chars": {
                "type": "integer",
                "description": "Truncate the response to this many characters (default 20000).",
            },
        },
        "required": ["url"],
    }
    side_effect = SideEffect.NETWORK
    timeout_seconds = 25.0

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        url = require_str(arguments, "url", max_length=2000)
        max_chars = optional_int(arguments, "max_chars", default=20_000, low=500, high=100_000)

        current = url
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": get_settings().tool_user_agent},
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                # Re-validated on every hop: a public host redirecting into the
                # private range is the standard SSRF bypass.
                await assert_safe_url(current)
                try:
                    response = await client.get(current)
                except httpx.HTTPError as exc:
                    raise ToolError(f"request failed: {type(exc).__name__}") from exc

                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ToolError("the server returned a redirect with no location")
                    current = str(response.url.join(location))
                    continue
                break
            else:
                raise ToolError(f"too many redirects (limit {MAX_REDIRECTS})")

        if response.status_code >= 400:
            return ToolResult(
                content=f"HTTP {response.status_code} from {current}",
                is_error=True,
                metadata={"status": response.status_code, "url": current},
            )

        raw = response.content[:MAX_RESPONSE_BYTES]
        content_type = response.headers.get("content-type", "")
        body = raw.decode(response.encoding or "utf-8", errors="replace")

        title = None
        if "html" in content_type:
            body, title = html_to_text(body)

        header = f"# {title}\n\n" if title else ""
        return ToolResult(
            content=f"{header}Source: {current}\n\n{body[:max_chars]}",
            metadata={
                "url": current,
                "status": response.status_code,
                "content_type": content_type,
                "title": title,
            },
        )


class WebSearch(Tool):
    """Search the public web.

    Registered only when a search provider is configured — an unusable tool in
    the schema is worse than a missing one, because the model will keep trying
    it and burning steps on the failure.
    """

    name = "web_search"
    description = (
        "Search the public web and return ranked results with snippets. Use it "
        "when the answer depends on current information you do not already have; "
        "follow up with web_fetch to read a result in full."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "count": {
                "type": "integer",
                "description": "How many results to return (1-10, default 5).",
            },
        },
        "required": ["query"],
    }
    side_effect = SideEffect.NETWORK
    timeout_seconds = 20.0

    async def run(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        query = require_str(arguments, "query", max_length=500)
        count = optional_int(arguments, "count", default=5, low=1, high=10)

        settings = get_settings()
        if settings.search_provider == "brave":
            results = await _brave_search(query, count, settings.brave_search_api_key or "")
        elif settings.search_provider == "tavily":
            results = await _tavily_search(query, count, settings.tavily_api_key or "")
        else:
            raise ToolError("no search provider is configured on this deployment")

        if not results:
            return ToolResult(content=f"No results for {query!r}.", metadata={"count": 0})

        rendered = "\n\n".join(
            f"{index + 1}. {item['title']}\n   {item['url']}\n   {item['snippet']}"
            for index, item in enumerate(results)
        )
        return ToolResult(
            content=rendered,
            metadata={"count": len(results), "provider": settings.search_provider},
        )


async def _brave_search(query: str, count: int, api_key: str) -> list[dict[str, str]]:
    if not api_key:
        raise ToolError("the Brave search key is not configured")
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
        try:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"search request failed: {type(exc).__name__}") from exc

    payload = response.json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": _strip_tags(item.get("description", "")),
        }
        for item in (payload.get("web", {}).get("results") or [])[:count]
    ]


async def _tavily_search(query: str, count: int, api_key: str) -> list[dict[str, str]]:
    if not api_key:
        raise ToolError("the Tavily key is not configured")
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        try:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": count},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"search request failed: {type(exc).__name__}") from exc

    payload = response.json()
    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:400],
        }
        for item in (payload.get("results") or [])[:count]
    ]


_TAG = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _TAG.sub("", text).strip()
