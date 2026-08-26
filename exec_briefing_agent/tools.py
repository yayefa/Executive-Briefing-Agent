import os
import urllib.request
import logging
from typing import Optional, Any
import httpx
from google.adk.tools.mcp_tool import (
    McpToolset,
    SseConnectionParams,
    StreamableHTTPConnectionParams,
)


logger = logging.getLogger(__name__)


def _fetch_id_token_for_url(url_obj: httpx.URL) -> Optional[str]:
    """Helper to get ID token for a given httpx URL."""
    try:
        from .utils import get_id_token
        audience = f"{url_obj.scheme}://{url_obj.netloc}"
        return get_id_token(audience)
    except Exception:
        try:
            from utils import get_id_token
            audience = f"{url_obj.scheme}://{url_obj.netloc}"
            return get_id_token(audience)
        except Exception:
            return None


class BearerIdTokenAuth(httpx.Auth):
    """Custom httpx.Auth that dynamically attaches and preserves Google ID tokens across redirects."""
    def auth_flow(self, request: httpx.Request):
        if request.url.host not in ("localhost", "127.0.0.1") and "authorization" not in request.headers:
            token = _fetch_id_token_for_url(request.url)
            if token:
                request.headers["Authorization"] = f"Bearer {token}"
        yield request


async def _force_https_request_hook(request: httpx.Request) -> None:
    """Normalizes MCP requests to prevent Cloud Run 302/307 redirects and dropped Auth headers.
    
    1. Rewrites http:// to https:// on remote endpoints (avoids 302 redirect).
    2. Normalizes /mcp/messages to /mcp/messages/ (avoids 307 redirect from FastMCP).
    3. Re-attaches Authorization Bearer token if missing or stripped across redirects.
    """
    if request.url.scheme == "http" and request.url.host not in ("localhost", "127.0.0.1"):
        request.url = request.url.copy_with(
            scheme="https",
            port=443 if request.url.port == 80 else request.url.port,
        )

    # FastMCP mounts message post endpoints with a trailing slash.
    # Normalize path so FastMCP does not return a 307 redirect.
    if request.url.path == "/mcp/messages":
        request.url = request.url.copy_with(path="/mcp/messages/")

    # Ensure Authorization header is present on all remote GCP requests
    if request.url.host not in ("localhost", "127.0.0.1") and "authorization" not in request.headers:
        token = _fetch_id_token_for_url(request.url)
        if token:
            request.headers["Authorization"] = f"Bearer {token}"


DEFAULT_FETCH_USER_AGENT = os.getenv(
    "FETCH_URL_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
)
DEFAULT_FETCH_TIMEOUT = float(os.getenv("FETCH_URL_TIMEOUT_SECS", "15.0"))
DEFAULT_MAX_FETCH_CHARS = int(os.getenv("MAX_FETCH_URL_CHARS", "40000"))
DEFAULT_MCP_CONNECT_TIMEOUT = float(os.getenv("MCP_CONNECT_TIMEOUT_SECS", "30.0"))
DEFAULT_MCP_READ_TIMEOUT = float(os.getenv("MCP_READ_TIMEOUT_SECS", "300.0"))


def _create_mcp_http_client(
    headers: Optional[dict[str, Any]] = None,
    timeout: Optional[httpx.Timeout] = None,
    auth: Optional[httpx.Auth] = None,
) -> httpx.AsyncClient:
    """Creates a configured HTTPX client that rewrites http to https and preserves ID tokens."""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(DEFAULT_MCP_CONNECT_TIMEOUT, read=DEFAULT_MCP_READ_TIMEOUT),
        auth=auth or BearerIdTokenAuth(),
        follow_redirects=True,
        event_hooks={"request": [_force_https_request_hook]},
    )



def fetch_url_content(url: str) -> str:
    """Fetches and cleans the content of a given URL.
    
    Args:
        url: The URL to fetch.
    Returns:
        The extracted and cleaned text content of the URL as a string.
    """
    clean_url = url.strip().strip("<>\"'[]()")
    logger.info("Fetching URL content: %s", clean_url)
    try:
        req = urllib.request.Request(
            clean_url, 
            headers={"User-Agent": DEFAULT_FETCH_USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=DEFAULT_FETCH_TIMEOUT) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            raw_bytes = response.read()
            raw_text = raw_bytes.decode(charset, errors="replace")

        # Use BeautifulSoup to strip non-content tags and extract clean text
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw_text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "svg"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except Exception:
            text = raw_text

        # Truncate to reasonable limit if excessively large
        if len(text) > DEFAULT_MAX_FETCH_CHARS:
            text = text[:DEFAULT_MAX_FETCH_CHARS] + "\n\n[Content truncated due to length...]"

        # Extract CVEs or identifiers from URL path as supplementary context
        import re
        url_cves = re.findall(r"(?i)CVE-\d{4}-\d{4,7}", clean_url)
        if url_cves:
            cve_summary = ", ".join(sorted(set(c.upper() for c in url_cves)))
            text = f"[URL Reference: {clean_url}]\n[Identified CVE(s) from URL path: {cve_summary}]\n\n{text}"

        return text or f"URL Reference: {clean_url}"
    except Exception as e:
        logger.warning("Error fetching URL '%s': %s", clean_url, e)
        # Even if network fetch fails, return the URL and any CVEs extracted from the URL path
        import re
        url_cves = re.findall(r"(?i)CVE-\d{4}-\d{4,7}", clean_url)
        cve_info = f" (CVE: {', '.join(set(c.upper() for c in url_cves))})" if url_cves else ""
        return f"Error fetching URL: {e}. Source URL: {clean_url}{cve_info}"



def create_mcp_toolset(
    url: Optional[str],
    transport: Optional[str] = None,
) -> Optional[McpToolset]:
    """Creates an McpToolset connection if URL is provided.

    Supports both SSE (Server-Sent Events) and Streamable HTTP transports.
    Cloud Run MCP servers (FastMCP / MCP SDK) use SSE transport by default.
    """
    if not url or not url.strip():
        logger.warning("MCP URL is empty or None.")
        return None

    clean_url = url.strip()
    from urllib.parse import urlparse
    parsed_url = urlparse(clean_url)
    audience = f"{parsed_url.scheme}://{parsed_url.netloc}"

    def _fetch_token():
        try:
            from .utils import get_id_token
            return get_id_token(audience)
        except Exception:
            try:
                from utils import get_id_token
                return get_id_token(audience)
            except Exception:
                return None

    def dynamic_jwt_header_provider(session_state=None):
        token = _fetch_token()
        if token:
            logger.info("Attaching Authorization Bearer header for MCP audience: %s", audience)
            return {"Authorization": f"Bearer {token}"}
        logger.warning("No ID token available for MCP audience %s. Request may be rejected by Cloud Run if private.", audience)
        return {}

    # Fetch initial token so base connection_params.headers is never empty on startup/tool listing
    initial_token = _fetch_token()
    initial_headers = {"Authorization": f"Bearer {initial_token}"} if initial_token else {}

    # Determine transport type:
    # 1. explicit transport parameter
    # 2. environment variable
    # 3. URL heuristics (e.g. /streamable)
    is_streamable = (
        transport == "streamable"
        or os.getenv("MCP_TRANSPORT", "").lower() == "streamable"
        or clean_url.endswith("/streamable")
    )


    if is_streamable:
        params = StreamableHTTPConnectionParams(
            url=clean_url,
            headers=initial_headers,
            httpx_client_factory=_create_mcp_http_client,
        )
        transport_type = "Streamable HTTP"
    else:
        params = SseConnectionParams(
            url=clean_url,
            headers=initial_headers,
            httpx_client_factory=_create_mcp_http_client,
        )
        transport_type = "SSE"

    if clean_url.startswith("http://localhost") or clean_url.startswith("http://127.0.0.1"):
        logger.info(f"Connecting to local {transport_type} MCP server at {clean_url}")
        return McpToolset(connection_params=params)
    else:
        logger.info(f"Connecting to remote {transport_type} MCP server at {clean_url}")
        return McpToolset(connection_params=params, header_provider=dynamic_jwt_header_provider)




