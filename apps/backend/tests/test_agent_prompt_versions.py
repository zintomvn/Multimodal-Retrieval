from __future__ import annotations

from app.modules.retrieval.query_planning import AgentQueryPlanner


def test_planner_selects_configured_prompt_version_and_reports_it() -> None:
    planner = AgentQueryPlanner({
        "llm_query_planning": {
            "enabled": True,
            "active_profile": "test",
            "profiles": {"test": {"provider": "openai", "model": "test-model"}},
            "prompt_versions": {
                "active_version": "agent_v2",
                "versions": {
                    "agent_v1": {"description": "control"},
                    "agent_v2": {"planner_append": "Use only observable evidence."},
                },
            },
            "agents": {"planner": {"system_prompt": "Return JSON."}},
        }
    })

    assert planner._planner_system_prompt() == "Return JSON.\n\nUse only observable evidence."
    assert planner._agent_metadata()["active_prompt_version"] == "agent_v2"


def test_agent_v1_and_fresh_agent_v2_start_with_the_same_prompt() -> None:
    config = {
        "llm_query_planning": {
            "prompt_versions": {
                "active_version": "agent_v1",
                "versions": {"agent_v1": {"planner_append": ""}, "agent_v2": {"planner_append": ""}},
            },
            "agents": {"planner": {"system_prompt": "Shared full prompt."}},
        }
    }
    v1 = AgentQueryPlanner(config)
    v1_prompt = v1._planner_system_prompt()
    config["llm_query_planning"]["prompt_versions"]["active_version"] = "agent_v2"
    v2 = AgentQueryPlanner(config)

    assert v1_prompt == v2._planner_system_prompt() == "Shared full prompt."


def test_planner_reads_the_full_prompt_from_the_selected_version_file(tmp_path) -> None:
    prompt_file = tmp_path / "agent_v2.md"
    prompt_file.write_text("Complete V2 prompt.", encoding="utf-8")
    planner = AgentQueryPlanner({
        "llm_query_planning": {
            "prompt_versions": {
                "active_version": "agent_v2",
                "versions": {
                    "agent_v2": {"planner_prompt_file": "agent_v2.md"},
                },
            },
            "agents": {"planner": {"system_prompt": "Legacy prompt."}},
        }
    }, config_path=tmp_path / "agent.yaml")

    assert planner._planner_system_prompt() == "Complete V2 prompt."


def test_planner_falls_back_to_legacy_prompt_for_unknown_version() -> None:
    planner = AgentQueryPlanner({
        "llm_query_planning": {
            "prompt_versions": {"active_version": "missing", "versions": {}},
            "agents": {"planner": {"system_prompt": "Legacy planner prompt."}},
        }
    })

    assert planner._active_prompt_version() == "legacy"
    assert planner._planner_system_prompt() == "Legacy planner prompt."
