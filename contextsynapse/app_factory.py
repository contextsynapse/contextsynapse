"""App Factory — creates a FastAPI app with platform capabilities.

Verticals call create_app() and register their own routers.

Usage (in PMS vertical):
    from contextsynapse.app_factory import create_app

    app = create_app()
    app.register_vertical("pms", pms_router, register_fn)
    # or
    app = create_app(verticals={"pms": {"router": pms_router, "register": register_fn}})
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def create_app(
    title: str = "ContextSynapse",
    version: str = "1.0.0",
    verticals: dict[str, dict] = None,
    cors_origins: list[str] = None,
    enable_audit: bool = True,
    enable_rate_limit: bool = True,
) -> FastAPI:
    """Create a FastAPI app with platform capabilities.

    Args:
        title: App title
        version: App version
        verticals: {"pms": {"register": fn, "routers": [router1, ...]}}
        cors_origins: CORS allowed origins
        enable_audit: Enable audit log middleware
        enable_rate_limit: Enable per-user rate limiting
    """
    app = FastAPI(title=title, version=version, docs_url="/docs", redoc_url="/redoc")

    # Audit middleware
    if enable_audit:
        try:
            from contextsynapse.security.audit_middleware import AuditMiddleware
            app.add_middleware(AuditMiddleware)
            logger.info("Audit middleware enabled")
        except Exception as e:
            logger.debug("Audit middleware skipped: %s", e)

    # Per-user rate limiting
    if enable_rate_limit:
        try:
            from contextsynapse.security.rate_limit import UserRateLimitMiddleware
            app.add_middleware(UserRateLimitMiddleware)
            logger.info("Rate limiting enabled")
        except Exception as e:
            logger.debug("Rate limiting skipped: %s", e)

    # CORS
    origins = cors_origins or [
        o.strip() for o in
        os.environ.get("CONTEXTSYNAPSE_CORS_ORIGINS",
                        os.environ.get("AICONTEXTDB_CORS_ORIGINS", "http://localhost:3000")).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Admin-Key"],
    )

    # Error monitoring
    try:
        from contextsynapse.security.error_monitor import init_error_monitoring
        init_error_monitoring()
    except Exception:
        pass

    # Platform routers
    _mount_platform_routers(app)

    # Register verticals
    if verticals:
        for name, config in verticals.items():
            register_fn = config.get("register")
            if register_fn:
                try:
                    register_fn()
                    logger.info("Vertical '%s' registered", name)
                except Exception as e:
                    logger.warning("Vertical '%s' registration failed: %s", name, e)

            for router in config.get("routers", []):
                app.include_router(router)
                logger.info("Vertical '%s' router mounted", name)

    return app


def _mount_platform_routers(app: FastAPI):
    """Mount platform-level routers (auth, context, skills, stocks, workflow)."""
    graph_registry = None
    try:
        from contextsynapse.core.registry import GraphRegistry
        graph_registry = GraphRegistry()
    except Exception:
        pass

    # Auth
    try:
        from contextsynapse.api.auth_router import create_auth_router
        from contextsynapse.api.users import UserRegistry
        from contextsynapse.api.tenants import TenantRegistry
        user_reg = UserRegistry()
        tenant_reg = TenantRegistry()
        try:
            user_reg.ensure_admin()
        except Exception:
            pass
        app.include_router(create_auth_router(user_reg, tenant_reg))
        logger.info("Auth router mounted")
    except Exception as e:
        logger.debug("Auth router skipped: %s", e)

    # Context assembly
    try:
        from contextsynapse.api.assembly_router import create_assembly_router
        app.include_router(create_assembly_router(graph_registry=graph_registry))
    except Exception:
        pass

    # Workflow
    try:
        from contextsynapse.api.workflow_router import router as workflow_router
        app.include_router(workflow_router)
    except Exception:
        pass

    # Skills
    try:
        from contextsynapse.api.skills_router import create_skills_router
        app.include_router(create_skills_router(graph_registry=graph_registry))
    except Exception:
        pass

    # Stock master
    try:
        from contextsynapse.api.stock_master_router import router as stock_router
        app.include_router(stock_router)
    except Exception:
        pass

    # Tenant management + blueprints
    try:
        from contextsynapse.api.tenant_router import router as tenant_router
        app.include_router(tenant_router)
        logger.info("Tenant router mounted")
    except Exception:
        pass

    # Health
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        return {"status": "ready"}
