"""Bug-fix opts into shared preparation without changing an existing config."""

import pytest
from agent_tools.firefox import FirefoxContext
from hackbot_agents.bug_fix import agent


@pytest.mark.parametrize("existing", [None, "ac_add_options --enable-js-shell\n"])
async def test_build_preparation_is_enabled_before_the_session(
    tmp_path, monkeypatch, existing
):
    ctx = FirefoxContext.from_source_repo(tmp_path)
    if existing is not None:
        ctx.mozconfig.write_text(existing)

    def stop_before_session(name, context, tools):
        assert name == "firefox"
        assert context is ctx
        assert context.bootstrap_before_build
        assert context._bootstrapped is None
        assert context._ci_build_env is None
        if existing is not None:
            assert context.mozconfig.read_text() == existing
        else:
            assert "--enable-debug" in context.mozconfig.read_text()
            assert "--enable-clang-plugin" in context.mozconfig.read_text()
            assert "--enable-js-shell" in context.mozconfig.read_text()
        raise RuntimeError("stop before model session")

    monkeypatch.setattr(agent, "build_sdk_server", stop_before_session)
    with pytest.raises(RuntimeError, match="stop before model session"):
        await agent.run_bug_fix(
            bugzilla_mcp_server={"type": "http", "url": "http://broker/bugzilla"},
            phabricator_mcp_server={"type": "http", "url": "http://broker/phabricator"},
            source_repo=tmp_path,
            fx_ctx=ctx,
            bug=1,
        )
