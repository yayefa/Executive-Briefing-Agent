import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Ensure environment variables are loaded from package or workspace root
_pkg_dir = Path(__file__).resolve().parent
_root_dir = _pkg_dir.parent

load_dotenv(dotenv_path=_pkg_dir / ".env")
load_dotenv(dotenv_path=_root_dir / ".env")
load_dotenv()

logger = logging.getLogger(__name__)


project = (
    os.getenv("GOOGLE_CLOUD_PROJECT")
    or os.getenv("GCP_PROJECT")
    or os.getenv("PROJECT_ID")
    or os.getenv("VERTEX_AI_PROJECT_ID")
)
location = (
    os.getenv("GOOGLE_CLOUD_LOCATION")
    or os.getenv("GOOGLE_CLOUD_REGION")
    or os.getenv("LOCATION")
    or "us-central1"
)

try:
    import vertexai
    if project:
        vertexai.init(project=project, location=location)
    else:
        # Provide fallback project for local serialization & testing
        vertexai.init(project="default-project", location=location)
except Exception as e:
    logger.warning("Could not initialize vertexai directly: %s", e)

try:
    from vertexai.agent_engines import AdkApp
except (ImportError, ValueError):
    try:
        from vertexai.preview.reasoning_engines import AdkApp
    except (ImportError, ValueError):
        AdkApp = None

try:
    from .agent import root_agent
except (ImportError, ValueError):
    from agent import root_agent

if AdkApp is not None:
    app = AdkApp(agent=root_agent)
else:
    app = root_agent

__all__ = ["root_agent", "app"]
