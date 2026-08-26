import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse
import google.auth
import google.auth.transport.requests
from google.oauth2 import id_token

# Ensure cross-namespace module registration for serialization/unpickling
if "exec_briefing_agent.utils" not in sys.modules and __name__ == "utils":
    sys.modules["exec_briefing_agent.utils"] = sys.modules["utils"]
if "utils" not in sys.modules and __name__ == "exec_briefing_agent.utils":
    sys.modules["utils"] = sys.modules["exec_briefing_agent.utils"]

logger = logging.getLogger(__name__)

_token_cache: dict[str, tuple[str | None, float]] = {}


def _is_gcp_environment() -> bool:
    """Fast check to determine if running on GCP / Cloud Run / Reasoning Engine."""
    return bool(any(os.getenv(k) for k in (
        "K_SERVICE",
        "CLOUD_RUN_JOB",
        "GAE_SERVICE",
        "METADATA_SERVER_HOST",
        "GOOGLE_CLOUD_AGENT_ENGINE_ID",
        "VERTEX_AI_REASONING_ENGINE_ID",
    )))


def get_id_token(url: str) -> str | None:
    """Gets a Google ID token for the given audience (URL)."""
    if not url or not url.strip():
        return None

    # Clean and standardize audience URL to base domain (Cloud Run requires scheme://netloc)
    url_clean = str(url).strip()
    if url_clean.startswith("b'") or url_clean.startswith('b"'):
        url_clean = url_clean[2:-1]
    parsed = urlparse(url_clean)
    if parsed.scheme and parsed.netloc:
        audience = f"{parsed.scheme}://{parsed.netloc}"
    else:
        audience = url_clean

    now = time.time()
    if audience in _token_cache:
        cached_token, expiry = _token_cache[audience]
        if now < expiry and cached_token:
            return cached_token

    # 0. Direct environment variable token override if configured
    env_token = os.getenv("ID_TOKEN") or os.getenv("GOOGLE_ID_TOKEN") or os.getenv("GCP_ID_TOKEN")
    if env_token and env_token.strip():
        logger.info("Using ID token from environment variable.")
        _token_cache[audience] = (env_token.strip(), now + 3000)
        return env_token.strip()

    logger.info("Acquiring ID token for audience: %s", audience)

    # 1. Direct GCP Metadata Server query (Fastest & most reliable on Vertex AI / Cloud Run)
    if _is_gcp_environment():
        metadata_host = os.getenv("METADATA_SERVER_HOST", "metadata.google.internal")
        token_url = f"http://{metadata_host}/computeMetadata/v1/instance/service-accounts/default/identity?audience={audience}&format=full"
        try:
            req = urllib.request.Request(token_url, headers={"Metadata-Flavor": "Google"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=1.5) as resp:
                token = resp.read().decode("utf-8").strip()
                if token:
                    logger.info("Successfully acquired ID token via GCP Metadata Server.")
                    _token_cache[audience] = (token, now + 3000)
                    return token
        except Exception as e:
            logger.debug("Direct metadata server ID token request failed: %s", e)

        # 2. Google Auth Library (google.oauth2.id_token) for Service Accounts on GCP
        try:
            auth_req = google.auth.transport.requests.Request()
            token = id_token.fetch_id_token(auth_req, audience)
            if token:
                logger.info("Successfully acquired ID token via google.auth.")
                _token_cache[audience] = (token, now + 3000)
                return token
        except Exception as e:
            logger.debug("Standard fetch_id_token failed: %s", e)

    # 3. Google ADC User Credentials (exchange refresh token for ID token)
    try:
        creds, _ = google.auth.default()
        if hasattr(creds, "_refresh_token") and creds._refresh_token and hasattr(creds, "_client_id") and creds._client_id:
            token_uri = getattr(creds, "_token_uri", "https://oauth2.googleapis.com/token")
            params = {
                "client_id": creds._client_id,
                "client_secret": getattr(creds, "_client_secret", ""),
                "refresh_token": creds._refresh_token,
                "grant_type": "refresh_token",
            }
            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(
                token_uri,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=5.0) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                user_id_token = res.get("id_token")
                expires_in = res.get("expires_in", 3600)
                if user_id_token:
                    logger.info("Successfully acquired ID token via ADC OAuth refresh token.")
                    _token_cache[audience] = (user_id_token, now + max(60, expires_in - 300))
                    return user_id_token
    except Exception as e:
        logger.debug("ADC user credentials ID token exchange failed: %s", e)

    # 4. Local fallback: use gcloud CLI if available on the system
    if shutil.which("gcloud"):
        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
                capture_output=True,
                text=True,
                timeout=8,
                check=True,
            )
            token = result.stdout.strip()
            if token:
                logger.info("Successfully acquired ID token via gcloud CLI.")
                _token_cache[audience] = (token, now + 3000)
                return token
        except Exception as e:
            logger.debug("gcloud identity-token fallback failed: %s", e)

    logger.warning("Failed to acquire ID token for audience: %s", audience)
    # Cache negative result for 30 seconds
    _token_cache[audience] = (None, now + 30)
    return None



DEFAULT_GCP_REGION = os.getenv("DEFAULT_GCP_REGION", "us-central1")
DEFAULT_TOKEN_EXPIRY_SECS = int(os.getenv("DEFAULT_TOKEN_EXPIRY_SECS", "3000"))
DEFAULT_METADATA_TIMEOUT = float(os.getenv("METADATA_TIMEOUT_SECS", "1.5"))
DEFAULT_GCLOUD_TIMEOUT = float(os.getenv("GCLOUD_TIMEOUT_SECS", "8.0"))


def get_gcp_project_number() -> str | None:
    """Fetches the numeric project ID from environment variables or the GCP metadata server."""
    num = (
        os.getenv("PROJECT_NUMBER")
        or os.getenv("GOOGLE_CLOUD_PROJECT_NUMBER")
        or os.getenv("MCP_PROJECT_NUMBER")
    )
    if num and num.strip():
        return num.strip()

    if _is_gcp_environment():
        try:
            req = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/project/numeric-project-id",
                headers={"Metadata-Flavor": "Google"},
            )
            with urllib.request.urlopen(req, timeout=DEFAULT_METADATA_TIMEOUT) as resp:
                val = resp.read().decode("utf-8").strip()
                if val:
                    logger.info("Retrieved numeric project ID from GCP metadata server: %s", val)
                    return val
        except Exception as e:
            logger.debug("Failed to query metadata server for numeric-project-id: %s", e)
    return None


def get_gcp_region() -> str:
    """Fetches the GCP region from environment variables or GCP metadata server."""
    region = (
        os.getenv("GOOGLE_CLOUD_LOCATION")
        or os.getenv("GOOGLE_CLOUD_REGION")
        or os.getenv("REGION")
        or os.getenv("LOCATION")
    )
    if region and region.strip():
        return region.strip()

    if _is_gcp_environment():
        try:
            req = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/instance/region",
                headers={"Metadata-Flavor": "Google"},
            )
            with urllib.request.urlopen(req, timeout=DEFAULT_METADATA_TIMEOUT) as resp:
                val = resp.read().decode("utf-8").strip()
                if val:
                    return val.split("/")[-1]
        except Exception:
            pass
    return DEFAULT_GCP_REGION


def resolve_mcp_url(
    url_env_var: str,
    service_name_env_var: str = "",
    default_service_name: str = "",
    default_path: str = "/mcp",
) -> str | None:
    """Dynamically resolves the MCP server URL from variables (PROJECT_NUMBER, SERVICE_NAME, REGION), GCP metadata, or direct URL."""
    # 1. Direct URL from environment variable (if explicitly provided)
    url = os.getenv(url_env_var)
    if url and url.strip():
        clean_url = url.strip()
        parsed = urlparse(clean_url)
        # If no path specified or just root '/', append default_path (/mcp)
        if not parsed.path or parsed.path == "/":
            clean_url = f"{clean_url.rstrip('/')}{default_path}"
        return clean_url

    # 2. Dynamic construction from variables or GCP metadata server: SERVICE_NAME + PROJECT_NUMBER + REGION
    service_name = (os.getenv(service_name_env_var) if service_name_env_var else None) or default_service_name
    project_number = get_gcp_project_number()
    region = get_gcp_region()

    if service_name and project_number and region:
        clean_service = service_name.strip()
        clean_num = project_number.strip()
        clean_region = region.strip()
        constructed_url = f"https://{clean_service}-{clean_num}.{clean_region}.run.app{default_path}"
        logger.info("Constructed dynamic MCP URL for %s from project metadata: %s", service_name, constructed_url)
        return constructed_url

    # 3. Dynamic discovery via gcloud if available (local development)
    project_id = (
        os.getenv("MCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("PROJECT_ID")
    )
    if service_name and project_id and shutil.which("gcloud"):
        try:
            cmd = [
                "gcloud", "run", "services", "describe", service_name,
                f"--project={project_id}",
                f"--region={region}",
                "--format=value(status.url)",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_GCLOUD_TIMEOUT, check=True)
            base_url = result.stdout.strip()
            if base_url:
                logger.info("Discovered %s Cloud Run URL via gcloud: %s", service_name, base_url)
                return f"{base_url.rstrip('/')}{default_path}"
        except Exception as e:
            logger.debug("gcloud service discovery for %s skipped: %s", service_name, e)

    return None




