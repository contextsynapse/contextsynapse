"""Integration tests for SDLC scan pipeline registration."""
import pytest


class TestOperatorRegistry:
    def test_sdlc_scan_in_registry(self):
        """sdlc_scan is resolvable from the operator registry."""
        from contextcore.ingestion.universal._operator_registry import resolve_operators

        # ExecutionPlan-like object with stages list
        class FakePlan:
            stages = ["sdlc_scan"]
            signals = None
            significance_threshold = 0.3
            topic_method = "keyword"

        operators = resolve_operators(FakePlan())
        assert len(operators) == 1
        assert operators[0].name == "sdlc_scan"


class TestScenarioRouter:
    def test_route_software_dev(self):
        """software_dev structure routes to only SDLC_SCAN.

        BM25 and embed are handled inline inside the SDLC_SCAN stage handler,
        so they are no longer separate stages in the route.
        """
        from contextcore.ingestion.scenario_router import ScenarioRouter, STAGE_SDLC_SCAN
        from contextcore.ingestion.pipeline_context import ClassificationResult

        router = ScenarioRouter()
        classification = ClassificationResult(structure_type="software_dev")
        stages = router.route(classification, "build_graph")

        assert stages == [STAGE_SDLC_SCAN]


class TestStageExecutorHandler:
    def test_sdlc_scan_handler_registered(self):
        """StageExecutor has SDLC_SCAN in its handler dispatch table."""
        from contextcore.ingestion.stage_executor import StageExecutor
        executor = StageExecutor()
        assert "SDLC_SCAN" in executor._stage_handlers
