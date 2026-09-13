"""Tests for task generation, entity linking, and enhanced task processing."""

import json
import pytest


class TestTaskEntityLinking:
    def test_tasks_linked_to_matching_entities(self):
        from contextcore.project.code_context import _link_tasks_to_entities

        tasks = [
            {"node_id": "task_1", "title": "Build auth module", "description": "JWT authentication", "files": ["src/auth.py"]},
        ]
        entities = {
            "Component:auth": "ent_auth",
            "APIEndpoint:/api/login": "ent_login",
        }

        links = _link_tasks_to_entities(tasks, entities)
        assert len(links) >= 1
        assert ("task_1", "ent_auth", "IMPLEMENTS") in links

    def test_no_links_when_no_match(self):
        from contextcore.project.code_context import _link_tasks_to_entities

        tasks = [{"node_id": "task_1", "title": "Setup CI/CD", "description": "GitHub Actions", "files": []}]
        entities = {"Component:auth": "ent_auth"}

        links = _link_tasks_to_entities(tasks, entities)
        assert len(links) == 0

    def test_relates_to_for_non_component_entities(self):
        from contextcore.project.code_context import _link_tasks_to_entities

        tasks = [{"node_id": "task_1", "title": "Handle payment risk", "description": "Risk assessment", "files": []}]
        entities = {"Risk:payment": "ent_risk"}

        links = _link_tasks_to_entities(tasks, entities)
        assert len(links) >= 1
        assert links[0][2] == "RELATES_TO"  # Risk is not Component/Feature, so RELATES_TO


# ==================================================================
# TestEnhancedTaskPrompt
# ==================================================================

class TestEnhancedTaskPrompt:
    """Tests for _build_task_prompt output."""

    def test_prompt_requests_acceptance_criteria(self):
        from contextcore.project.code_context import _build_task_prompt

        prompt = _build_task_prompt(
            name="my-app",
            spec="Build a task app",
            stack={"backend": "Python"},
            rules=["use pytest"],
            requirements_text="",
        )
        assert "acceptance_criteria" in prompt
        assert "complexity" in prompt
        assert "files" in prompt

    def test_prompt_includes_project_info(self):
        from contextcore.project.code_context import _build_task_prompt

        prompt = _build_task_prompt(
            name="demo",
            spec="A demo spec",
            stack={"backend": "Go"},
            rules=["lint everything"],
            requirements_text="",
        )
        assert "demo" in prompt
        assert "Go" in prompt
        assert "lint everything" in prompt

    def test_prompt_includes_requirements_text(self):
        from contextcore.project.code_context import _build_task_prompt

        prompt = _build_task_prompt(
            name="proj",
            spec="spec",
            stack={},
            rules=[],
            requirements_text="Must support OAuth2",
        )
        assert "Must support OAuth2" in prompt

    def test_prompt_includes_json_schema(self):
        from contextcore.project.code_context import _build_task_prompt

        prompt = _build_task_prompt(
            name="proj",
            spec="spec",
            stack={},
            rules=[],
            requirements_text="",
        )
        assert "test_task" in prompt
        assert "depends_on" in prompt


# ==================================================================
# TestTaskPostProcessing
# ==================================================================

class TestTaskPostProcessing:
    """Tests for _generate_test_tasks."""

    def test_generates_test_tasks(self):
        from contextcore.project.code_context import _generate_test_tasks

        tasks = [
            {
                "title": "Implement auth",
                "description": "Build auth module",
                "priority": "high",
                "complexity": "medium",
                "acceptance_criteria": ["Login works", "JWT issued"],
                "files": ["src/auth.py"],
                "tags": ["backend"],
                "depends_on": [],
                "test_task": True,
            },
            {
                "title": "Build API",
                "description": "REST endpoints",
                "priority": "medium",
                "complexity": "medium",
                "acceptance_criteria": ["CRUD works"],
                "files": ["src/api.py"],
                "tags": ["backend"],
                "depends_on": ["Implement auth"],
                "test_task": True,
            },
        ]
        result = _generate_test_tasks(tasks)
        assert len(result) == 4  # 2 feature + 2 test

    def test_test_task_depends_on_feature(self):
        from contextcore.project.code_context import _generate_test_tasks

        tasks = [
            {
                "title": "Implement auth",
                "description": "Build auth",
                "priority": "high",
                "complexity": "medium",
                "acceptance_criteria": [],
                "files": ["src/auth.py"],
                "tags": ["backend"],
                "depends_on": [],
                "test_task": True,
            },
        ]
        result = _generate_test_tasks(tasks)
        test_tasks = [t for t in result if t["title"].startswith("Test:")]
        assert len(test_tasks) == 1
        assert "Implement auth" in test_tasks[0]["depends_on"]

    def test_no_duplicate_test_for_test_task(self):
        """A task that is already a test task should not spawn another test."""
        from contextcore.project.code_context import _generate_test_tasks

        tasks = [
            {
                "title": "Test: Implement auth",
                "description": "Tests for auth",
                "priority": "medium",
                "complexity": "low",
                "acceptance_criteria": [],
                "files": ["tests/test_auth.py"],
                "tags": ["testing"],
                "depends_on": ["Implement auth"],
                "test_task": True,
            },
        ]
        result = _generate_test_tasks(tasks)
        assert len(result) == 1  # no new test task created

    def test_test_task_files_mapped(self):
        from contextcore.project.code_context import _generate_test_tasks

        tasks = [
            {
                "title": "Build models",
                "description": "Models",
                "priority": "medium",
                "complexity": "low",
                "acceptance_criteria": [],
                "files": ["src/models.py", "src/schema.py"],
                "tags": ["backend"],
                "depends_on": [],
                "test_task": True,
            },
        ]
        result = _generate_test_tasks(tasks)
        test_tasks = [t for t in result if t["title"].startswith("Test:")]
        assert "tests/test_models.py" in test_tasks[0]["files"]
        assert "tests/test_schema.py" in test_tasks[0]["files"]

    def test_no_test_when_flag_false(self):
        from contextcore.project.code_context import _generate_test_tasks

        tasks = [
            {
                "title": "Write docs",
                "description": "Documentation",
                "priority": "low",
                "complexity": "low",
                "acceptance_criteria": [],
                "files": [],
                "tags": ["docs"],
                "depends_on": [],
                "test_task": False,
            },
        ]
        result = _generate_test_tasks(tasks)
        assert len(result) == 1


# ==================================================================
# TestSubTaskDecomposition
# ==================================================================

class TestSubTaskDecomposition:
    """Tests for _decompose_large_tasks."""

    def test_large_task_gets_subtasks(self):
        from contextcore.project.code_context import _decompose_large_tasks

        tasks = [
            {
                "title": "Build entire auth system",
                "description": "Full auth",
                "priority": "high",
                "complexity": "high",
                "acceptance_criteria": [
                    "User registration works",
                    "Login returns JWT",
                    "Password reset via email",
                ],
                "files": ["src/auth.py"],
                "tags": ["backend", "auth"],
                "depends_on": [],
                "test_task": False,
            },
        ]
        result = _decompose_large_tasks(tasks)
        # Parent + 3 subtasks
        assert len(result) >= 4
        # Parent becomes meta
        parent = [t for t in result if t["title"] == "Build entire auth system"][0]
        assert parent["complexity"] == "low"
        assert "meta" in parent["tags"]

    def test_subtasks_chain_dependencies(self):
        from contextcore.project.code_context import _decompose_large_tasks

        tasks = [
            {
                "title": "Big task",
                "description": "A big task",
                "priority": "high",
                "complexity": "high",
                "acceptance_criteria": ["Step A", "Step B", "Step C"],
                "files": [],
                "tags": [],
                "depends_on": [],
                "test_task": False,
            },
        ]
        result = _decompose_large_tasks(tasks)
        subtasks = [t for t in result if t["title"] != "Big task"]
        assert len(subtasks) == 3
        # First subtask depends on parent
        assert "Big task" in subtasks[0]["depends_on"]
        # Second depends on first subtask
        assert subtasks[0]["title"] in subtasks[1]["depends_on"]
        # Third depends on second subtask
        assert subtasks[1]["title"] in subtasks[2]["depends_on"]

    def test_low_complexity_unchanged(self):
        from contextcore.project.code_context import _decompose_large_tasks

        tasks = [
            {
                "title": "Small fix",
                "description": "Fix a bug",
                "priority": "low",
                "complexity": "low",
                "acceptance_criteria": ["Bug is fixed"],
                "files": ["src/fix.py"],
                "tags": ["bugfix"],
                "depends_on": [],
                "test_task": False,
            },
        ]
        result = _decompose_large_tasks(tasks)
        assert len(result) == 1
        assert result[0]["title"] == "Small fix"
        assert result[0]["complexity"] == "low"

    def test_medium_complexity_unchanged(self):
        from contextcore.project.code_context import _decompose_large_tasks

        tasks = [
            {
                "title": "Medium task",
                "description": "Some work",
                "priority": "medium",
                "complexity": "medium",
                "acceptance_criteria": ["Done"],
                "files": [],
                "tags": [],
                "depends_on": [],
                "test_task": False,
            },
        ]
        result = _decompose_large_tasks(tasks)
        assert len(result) == 1


# ==================================================================
# TestGraphRequirementsSummary
# ==================================================================

class TestGraphRequirementsSummary:
    """Tests for _build_graph_requirements_summary."""

    def test_builds_summary_from_features(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        feature = MagicMock(properties={"name": "User Auth", "description": "JWT authentication", "priority": "high"})
        api = MagicMock(properties={"name": "/api/login", "method": "POST", "path": "/api/login", "description": "Login endpoint"})

        def mock_get_nodes(label=""):
            if label == "Feature": return [feature]
            if label == "APIEndpoint": return [api]
            return []

        conn.get_nodes = mock_get_nodes

        summary = _build_graph_requirements_summary(conn)
        assert "User Auth" in summary
        assert "JWT authentication" in summary
        assert "/api/login" in summary
        assert "POST" in summary

    def test_empty_graph_returns_empty(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        conn.get_nodes = MagicMock(return_value=[])

        summary = _build_graph_requirements_summary(conn)
        assert summary == ""

    def test_includes_user_stories(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        story = MagicMock(properties={"title": "Login flow", "as_a": "user", "i_want": "to log in with email"})
        conn.get_nodes = MagicMock(side_effect=lambda label="": [story] if label == "UserStory" else [])

        summary = _build_graph_requirements_summary(conn)
        assert "Login flow" in summary
        assert "As a user" in summary

    def test_includes_data_model_fields(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        model = MagicMock(properties={"name": "UserModel", "description": "User table", "fields": "id, email, name, created_at"})
        conn.get_nodes = MagicMock(side_effect=lambda label="": [model] if label == "DataModel" else [])

        summary = _build_graph_requirements_summary(conn)
        assert "UserModel" in summary
        assert "Fields:" in summary
        assert "id, email" in summary

    def test_includes_constraints_with_severity(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        constraint = MagicMock(properties={"name": "Max response time", "description": "API must respond in <200ms", "severity": "critical"})
        conn.get_nodes = MagicMock(side_effect=lambda label="": [constraint] if label == "Constraint" else [])

        summary = _build_graph_requirements_summary(conn)
        assert "Max response time" in summary
        assert "severity: critical" in summary

    def test_includes_risk_mitigation(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        risk = MagicMock(properties={"name": "Data breach", "description": "Sensitive data exposure", "mitigation": "Encrypt at rest and in transit"})
        conn.get_nodes = MagicMock(side_effect=lambda label="": [risk] if label == "Risk" else [])

        summary = _build_graph_requirements_summary(conn)
        assert "Data breach" in summary
        assert "Mitigation:" in summary
        assert "Encrypt at rest" in summary

    def test_caps_nodes_at_20_per_type(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        features = [MagicMock(properties={"name": f"Feature {i}", "description": f"Desc {i}"}) for i in range(30)]
        conn.get_nodes = MagicMock(side_effect=lambda label="": features if label == "Feature" else [])

        summary = _build_graph_requirements_summary(conn)
        assert "Feature 19" in summary
        assert "Feature 20" not in summary

    def test_handles_conn_without_get_nodes(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock(spec=[])  # no get_nodes attribute

        summary = _build_graph_requirements_summary(conn)
        assert summary == ""

    def test_priority_shown_in_brackets(self):
        from contextcore.project.code_context import _build_graph_requirements_summary
        from unittest.mock import MagicMock

        conn = MagicMock()
        feature = MagicMock(properties={"name": "Auth", "description": "Auth system", "priority": "high"})
        conn.get_nodes = MagicMock(side_effect=lambda label="": [feature] if label == "Feature" else [])

        summary = _build_graph_requirements_summary(conn)
        assert "[high]" in summary


class TestGenerateTasksFromGraph:
    """Tests for generate_tasks_from_graph convenience method."""

    def test_method_exists_on_code_context(self):
        from contextcore.project.code_context import CodeContext
        assert hasattr(CodeContext, "generate_tasks_from_graph")

    def test_graph_summary_enriches_generate_tasks(self):
        """generate_tasks should include SDLC graph summary when entities exist."""
        from contextcore.project.code_context import CodeContext, _build_graph_requirements_summary
        from unittest.mock import MagicMock, patch

        cc = CodeContext.__new__(CodeContext)
        cc.name = "test-proj"
        cc.conn = MagicMock()
        cc.ns = MagicMock()
        cc._spec_node_id = None
        cc._thread_id = None

        # Set up SDLC entities in graph
        feature = MagicMock(properties={"name": "Auth", "description": "JWT auth", "priority": "high"})
        cc.conn.get_nodes = MagicMock(side_effect=lambda label="": [feature] if label == "Feature" else [])

        cc.ns.get_metadata = MagicMock(side_effect=lambda key: {
            "spec": "Build a SaaS app",
            "stack": {"backend": "Python"},
            "rules": [],
        }.get(key))

        # Mock LLM to capture the prompt
        mock_llm = MagicMock()
        mock_llm.generate_json = MagicMock(return_value={"tasks": []})

        with patch("contextcore.llm.get_llm_client", return_value=mock_llm):
            cc.generate_tasks()

        # Verify the prompt included graph summary
        call_args = mock_llm.generate_json.call_args
        prompt = call_args.kwargs.get("prompt") or call_args[1].get("prompt") or call_args[0][0]
        assert "Auth" in prompt
        assert "JWT auth" in prompt
