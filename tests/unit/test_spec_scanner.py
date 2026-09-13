"""Tests for spec_scanner — parse structured specs into SDLC nodes."""
import pytest

SAMPLE_SPEC = """
# Authentication Service Spec

## Requirements
- Users must authenticate with email and password
- Failed login attempts must be rate-limited to 5 per minute per IP
- Sessions must expire after 24 hours of inactivity

## Architecture Decisions
- Use JWT for stateless session tokens across microservices
- Use bcrypt with work factor 12 for password hashing

## Constraints
- All API responses must be JSON
- Max request payload: 1MB
- Token expiry: 24 hours

## Acceptance Criteria
- Valid credentials return 200 with JWT token
- Invalid credentials return 401 with no token
- Rate-limited requests return 429

## Goals
- Achieve sub-100ms login latency
- Support 10,000 concurrent sessions

## User Stories
- As a user, I want to log in with my email so I can access my account
- As an admin, I want to revoke sessions so I can enforce security policies
"""

CHATGPT_STYLE_SPEC = """
## Functional Requirements

1. The system shall validate user credentials against the database
2. The system shall issue JWT tokens upon successful authentication
3. The system shall enforce rate limiting on failed login attempts

## Non-Functional Requirements

- Response time for login must be under 200ms at p99
- The service must handle 1000 concurrent login requests

## Design Decisions

- Redis will be used for rate limiting with sliding window algorithm
- Password hashing uses Argon2id (memory-hard, resistant to GPU attacks)

## Test Plan

- Verify correct JWT structure and signature
- Verify 401 on invalid password
- Verify rate limit enforced after 5 failures
"""


class TestScanSpecDocument:
    def test_parses_requirements(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(SAMPLE_SPEC)
        reqs = [n for n in nodes if n["label"] == "Requirement"]
        assert len(reqs) == 3
        assert "email and password" in reqs[0]["properties"]["content"]

    def test_parses_arch_decisions(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(SAMPLE_SPEC)
        archs = [n for n in nodes if n["label"] == "ArchDecision"]
        assert len(archs) == 2
        assert "JWT" in archs[0]["properties"]["decision"]

    def test_parses_constraints(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(SAMPLE_SPEC)
        constraints = [n for n in nodes if n["label"] == "Constraint"]
        assert len(constraints) == 3

    def test_parses_test_cases(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(SAMPLE_SPEC)
        tcs = [n for n in nodes if n["label"] == "TestCase"]
        assert len(tcs) == 3
        assert "200" in tcs[0]["properties"]["what"]

    def test_parses_goals(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(SAMPLE_SPEC)
        goals = [n for n in nodes if n["label"] == "Goal"]
        assert len(goals) == 2

    def test_parses_user_stories(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(SAMPLE_SPEC)
        stories = [n for n in nodes if n["label"] == "UserStory"]
        assert len(stories) == 2

    def test_chatgpt_style_spec(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(CHATGPT_STYLE_SPEC, source="chatgpt")
        reqs = [n for n in nodes if n["label"] == "Requirement"]
        constraints = [n for n in nodes if n["label"] == "Constraint"]
        archs = [n for n in nodes if n["label"] == "ArchDecision"]
        tcs = [n for n in nodes if n["label"] == "TestCase"]
        assert len(reqs) == 3      # functional requirements
        assert len(constraints) == 2  # non-functional
        assert len(archs) == 2      # design decisions
        assert len(tcs) == 3        # test plan

    def test_empty_text_returns_empty(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        assert scan_spec_document("") == []
        assert scan_spec_document("   ") == []

    def test_no_headers_returns_empty(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        result = scan_spec_document("Just some plain text without any structure.")
        assert result == []

    def test_node_ids_are_deterministic(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes1 = scan_spec_document(SAMPLE_SPEC)
        nodes2 = scan_spec_document(SAMPLE_SPEC)
        ids1 = [n["id"] for n in nodes1]
        ids2 = [n["id"] for n in nodes2]
        assert ids1 == ids2

    def test_all_nodes_have_source(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        nodes = scan_spec_document(SAMPLE_SPEC, source="my-spec.md")
        for node in nodes:
            assert node["properties"]["source"] == "my-spec.md"

    def test_cap_at_50_nodes(self):
        from contextcore.ingestion.universal.operators.spec_scanner import scan_spec_document
        # Generate a huge spec with 60+ items
        huge = "## Requirements\n" + "\n".join(f"- Requirement number {i} that is long enough" for i in range(60))
        nodes = scan_spec_document(huge)
        assert len(nodes) <= 50


class TestClassifySection:
    def test_requirement_variants(self):
        from contextcore.ingestion.universal.operators.spec_scanner import _classify_section
        assert _classify_section("Requirements") == "Requirement"
        assert _classify_section("Functional Requirements") == "Requirement"
        assert _classify_section("Features") == "Requirement"

    def test_constraint_variants(self):
        from contextcore.ingestion.universal.operators.spec_scanner import _classify_section
        assert _classify_section("Constraints") == "Constraint"
        assert _classify_section("Non-Functional Requirements") == "Constraint"
        assert _classify_section("NFR") == "Constraint"

    def test_unknown_returns_empty(self):
        from contextcore.ingestion.universal.operators.spec_scanner import _classify_section
        assert _classify_section("Random Header") == ""
        assert _classify_section("Introduction") == ""
