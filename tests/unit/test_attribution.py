from tracedeck.attribution import canonical_mcp_attribution


def test_canonical_mcp_name_is_split_without_inference():
    assert canonical_mcp_attribution("mcp__filesystem__read_file") == {
        "mcp_server": "filesystem",
        "mcp_tool": "read_file",
        "attribution_source": "hook:canonical_tool_name",
    }


def test_non_canonical_tool_names_remain_nullable():
    for name in ("Bash", "mcp__filesystem", "mcp____read_file", "mcp__server__tool__extra", None):
        assert canonical_mcp_attribution(name) == {
            "mcp_server": None,
            "mcp_tool": None,
            "attribution_source": None,
        }
