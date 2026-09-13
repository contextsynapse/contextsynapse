"""
Benchmark Workloads
====================
Curated documents of varying complexity for benchmarking.
Each workload has text, expected entity types, and difficulty level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Workload:
    """A benchmark workload."""
    name: str
    text: str
    expected_entity_types: List[str]
    expected_min_entities: int
    difficulty: str  # "simple", "medium", "complex"
    domain: str  # "tech", "business", "legal"


WORKLOADS: List[Workload] = [
    Workload(
        name="API Specification",
        difficulty="medium",
        domain="tech",
        expected_entity_types=["Endpoint", "Method", "Parameter", "Response", "Authentication", "Error"],
        expected_min_entities=10,
        text="""
# UserService API Specification v2.1

## Overview
The UserService API provides RESTful endpoints for managing user accounts,
authentication, and profile data. Built on FastAPI with PostgreSQL backend.

## Authentication
All endpoints require Bearer token authentication via JWT.
Tokens are issued by the AuthService at POST /auth/token with client credentials.
Token TTL is 3600 seconds. Refresh tokens last 30 days.

## Endpoints

### POST /users
Create a new user account.

Request body:
- email (string, required): User's email address. Must be unique.
- password (string, required): Minimum 8 characters, one uppercase, one digit.
- display_name (string, optional): Display name. Max 100 characters.
- role (string, optional): One of "user", "admin", "moderator". Default: "user".

Response (201 Created):
- id (uuid): Unique user identifier
- email (string): User's email
- display_name (string): Display name
- role (string): Assigned role
- created_at (datetime): ISO 8601 timestamp

Errors:
- 409 Conflict: Email already exists
- 422 Validation Error: Invalid input

### GET /users/{user_id}
Retrieve user profile by ID.

Path parameters:
- user_id (uuid, required): The user's unique identifier

Response (200 OK):
- id, email, display_name, role, created_at, last_login_at

Errors:
- 404 Not Found: User does not exist
- 403 Forbidden: Insufficient permissions

### PUT /users/{user_id}
Update user profile. Only the user or admin can update.

Request body (partial update):
- display_name (string, optional)
- role (string, optional, admin only)

Response (200 OK): Updated user object

### DELETE /users/{user_id}
Soft-delete a user account. Marks as inactive, data retained for 90 days.

Response (204 No Content)

### GET /users
List users with pagination and filtering.

Query parameters:
- page (integer, default 1)
- per_page (integer, default 20, max 100)
- role (string, optional): Filter by role
- search (string, optional): Search by email or display_name
- sort_by (string, optional): "created_at", "email", "display_name"
- sort_order (string, optional): "asc" or "desc"

Response (200 OK):
- items: Array of user objects
- total: Total count
- page: Current page
- per_page: Items per page

## Rate Limiting
- Authenticated requests: 1000/minute per API key
- Unauthenticated: 60/minute per IP
- Rate limit headers: X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset

## Error Format
All errors follow RFC 7807 Problem Details:
{
    "type": "https://api.example.com/errors/not-found",
    "title": "Resource Not Found",
    "status": 404,
    "detail": "User with id '...' does not exist",
    "instance": "/users/abc-123"
}

## Dependencies
- AuthService: JWT token validation
- NotificationService: Welcome email on user creation
- AuditService: Log all mutations for compliance
""",
    ),

    Workload(
        name="Product Requirements Document",
        difficulty="complex",
        domain="business",
        expected_entity_types=["Feature", "Requirement", "User", "System", "Constraint", "Decision", "Dependency"],
        expected_min_entities=15,
        text="""
# Project Atlas — Product Requirements Document

## 1. Executive Summary
Project Atlas is a real-time collaboration platform for distributed engineering
teams. It combines document editing, code review, and task management into a
single workspace. Target launch: Q3 2026. Budget: $2.4M.

## 2. Stakeholders
- Product Owner: Sarah Chen (VP Product)
- Tech Lead: Marcus Rodriguez
- Design Lead: Aisha Patel
- Engineering: 8 full-time, 3 contractors
- Customers: Enterprise B2B, 50-5000 seat companies

## 3. Core Features

### 3.1 Real-Time Document Editor
Users can collaboratively edit markdown documents with real-time cursor
tracking and conflict resolution. Must support:
- Up to 50 concurrent editors per document
- Offline editing with automatic sync on reconnect
- Version history with diff view (last 100 versions)
- Inline code blocks with syntax highlighting (20+ languages)
- Comments and @mentions with notification delivery < 2 seconds

Decision: Use CRDT (Conflict-free Replicated Data Types) instead of OT
(Operational Transforms). Rationale: better offline support and simpler
conflict resolution at scale. Risk: Higher memory usage per document.

### 3.2 Integrated Code Review
Pull request reviews embedded directly in the workspace.
- GitHub and GitLab integration via OAuth
- Inline commenting on diffs
- Approval workflow: 1 approval required for merge (configurable per repo)
- CI status display (GitHub Actions, GitLab CI)

Dependency: Requires GitHub App registration and OAuth callback endpoint.
Blocked by: Legal review of GitHub Enterprise terms (ETA: April 15).

### 3.3 Task Management
Kanban-style task board with:
- Custom columns (default: Backlog, In Progress, Review, Done)
- Task dependencies (blocks/blocked-by relationships)
- Sprint planning with velocity tracking
- Assignee workload visualization
- Auto-assignment based on expertise tags

Constraint: Must integrate with Jira for enterprise customers who cannot
migrate. Two-way sync required (create, update, status change).

### 3.4 Search
Full-text search across documents, code, tasks, and comments.
- Response time < 200ms for 95th percentile
- Faceted results (by type, author, date range)
- Saved searches with email/Slack notifications on new results

Decision: Use Elasticsearch for search backend instead of PostgreSQL
full-text search. Rationale: Better relevance scoring, faceting, and
performance at scale. Cost: Additional $800/month infrastructure.

## 4. Non-Functional Requirements
- Availability: 99.9% uptime SLA
- Latency: API response < 100ms p95
- Security: SOC2 Type II compliance by launch
- Data residency: EU customers must have EU-only data storage
- Scalability: Support 100K concurrent users by month 6 post-launch
- Accessibility: WCAG 2.1 AA compliance

## 5. Architecture Decisions
- Frontend: React with Next.js (SSR for SEO on public docs)
- Backend: Go microservices (UserService, DocService, TaskService, SearchService)
- Database: PostgreSQL (primary), Redis (cache/pubsub), Elasticsearch (search)
- Infrastructure: AWS EKS (Kubernetes), deployed across us-east-1 and eu-west-1
- Authentication: Auth0 with SAML for enterprise SSO
- Real-time: WebSocket connections via AWS API Gateway

## 6. Risks
- CRDT library maturity: Yjs is the leading option but has known memory
  issues above 10MB documents. Mitigation: document size limit of 5MB.
- Jira sync complexity: Two-way sync with conflict resolution is notoriously
  difficult. Mitigation: Dedicate 2 engineers for 6 weeks.
- SOC2 timeline: Audit process takes 6-12 months. May not complete by launch.
  Mitigation: Start audit immediately, launch with SOC2 Type I initially.
""",
    ),

    Workload(
        name="Incident Report",
        difficulty="simple",
        domain="tech",
        expected_entity_types=["Service", "Error", "Person", "Action", "TimelineEvent", "RootCause"],
        expected_min_entities=8,
        text="""
# Incident Report: Payment Processing Outage

## Incident ID: INC-2026-0312
## Severity: P1 (Critical)
## Duration: 2026-03-10 14:23 UTC to 2026-03-10 16:47 UTC (2 hours 24 minutes)
## Impact: 100% of payment transactions failed for all customers

## Timeline
- 14:23 — PagerDuty alert: PaymentService error rate > 50%
- 14:25 — On-call engineer (David Kim) acknowledges, begins investigation
- 14:30 — Identified: PaymentService cannot connect to Stripe API
- 14:35 — Checked Stripe status page: no reported issues
- 14:42 — Found: SSL certificate for payments.example.com expired at 14:00 UTC
- 14:45 — Escalated to Platform team lead (Jennifer Wu)
- 14:50 — Jennifer discovers cert auto-renewal failed due to DNS validation error
- 15:10 — Manual certificate renewal initiated via Let's Encrypt
- 15:25 — New certificate issued and deployed to production load balancers
- 15:30 — PaymentService restarted across all 12 pods
- 15:35 — Error rate drops to 5% (retry backlog clearing)
- 16:00 — Error rate at 0%, all transactions processing normally
- 16:47 — Incident officially resolved after 45-minute monitoring window

## Root Cause
The SSL certificate for payments.example.com expired because the automated
renewal process (cert-manager v1.12) failed silently 7 days prior. The
failure was caused by a DNS provider API change (Cloudflare API v4 → v5)
that broke the DNS-01 challenge validation. No alerting was configured for
certificate expiry warnings.

## Impact Assessment
- 12,847 payment transactions failed
- Estimated revenue loss: $340,000
- 2,100 customers affected
- 847 support tickets generated
- Customer trust impact: 3 enterprise customers escalated to account team

## Action Items
1. [P0] Add certificate expiry monitoring (< 30 days = warning, < 7 days = critical) — Owner: David Kim, Due: March 15
2. [P0] Update cert-manager to v1.14 with Cloudflare API v5 support — Owner: Jennifer Wu, Due: March 13
3. [P1] Add redundant certificate renewal path (HTTP-01 as fallback) — Owner: Platform team, Due: March 22
4. [P1] Create runbook for manual certificate renewal — Owner: David Kim, Due: March 17
5. [P2] Evaluate certificate pinning removal to reduce blast radius — Owner: Security team, Due: April 1
""",
    ),
]


def get_workload(name: str) -> Workload:
    """Get a workload by name."""
    for w in WORKLOADS:
        if w.name == name:
            return w
    raise ValueError(f"Unknown workload: {name}. Available: {[w.name for w in WORKLOADS]}")


def get_all_workloads() -> List[Workload]:
    """Get all workloads."""
    return list(WORKLOADS)
