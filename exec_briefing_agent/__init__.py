import sys

try:
    from . import tools
except (ImportError, ValueError):
    try:
        import tools
    except ImportError:
        tools = None

try:
    from . import utils
except (ImportError, ValueError):
    try:
        import utils
    except ImportError:
        utils = None

try:
    from . import agent
    from .agent import root_agent
except (ImportError, ValueError):
    try:
        from exec_briefing_agent import agent
        from exec_briefing_agent.agent import root_agent
    except (ImportError, ValueError):
        import agent
        from agent import root_agent

# Ensure cross-namespace module registration for serialization/unpickling compatibility
if "exec_briefing_agent.tools" in sys.modules and "tools" not in sys.modules:
    sys.modules["tools"] = sys.modules["exec_briefing_agent.tools"]
if "exec_briefing_agent.utils" in sys.modules and "utils" not in sys.modules:
    sys.modules["utils"] = sys.modules["exec_briefing_agent.utils"]
if "exec_briefing_agent.agent" in sys.modules and "agent" not in sys.modules:
    sys.modules["agent"] = sys.modules["exec_briefing_agent.agent"]

try:
    from .agent_engine_app import app
except Exception:
    try:
        from exec_briefing_agent.agent_engine_app import app
    except Exception:
        try:
            from agent_engine_app import app
        except Exception:
            app = root_agent

__all__ = ["agent", "tools", "utils", "root_agent", "app"]
