"""End-to-end test: schema extraction block + turn chain + operator registry."""
import pytest
import uuid
from unittest.mock import MagicMock

from contextcore.ingestion.universal.ingest_content import IngestContent, Chunk
from contextcore.ingestion.universal.stage_executor import StageExecutor, GraphContext
from contextcore.ingestion.universal.parsers.turn_parser import TurnParser
from contextcore.ingestion.universal._operator_registry import resolve_operators
from contextcore.schema.sdl import EnhancedSchema, ExtractionConfig
from contextcore.schema.compiler import SchemaCompiler

CONV_TEXT = (
    "User: I need to set up Kubernetes on AWS EKS.\n"
    "Assistant: You should use eksctl for the simplest setup. "
    "We decided to use eksctl over manual CloudFormation.\n"
    "User: What about monitoring?\n"
    "Assistant: Use Prometheus with Grafana dashboards. "
    "The fix for the auth error is to install aws-iam-authenticator.\n"
    "User: How do I deploy my React app?\n"
    "Assistant: Containerize with Docker, push to ECR."
)


class TestConversationE2E:
    def test_full_pipeline(self):
        parser = TurnParser()
        session_meta, turns = parser.parse(CONV_TEXT)
        assert len(turns) == 6
        chunks = parser.to_chunks(turns)

        schema = EnhancedSchema(
            name="test_conv",
            extraction=ExtractionConfig(
                strategy="conversation",
                parser="turn",
                stages=["detect_signals", "cluster_topics", "link_cross_reference"],
                signals=["decision", "question", "problem", "solution"],
                topic_method="keyword_only",
            ),
        )
        compiled = SchemaCompiler.compile(schema)

        db = MagicMock()
        db.add_node = MagicMock(return_value=None)
        db.add_edge = MagicMock(return_value=None)
        db.name = "test_conv_e2e"

        content = IngestContent(content_type="conversation", text=CONV_TEXT, title="K8s Setup")
        operators = resolve_operators(compiled.execution_plan, compiled)
        executor = StageExecutor(
            operators=operators, db=db, namespace="test", compiled_schema=compiled,
        )
        result = executor.execute(content, chunks)

        assert result is not None
        assert result.edge_count > 0
        # Session + turns + signals should create nodes
        assert len(result.entity_ids) > 0

    def test_chatgpt_json_input(self):
        data = {
            "title": "Docker Help",
            "mapping": {
                "a": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["How to use Docker?"]},
                        "create_time": 1000,
                    },
                },
                "b": {
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["Install Docker Desktop."]},
                        "create_time": 1001,
                    },
                },
            },
        }
        parser = TurnParser()
        session, turns = parser.parse(data)
        assert session.source == "chatgpt"
        chunks = parser.to_chunks(turns)
        assert chunks[0].chunk_type == "turn"

    def test_backward_compat_no_extraction_block(self):
        raw = {
            "name": "legacy",
            "node_types": {
                "Person": {"fields": ["name"], "required": ["name"]},
            },
        }
        schema = EnhancedSchema.from_dict(raw)
        assert schema.extraction is None
        compiled = SchemaCompiler.compile(schema)
        # Schemas with node_types but no extraction block get a default execution
        # plan automatically so they benefit from the universal pipeline.
        assert compiled.execution_plan is not None
        assert compiled.execution_plan.parser == "text"
