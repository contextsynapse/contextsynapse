"""
Billing Router
==============
Billing and subscription management endpoints.

GET   /billing/plan      — Current plan + usage vs limits
GET   /billing/plans     — All available plans
POST  /billing/checkout  — Create Stripe Checkout session (upgrade)
POST  /billing/portal    — Create Stripe Customer Portal session
POST  /billing/webhook   — Stripe webhook handler
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)


def create_billing_router(
    user_registry,
    tenant_registry,
    billing_manager,
    usage_meter,
) -> APIRouter:
    """Create the /billing router with injected dependencies."""

    from .auth import UserAuth
    router = APIRouter(prefix="/billing", tags=["billing"])
    user_auth = UserAuth(user_registry)

    def _get_tenant(user):
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="No workspace found")
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return tenant

    # ------------------------------------------------------------------
    # GET /billing/plans — public (no auth)
    # ------------------------------------------------------------------
    @router.get("/plans")
    async def list_plans():
        """List all available plans with pricing and limits."""
        from .billing import PLANS
        return {
            "plans": [
                {"id": plan_id, **plan_data}
                for plan_id, plan_data in PLANS.items()
            ]
        }

    # ------------------------------------------------------------------
    # GET /billing/plan — current plan + usage
    # ------------------------------------------------------------------
    @router.get("/plan")
    async def current_plan(user=Depends(user_auth)):
        """Get current plan with usage vs limits."""
        tenant = _get_tenant(user)
        sub = billing_manager.get_or_create(tenant.tenant_id)
        limits = billing_manager.get_limits(tenant.tenant_id)
        usage = usage_meter.get_summary(tenant.tenant_id)

        # Count real resource usage
        graph_count = 0
        agent_count = 0
        try:
            from .api import graph_registry as _gr, _agent_reg as _ar
            if _gr:
                graph_count = len(_gr.list_graphs())
            if _ar:
                agent_count = len(_ar.list_agents())
        except Exception:
            pass

        return {
            "subscription": sub.to_dict(),
            "limits": limits,
            "usage": {
                "api_calls": {"used": usage.get("api_call", 0), "limit": limits["max_api_calls"]},
                "queries": {"used": usage.get("query", 0), "limit": -1},
                "searches": {"used": usage.get("search", 0), "limit": -1},
                "ingestions": {"used": usage.get("ingest", 0), "limit": -1},
                "graphs": {"used": graph_count, "limit": limits["max_graphs"]},
                "agents": {"used": agent_count, "limit": limits["max_agents"]},
                "nodes": {"used": usage.get("node_create", 0), "limit": limits["max_nodes"]},
            },
            "stripe_enabled": billing_manager.stripe_enabled,
        }

    # ------------------------------------------------------------------
    # POST /billing/checkout — Stripe Checkout session
    # ------------------------------------------------------------------
    @router.post("/checkout")
    async def create_checkout(user=Depends(user_auth)):
        """Create a Stripe Checkout session for upgrading to Pro."""
        if not billing_manager.stripe_enabled:
            raise HTTPException(
                status_code=400,
                detail="Billing is not configured (self-hosted mode). All features are unlocked.",
            )

        try:
            import stripe
            stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
        except (ImportError, KeyError):
            raise HTTPException(status_code=503, detail="Stripe not configured")

        tenant = _get_tenant(user)
        sub = billing_manager.get_or_create(tenant.tenant_id)

        # Get or create Stripe customer
        if sub.stripe_customer_id:
            customer_id = sub.stripe_customer_id
        else:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.display_name,
                metadata={"tenant_id": tenant.tenant_id, "user_id": user.user_id},
            )
            customer_id = customer.id

        price_id = os.environ.get("STRIPE_PRO_PRICE_ID")
        if not price_id:
            raise HTTPException(status_code=503, detail="Pro plan price not configured")

        base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{base_url}/dashboard/billing?upgraded=true",
            cancel_url=f"{base_url}/dashboard/billing",
            metadata={"tenant_id": tenant.tenant_id},
        )

        return {"checkout_url": session.url}

    # ------------------------------------------------------------------
    # POST /billing/portal — Stripe Customer Portal
    # ------------------------------------------------------------------
    @router.post("/portal")
    async def create_portal(user=Depends(user_auth)):
        """Create a Stripe Customer Portal session for managing subscription."""
        if not billing_manager.stripe_enabled:
            raise HTTPException(status_code=400, detail="Billing not configured (self-hosted mode)")

        try:
            import stripe
            stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
        except (ImportError, KeyError):
            raise HTTPException(status_code=503, detail="Stripe not configured")

        tenant = _get_tenant(user)
        sub = billing_manager.get(tenant.tenant_id)

        if not sub or not sub.stripe_customer_id:
            raise HTTPException(status_code=400, detail="No active subscription to manage")

        base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")

        session = stripe.billing_portal.Session.create(
            customer=sub.stripe_customer_id,
            return_url=f"{base_url}/dashboard/billing",
        )

        return {"portal_url": session.url}

    # ------------------------------------------------------------------
    # POST /billing/webhook — Stripe webhook
    # ------------------------------------------------------------------
    @router.post("/webhook")
    async def stripe_webhook(request: Request):
        """Handle Stripe webhook events."""
        webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
        if not webhook_secret:
            raise HTTPException(status_code=503, detail="Webhook not configured")

        payload = await request.body()
        sig_header = request.headers.get("stripe-signature", "")

        try:
            import stripe
            stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ImportError:
            raise HTTPException(status_code=503, detail="Stripe not installed")
        except Exception as e:
            logger.warning(f"Webhook signature verification failed: {e}")
            raise HTTPException(status_code=400, detail="Invalid signature")

        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "checkout.session.completed":
            tenant_id = data.get("metadata", {}).get("tenant_id")
            if tenant_id:
                customer_id = data.get("customer")
                sub_id = data.get("subscription")
                billing_manager.upgrade(
                    tenant_id, "pro",
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=sub_id,
                )
                logger.info(f"Tenant {tenant_id} upgraded to pro via checkout")

        elif event_type == "customer.subscription.updated":
            _handle_subscription_update(data)

        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(data)

        elif event_type == "invoice.payment_failed":
            _handle_payment_failed(data)

        return {"received": True}

    def _handle_subscription_update(data):
        """Handle subscription changes (plan switches, renewals)."""
        stripe_sub_id = data.get("id")
        status = data.get("status")
        # Find tenant by stripe subscription ID
        row = billing_manager._conn.execute(
            "SELECT tenant_id FROM subscriptions WHERE stripe_subscription_id = ?",
            (stripe_sub_id,),
        ).fetchone()
        if row:
            tenant_id = row["tenant_id"]
            if status == "active":
                period_end = data.get("current_period_end")
                if period_end:
                    from datetime import datetime, timezone
                    period_end = datetime.fromtimestamp(period_end, tz=timezone.utc).isoformat()
                billing_manager._conn.execute(
                    "UPDATE subscriptions SET status = 'active', current_period_end = ? WHERE tenant_id = ?",
                    (period_end, tenant_id),
                )
                billing_manager._conn.commit()

    def _handle_subscription_deleted(data):
        """Handle subscription cancellation — downgrade to free."""
        stripe_sub_id = data.get("id")
        row = billing_manager._conn.execute(
            "SELECT tenant_id FROM subscriptions WHERE stripe_subscription_id = ?",
            (stripe_sub_id,),
        ).fetchone()
        if row:
            tenant_id = row["tenant_id"]
            billing_manager.upgrade(tenant_id, "free")
            billing_manager.update_status(tenant_id, "active")
            logger.info(f"Tenant {tenant_id} downgraded to free (subscription deleted)")

    def _handle_payment_failed(data):
        """Mark subscription as past_due on payment failure."""
        customer_id = data.get("customer")
        row = billing_manager._conn.execute(
            "SELECT tenant_id FROM subscriptions WHERE stripe_customer_id = ?",
            (customer_id,),
        ).fetchone()
        if row:
            billing_manager.update_status(row["tenant_id"], "past_due")
            logger.warning(f"Payment failed for tenant {row['tenant_id']}")

    return router
