"""Tests for Bugzilla MCP tools."""

from unittest.mock import MagicMock

import pytest
from fastmcp.client import Client
from fastmcp.client.transports import FastMCPTransport


@pytest.fixture
async def mcp_client():
    """Create an MCP client for testing."""
    from bugbug_mcp.server import mcp

    async with Client(mcp) as client:
        yield client


def setup_bugzilla_mock(mocker, bugs):
    """Helper to setup Bugzilla mock with given bugs."""
    mock_bugzilla_class = mocker.patch("libmozdata.bugzilla.Bugzilla")
    mock_instance = MagicMock()

    # Store bugs to be returned
    mock_instance._bugs = bugs

    # When Bugzilla is initialized, capture the bughandler
    def mock_init(params, include_fields, bughandler):
        mock_instance._bughandler = bughandler
        return mock_instance

    # When get_data().wait() is called, invoke the handler with bugs
    def mock_get_data():
        for bug in mock_instance._bugs:
            mock_instance._bughandler(bug)
        return mock_instance

    mock_instance.get_data = mock_get_data
    mock_instance.wait = MagicMock()
    mock_bugzilla_class.side_effect = mock_init

    return mock_bugzilla_class


class TestBugzillaQuickSearch:
    """Test the bugzilla_quick_search tool."""

    async def test_quick_search_basic(
        self, mocker, mcp_client: Client[FastMCPTransport]
    ):
        """Test basic quick search functionality."""
        mock_bugs = [
            {
                "id": 123456,
                "status": "NEW",
                "summary": "Test bug 1",
                "product": "Firefox",
                "component": "General",
                "priority": "P1",
                "severity": "S2",
            },
            {
                "id": 789012,
                "status": "ASSIGNED",
                "summary": "Test bug 2",
                "product": "Core",
                "component": "DOM",
                "priority": "P2",
                "severity": "S3",
            },
        ]

        mock_bugzilla = setup_bugzilla_mock(mocker, mock_bugs)

        result = await mcp_client.call_tool(
            name="bugzilla_quick_search",
            arguments={"search_query": "firefox crash", "limit": 2},
        )

        # Verify API call
        mock_bugzilla.assert_called_once()
        call_args = mock_bugzilla.call_args[0][0]
        assert call_args["quicksearch"] == "firefox crash"
        assert call_args["limit"] == 2

        # Verify result
        result_text = result.content[0].text
        assert "Found 2 bug(s)" in result_text
        assert "Bug 123456 [NEW]" in result_text
        assert "Bug 789012 [ASSIGNED]" in result_text
        assert "Test bug 1" in result_text
        assert "Firefox::General" in result_text
        assert "Core::DOM" in result_text

    async def test_quick_search_no_results(
        self, mocker, mcp_client: Client[FastMCPTransport]
    ):
        """Test quick search with no results."""
        setup_bugzilla_mock(mocker, [])

        result = await mcp_client.call_tool(
            name="bugzilla_quick_search",
            arguments={"search_query": "nonexistent query"},
        )

        result_text = result.content[0].text
        assert "No bugs found matching: nonexistent query" in result_text

    async def test_quick_search_custom_limit(
        self, mocker, mcp_client: Client[FastMCPTransport]
    ):
        """Test quick search with custom limit."""
        mock_bugzilla = setup_bugzilla_mock(mocker, [])

        await mcp_client.call_tool(
            name="bugzilla_quick_search",
            arguments={"search_query": "test", "limit": 50},
        )

        call_args = mock_bugzilla.call_args[0][0]
        assert call_args["limit"] == 50

    async def test_quick_search_handles_missing_fields(
        self, mocker, mcp_client: Client[FastMCPTransport]
    ):
        """Test that missing fields are handled gracefully."""
        mock_bugs = [{"id": 123456, "summary": "Test bug"}]
        setup_bugzilla_mock(mocker, mock_bugs)

        result = await mcp_client.call_tool(
            name="bugzilla_quick_search", arguments={"search_query": "test"}
        )

        result_text = result.content[0].text
        assert "Bug 123456" in result_text
        assert "Test bug" in result_text
        assert "N/A" in result_text
