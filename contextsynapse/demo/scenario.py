"""Demo scenario: auth-service project seed data."""
from typing import List, Dict, Any

from contextsynapse.project.project_context import ProjectContext
from contextsynapse.ingestion.sdlc_ingest import ingest_sdlc_nodes

# Static scenario definition — all nodes and edges that seed the project.
AUTH_SCENARIO: Dict[str, List[Dict[str, Any]]] = {
    "description": "Authentication service for a web application",
    "task": (
        "Design and implement a login endpoint for our authentication service. "
        "The endpoint should validate credentials, enforce the rate-limiting policy, "
        "and return a session token. Write any new SDLC artifacts you create back "
        "to the context graph."
    ),
    "nodes": [
        # intent layer — 3 requirements
        {
            "id": "req:login",
            "label": "Requirement",
            "properties": {
                "content": (
                    "Users must authenticate with email and password. "
                    "Failed attempts must be rate-limited to 5 per minute per IP."
                ),
                "version": 1,
            },
        },
        {
            "id": "req:logout",
            "label": "Requirement",
            "properties": {
                "content": (
                    "Users must be able to invalidate their session at any time. "
                    "All tokens issued for the user must be revocable."
                ),
                "version": 1,
            },
        },
        {
            "id": "req:session-timeout",
            "label": "Requirement",
            "properties": {
                "content": "Sessions must expire after 24 hours of inactivity.",
                "version": 1,
            },
        },
        # design layer — 2 arch decisions
        {
            "id": "arch:jwt",
            "label": "ArchDecision",
            "properties": {
                "decision": "Use JWT (JSON Web Tokens) for session tokens.",
                "rationale": (
                    "Stateless — no server-side session store required. "
                    "Works across microservices. Standard library support in all languages."
                ),
                "version": 1,
            },
        },
        {
            "id": "arch:bcrypt",
            "label": "ArchDecision",
            "properties": {
                "decision": "Use bcrypt with work factor 12 for password hashing.",
                "rationale": (
                    "Industry standard. Configurable work factor lets us increase cost "
                    "as hardware improves. Resistant to rainbow table attacks."
                ),
                "version": 1,
            },
        },
        # build layer — 1 code module
        {
            "id": "code:user-service",
            "label": "CodeModule",
            "properties": {
                "summary": (
                    "Handles user CRUD operations and password management. "
                    "Exposes /users REST endpoints. Uses bcrypt internally."
                ),
                "version": 1,
            },
        },
        # verify layer — 2 test cases
        {
            "id": "tc:login-success",
            "label": "TestCase",
            "properties": {
                "what": "Valid credentials return 200 with a JWT token.",
                "how": (
                    "POST /auth/login with valid email + password. "
                    "Assert HTTP 200, response body contains 'token', "
                    "token is a valid JWT signed with the server key."
                ),
                "version": 1,
            },
        },
        {
            "id": "tc:login-invalid",
            "label": "TestCase",
            "properties": {
                "what": "Invalid credentials return 401 Unauthorized.",
                "how": (
                    "POST /auth/login with wrong password. "
                    "Assert HTTP 401, no token in response body."
                ),
                "version": 1,
            },
        },
    ],
    "edges": [
        # TestCase SATISFIES Requirement
        {
            "id": "edge:tc-login-success-satisfies-req-login",
            "label": "SATISFIES",
            "source": "tc:login-success",
            "target": "req:login",
        },
        {
            "id": "edge:tc-login-invalid-satisfies-req-login",
            "label": "SATISFIES",
            "source": "tc:login-invalid",
            "target": "req:login",
        },
        # CodeModule IMPLEMENTS Requirement
        {
            "id": "edge:user-service-implements-req-logout",
            "label": "IMPLEMENTS",
            "source": "code:user-service",
            "target": "req:logout",
        },
        # ArchDecision GOVERNS CodeModule
        {
            "id": "edge:jwt-governs-user-service",
            "label": "GOVERNS",
            "source": "arch:jwt",
            "target": "code:user-service",
        },
    ],
}


def seed_project(name: str, base_path: str) -> ProjectContext:
    """Create a project and populate it with the auth-service scenario.

    Nodes are written via ``ingest_sdlc_nodes()`` so they are BM25-indexed
    and vector-embedded immediately — making ``search_sdlc()`` work from
    the first query without a separate indexing step.

    Args:
        name: project namespace name (must be unique in the Redis instance)
        base_path: directory for the projects index file

    Returns:
        ProjectContext with all scenario nodes and edges inserted and indexed.
    """
    pc = ProjectContext.create(name, base_path=base_path)
    ingest_sdlc_nodes(
        db=pc.db,
        namespace=pc.name,
        nodes=AUTH_SCENARIO["nodes"],
        edges=AUTH_SCENARIO["edges"],
    )
    return pc
