import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from google.adk.agents.llm_agent import Agent
from google.adk.agents.sequential_agent import SequentialAgent

import sys

# Ensure cross-namespace module registration for serialization/unpickling
if "exec_briefing_agent.agent" not in sys.modules and __name__ == "agent":
    sys.modules["exec_briefing_agent.agent"] = sys.modules["agent"]
if "agent" not in sys.modules and __name__ == "exec_briefing_agent.agent":
    sys.modules["agent"] = sys.modules["exec_briefing_agent.agent"]

# Load .env prioritizing workspace root, then package directory
_pkg_dir = Path(__file__).resolve().parent
_root_dir = _pkg_dir.parent

if (_root_dir / ".env").is_file():
    load_dotenv(dotenv_path=_root_dir / ".env", override=True)
if (_pkg_dir / ".env").is_file():
    load_dotenv(dotenv_path=_pkg_dir / ".env", override=False)
load_dotenv()

try:
    from .tools import (
        fetch_url_content,
        create_mcp_toolset,
    )
    from .utils import resolve_mcp_url
except (ImportError, ValueError):
    try:
        from exec_briefing_agent.tools import (
            fetch_url_content,
            create_mcp_toolset,
        )
        from exec_briefing_agent.utils import resolve_mcp_url
    except (ImportError, ValueError):
        from tools import (
            fetch_url_content,
            create_mcp_toolset,
        )
        from utils import resolve_mcp_url
from google.adk.tools.agent_tool import AgentTool

logger = logging.getLogger(__name__)
logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

# Configurable Model Name (Defaults to gemini-2.5-flash)
MODEL_NAME = os.getenv("SECOPS_AGENT_MODEL") or os.getenv("MODEL_NAME") or os.getenv("DEFAULT_MODEL") or "gemini-2.5-flash"

GTI_SERVICE_NAME = os.getenv("GTI_SERVICE_NAME", "mcp-gti-mcp-server")
SECOPS_SERVICE_NAME = os.getenv("SECOPS_SERVICE_NAME", "mcp-secops-mcp-server")

KEYWORD_EXTRACTOR_NAME = os.getenv("KEYWORD_EXTRACTOR_NAME", "keyword_extractor")
IOC_COLLECTOR_NAME = os.getenv("IOC_COLLECTOR_NAME", "ioc_collector")
INVESTIGATOR_NAME = os.getenv("INVESTIGATOR_NAME", "investigator")
CONSOLIDATOR_NAME = os.getenv("CONSOLIDATOR_NAME", "consolidator")
HUNTING_WORKFLOW_NAME = os.getenv("HUNTING_WORKFLOW_NAME", "hunting_workflow")
REPORTING_AGENT_NAME = os.getenv("REPORTING_AGENT_NAME", "reporting_agent")
ROOT_AGENT_NAME = os.getenv("ROOT_AGENT_NAME", "root_agent")

KEYWORD_OUTPUT_KEY = os.getenv("KEYWORD_OUTPUT_KEY", "keyword_extraction_result")
ANALYSIS_OUTPUT_KEY = os.getenv("ANALYSIS_OUTPUT_KEY", "analysis_result")
INVESTIGATION_OUTPUT_KEY = os.getenv("INVESTIGATION_OUTPUT_KEY", "investigation_result")

# Dynamically resolve MCP URLs from environment variables or Cloud Run service discovery
gti_mcp_server_url = resolve_mcp_url("GTI_MCP_URL", "GTI_SERVICE_NAME", GTI_SERVICE_NAME)
secops_mcp_server_url = resolve_mcp_url("SECOPS_MCP_URL", "SECOPS_SERVICE_NAME", SECOPS_SERVICE_NAME)


# 1. Keyword Extractor
keyword_extractor = Agent(
    model=MODEL_NAME,
    name=KEYWORD_EXTRACTOR_NAME,
    description="Extracts keywords from URL content.",
    instruction=f"""Use the `fetch_url_content` tool to read the content of the provided URL.
    Then, analyze the content to extract key keywords and a comprehensive summary of the security event.
    Output the keywords and summary.
    
    ## RULE ##
    At the end of your response, you must accurately list ONLY the tools you specifically invoked to answer the CURRENT query in this turn.
    For each tool used, you must specify:
    1. The name of the tool.
    2. The arguments used for the call.
    3. The source or MCP server it belongs to (e.g., 'Local Function').
    If you did not use any tools for this specific response, you must clearly state that you did not use any tools.""",
    tools=[fetch_url_content],
    output_key=KEYWORD_OUTPUT_KEY,
)

# 1.5 IOC Collector
ioc_collector = Agent(
    model=MODEL_NAME,
    name=IOC_COLLECTOR_NAME,
    description="Searches GTI for IOCs using keywords.",
    instruction=f"""Given the keyword extraction result from the previous step: {{{KEYWORD_OUTPUT_KEY}}}.
    Use the extracted keywords to search for related Indicators of Compromise (IOCs) in the Google Threat Intelligence platform using the `search_iocs` tool.
    Extract domains, IPs, URLs, and hashes found in the search results.
    
    Output the findings in the following JSON structure:
    {{{{
      "iocs": {{{{
        "ip": [...],
        "domain": [...],
        "hash": [...],
        "url": [...],
        "other_identifiers": [...]
      }}}},
      "summary": "Summary of the security event",
      "status": "SUCCESS"
    }}}}
    If no IOCs are found in GTI, set the "iocs" object to empty lists and set "status" to "NO_IOCS_FOUND".
    Output ONLY the JSON object.
    
    ## RULE ##
    At the end of your response, you must accurately list ONLY the tools you specifically invoked to answer the CURRENT query in this turn.
    For each tool used, you must specify:
    1. The name of the tool.
    2. The arguments used for the call.
    3. The source or MCP server it belongs to (e.g., 'GTI MCP').
    If you did not use any tools for this specific response, you must clearly state that you did not use any tools.""",
    tools=[t for t in [create_mcp_toolset(gti_mcp_server_url)] if t is not None],
    output_key=ANALYSIS_OUTPUT_KEY,
)

# 2. Investigator (SecOps only)
# Refer to the output of the previous agent using the {analysis_result} placeholder.
investigator = Agent(
    model=MODEL_NAME,
    name=INVESTIGATOR_NAME,
    description="Investigates security events using SecOps tools.",
    instruction=f"""Given the analysis result: {{{ANALYSIS_OUTPUT_KEY}}}.
    The analysis result contains a JSON string with extracted IOCs.
    If the "status" is "NO_IOCS_FOUND" or if there are no IOCs in all categories, do NOT use any tools. Simply output 'Workflow terminated: No IOCs found to investigate.'
    Otherwise, for EACH IOC listed in the "iocs" object (across all categories), you MUST use the available Google SecOps MCP tools to check for related security events, alerts, or logs in your environment.
    You MUST execute tool calls for every identified IOC. Do not just summarize without calling tools.
    Summarize the findings from SecOps for all IOCs.
    
    ## RULE ##
    At the end of your response, you must accurately list ONLY the tools you specifically invoked to answer the CURRENT query in this turn.
    For each tool used, you must specify:
    1. The name of the tool.
    2. The arguments used for the call.
    3. The source or MCP server it belongs to (e.g., 'SecOps MCP').
    If a tool name is generic like 'default_api.search', make sure to identify its source server correctly based on the context or the toolset it belongs to.
    Do not list tools used in previous turns or tools that you did not actually call for this specific response.
    If you did not use any tools for this specific response, you must clearly state that you did not use any tools.""",
    tools=[t for t in [create_mcp_toolset(secops_mcp_server_url)] if t is not None],
    output_key=INVESTIGATION_OUTPUT_KEY,
)

# 3. Consolidator
# Refer to the output of the previous agent using the {investigation_result} placeholder.
consolidator = Agent(
    model=MODEL_NAME,
    name=CONSOLIDATOR_NAME,
    description="Consolidates findings and answers Yes/No.",
    instruction=f"""Given the investigation result: {{{INVESTIGATION_OUTPUT_KEY}}}.
    If the investigation result indicates that the workflow was terminated due to no IOCs, answer:
    1. Reported internally?: N/A (No IOCs found to investigate)
    2. Key findings summary: The source page did not contain any identifiable IOCs, so internal investigation could not be performed.
    Otherwise, consolidate the findings and answer:
    1. Reported internally?: (Yes or No)
    2. Key findings summary.
    Answer Yes if any exposed assets or internal compromise was found, otherwise No.""",
)

# Bind the full flow with a sequential agent
hunting_workflow = SequentialAgent(
    name=HUNTING_WORKFLOW_NAME,
    description="Runs the full hunting workflow: extracts keywords, searches GTI for IOCs, investigates findings, and consolidates results.",
    sub_agents=[keyword_extractor, ioc_collector, investigator, consolidator],
)

reporting_agent = Agent(
    model=MODEL_NAME,
    name=REPORTING_AGENT_NAME,
    description="Generates the final Executive Threat Advisory report.",
    instruction=f"""You are the reporting agent.
    Your task is to generate a structured "EXECUTIVE THREAT ADVISORY" report based on the findings from the `{HUNTING_WORKFLOW_NAME}` tool.
    You MUST invoke the `{HUNTING_WORKFLOW_NAME}` tool with the provided URL to get the investigation results.
    Once you receive the results from `{HUNTING_WORKFLOW_NAME}`, you must map them into the following strict template and output ONLY this template. Do not output any other text before or after the template.
    
    Here is the exact structure you must follow:

    🛡️ EXECUTIVE THREAT ADVISORY

    Threat Profile: [Agent inserts threat name derived from article, e.g., "Volt Typhoon Infrastructure Target" or "Log4j Vulnerability"]

    VERDICT: [Agent outputs a color-coded, clear status: e.g., 🟢 NO IMMEDIATE IMPACT | 🔴 CRITICAL IMPACT DETECTED | 🟡 ELEVATED RISK - VULNERABILITY PRESENT]

    HIGH-LEVEL BUSINESS SUMMARY: [Agent provides a 2-3 sentence executive translation of the article. E.g., "The provided article details a newly discovered Zero-Day vulnerability affecting Microsoft SharePoint. If exploited, this allows unauthorized actors to access internal documents. Based on our rapid telemetry scan, our business operations are currently secure/compromised."]

    REASONING & INVESTIGATION FINDINGS: [Agent details the 'Why' behind the verdict using internal data]
    - (Scenario A - No Match): "I have scanned our Google SecOps environment across the last 30 days. There are no instances of the vulnerable software version running in our production environment, and zero network traffic matches for the 15 malicious IP addresses associated with this campaign."
    - (Scenario B - Match): "I queried our Google SecOps Asset Inventory and found 3 production servers running the affected software. Furthermore, telemetry from the last 24 hours confirms 5 inbound communication attempts from known malicious IPs associated with this threat actor."

    ACTION PLAN (PROACTIVE & REACTIVE STEPS): [Agent provides prescriptive guidance based on the verdict]
    - (Scenario A - Proactive): "To enhance our defensive posture, I have drafted a ticket to proactively add the newly discovered IOCs from this article into our Google SecOps blocklists. I recommend scheduling a routine scan for any dormant instances of this software."
    - (Scenario B - Reactive): "Immediate containment is required. I have surfaced the relevant SOAR playbooks to instantly isolate the 3 affected servers from the public internet. The SOC Tier 3 team has been automatically paged via Jira (Ticket #1042) to begin patching."

    FURTHER TECHNICAL DETAILS: [Agent provides a collapsible section for security leaders/analysts to drill down]
    - Source Reference: [URL analyzed]
    - Threat Actor Context (via GTI): [Information on the adversary's motives/history]
    - Analyzed IOCs: [List of IP addresses, hashes, and domains checked]
    - Internal SecOps Matches: [List of specific internal hostnames, user IDs, or log IDs affected - Or "N/A"]
    
    ## RULE ##
    You must ONLY output the completed template above. Do not add any conversational filler or extra text.""",
    tools=[AgentTool(agent=hunting_workflow)],
)

root_agent = Agent(
    model=MODEL_NAME,
    name=ROOT_AGENT_NAME,
    description="Root agent that handles user requests and decides when to run the hunting workflow.",
    instruction=f"""You are the root agent.
    Your task is to handle user requests.
    If the user provides a page URL for a security incident or vulnerability, you MUST transfer control to the `{REPORTING_AGENT_NAME}` sub-agent to process it.
    Do not attempt to analyze the URL yourself or use other tools. Just pass the URL to `{REPORTING_AGENT_NAME}`.
    If the user does not provide a URL or asks about other things, respond politely stating that you need a URL to start the investigation.""",
    sub_agents=[reporting_agent],
)
