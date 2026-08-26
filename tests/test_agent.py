import unittest
from unittest.mock import patch, MagicMock
from google.adk.cli.utils.agent_loader import AgentLoader
from google.adk.agents.llm_agent import Agent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.tools.agent_tool import AgentTool
from exec_briefing_agent.utils import get_id_token, _is_gcp_environment, _token_cache
from exec_briefing_agent.tools import (
    fetch_url_content,
    create_mcp_toolset,
    _create_mcp_http_client,
    DEFAULT_FETCH_TIMEOUT,
    DEFAULT_MCP_CONNECT_TIMEOUT,
    DEFAULT_MCP_READ_TIMEOUT,
)
from exec_briefing_agent.agent import (
    root_agent,
    reporting_agent,
    hunting_workflow,
    keyword_extractor,
    ioc_collector,
    investigator,
    consolidator,
)
from exec_briefing_agent.agent_engine_app import app


class TestAgentStructure(unittest.TestCase):
    def test_root_agent_hierarchy(self):
        self.assertEqual(root_agent.name, "root_agent")
        self.assertEqual(len(root_agent.sub_agents), 1)
        self.assertEqual(root_agent.sub_agents[0].name, "reporting_agent")

    def test_reporting_agent_tools(self):
        self.assertEqual(reporting_agent.name, "reporting_agent")
        self.assertEqual(len(reporting_agent.tools), 1)
        tool = reporting_agent.tools[0]
        self.assertIsInstance(tool, AgentTool)
        self.assertEqual(tool.agent.name, "hunting_workflow")

    def test_hunting_workflow_subagents(self):
        self.assertIsInstance(hunting_workflow, SequentialAgent)
        self.assertEqual(len(hunting_workflow.sub_agents), 4)
        sub_names = [sa.name for sa in hunting_workflow.sub_agents]
        self.assertEqual(sub_names, ["keyword_extractor", "ioc_collector", "investigator", "consolidator"])

    def test_keyword_extractor_output_key(self):
        self.assertEqual(keyword_extractor.output_key, "keyword_extraction_result")

    def test_ioc_collector_output_key(self):
        self.assertEqual(ioc_collector.output_key, "analysis_result")

    def test_investigator_output_key(self):
        self.assertEqual(investigator.output_key, "investigation_result")


class TestTools(unittest.TestCase):
    def test_fetch_url_content_html_cleaning(self):
        sample_html = b"<html><head><script>alert('test')</script><style>body{color:red}</style></head><body><h1>Security Advisory</h1><p>Vulnerability details here.</p></body></html>"
        mock_response = MagicMock()
        mock_response.read.return_value = sample_html
        mock_response.headers.get_content_charset.return_value = "utf-8"
        mock_response.__enter__.return_value = mock_response
        mock_response.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = fetch_url_content("https://example.com/advisory")
            self.assertIn("Security Advisory", result)
            self.assertIn("Vulnerability details here.", result)
            self.assertNotIn("alert('test')", result)
            self.assertNotIn("body{color:red}", result)

    def test_fetch_url_content_error_handling(self):
        result = fetch_url_content("http://invalid-url-that-does-not-exist-123456789.org")
        self.assertTrue(result.startswith("Error fetching URL:"))

    def test_create_mcp_toolset_empty_url(self):
        self.assertIsNone(create_mcp_toolset(None))
        self.assertIsNone(create_mcp_toolset(""))
        self.assertIsNone(create_mcp_toolset("   "))

    def test_create_mcp_toolset_valid_url(self):
        toolset = create_mcp_toolset("https://example-mcp.run.app/mcp")
        self.assertIsNotNone(toolset)

    def test_create_mcp_toolset_timeout_configuration(self):
        toolset = create_mcp_toolset("https://example-mcp.run.app/mcp")
        self.assertIsNotNone(toolset)
        self.assertEqual(toolset._connection_params.timeout, 30.0)
        self.assertEqual(toolset._connection_params.sse_read_timeout, 300.0)

    def test_mcp_http_client_timeout(self):
        client = _create_mcp_http_client()
        self.assertEqual(client.timeout.connect, 30.0)
        self.assertEqual(client.timeout.read, 300.0)

    def test_default_fetch_timeout(self):
        self.assertEqual(DEFAULT_FETCH_TIMEOUT, 30.0)
        self.assertEqual(DEFAULT_MCP_CONNECT_TIMEOUT, 30.0)


class TestUtils(unittest.TestCase):
    def test_get_id_token_empty(self):
        self.assertIsNone(get_id_token(""))
        self.assertIsNone(get_id_token(None))

    def test_get_id_token_caching(self):
        _token_cache["https://cached-example.com"] = ("dummy-token", 9999999999.0)
        self.assertEqual(get_id_token("https://cached-example.com/some/path"), "dummy-token")

    def test_resolve_mcp_url_from_variables(self):
        from exec_briefing_agent.utils import resolve_mcp_url
        env = {
            "PROJECT_NUMBER": "123456789012",
            "GOOGLE_CLOUD_LOCATION": "europe-west1",
            "SECOPS_SERVICE_NAME": "custom-secops-server",
        }
        with patch.dict("os.environ", env, clear=True):
            resolved = resolve_mcp_url("SECOPS_MCP_URL", "SECOPS_SERVICE_NAME", "custom-secops-server")
            self.assertEqual(resolved, "https://custom-secops-server-123456789012.europe-west1.run.app/mcp")

    def test_get_gcp_region_from_env(self):
        from exec_briefing_agent.utils import get_gcp_region
        with patch.dict("os.environ", {"GOOGLE_CLOUD_LOCATION": "asia-east1"}, clear=True):
            self.assertEqual(get_gcp_region(), "asia-east1")

    def test_get_gcp_project_number_from_env(self):
        from exec_briefing_agent.utils import get_gcp_project_number
        with patch.dict("os.environ", {"PROJECT_NUMBER": "987654321098"}, clear=True):
            self.assertEqual(get_gcp_project_number(), "987654321098")

    def test_resolve_mcp_url_direct_env(self):
        from exec_briefing_agent.utils import resolve_mcp_url
        with patch.dict("os.environ", {"CUSTOM_MCP_URL": "https://custom.run.app"}, clear=True):
            resolved = resolve_mcp_url("CUSTOM_MCP_URL")
            self.assertEqual(resolved, "https://custom.run.app/mcp")


class TestAdkIntegration(unittest.TestCase):
    def test_adk_agent_loader(self):
        loader = AgentLoader(".")
        self.assertFalse(loader.is_single_agent)
        loaded = loader.load_agent("exec_briefing_agent")
        self.assertIsNotNone(loaded)

    def test_agent_engine_app_export(self):
        self.assertIsNotNone(app)

    def test_tool_module_binding(self):
        self.assertEqual(fetch_url_content.__module__, "exec_briefing_agent.tools")

    def test_cloudpickle_serialization_roundtrip(self):
        import cloudpickle
        pickled = cloudpickle.dumps(root_agent)
        self.assertGreater(len(pickled), 0)
        unpickled = cloudpickle.loads(pickled)
        self.assertEqual(unpickled.name, "root_agent")
        self.assertEqual(len(unpickled.sub_agents), 1)


if __name__ == "__main__":
    unittest.main()
