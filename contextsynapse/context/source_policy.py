"""
Source Policy — Control what data sources a context can ingest from.

Each context can have allowed_sources and blocked_sources in its config.
Checked before any ingestion (URL, file, connector, agent add_knowledge).

Usage:
    from contextsynapse.context.source_policy import check_source_allowed

    # Check if a URL is allowed for this context
    allowed, reason = check_source_allowed(context, source_url="https://docs.example.com/api")

    # Check if a file type is allowed
    allowed, reason = check_source_allowed(context, source_type="file", filename="spec.pdf")

Config format (stored in context.config):
    {
        "source_policy": {
            "allowed_domains": ["docs.mycompany.com", "github.com/myorg"],
            "blocked_domains": ["competitor.com"],
            "allowed_types": ["url", "file", "text"],  # or empty = all
            "blocked_types": [],
            "allowed_file_extensions": [".pdf", ".md", ".txt", ".docx"],
            "max_file_size_mb": 50,
            "require_approval": false
        }
    }

If no source_policy in config → everything is allowed (open by default).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Always blocked — security risk regardless of policy
ALWAYS_BLOCKED_DOMAINS = {
    "169.254.169.254",  # AWS metadata
    "metadata.google.internal",  # GCP metadata
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "[::]",
    "[::1]",
}

ALWAYS_BLOCKED_IP_PREFIXES = (
    "10.",       # Private
    "172.16.",   # Private
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",  # Private
    "169.254.",  # Link-local / cloud metadata
)


def check_source_allowed(
    context,
    source_url: Optional[str] = None,
    source_type: Optional[str] = None,  # "url", "file", "text", "connector"
    filename: Optional[str] = None,
    file_size_mb: Optional[float] = None,
) -> Tuple[bool, str]:
    """Check if a source is allowed for this context.

    Returns:
        (allowed: bool, reason: str)
    """
    config = context.config if hasattr(context, "config") else {}
    policy = config.get("source_policy", {})

    # Auto-derive policy from context's own URL source
    # If context has source="web:https://docs.example.com", only that domain is allowed
    ctx_source = getattr(context, "source", "") or ""
    ctx_type = getattr(context, "context_type", "") or ""
    if not policy and ctx_source.startswith("web:"):
        ctx_url = ctx_source[4:]  # strip "web:" prefix
        parsed_ctx = urlparse(ctx_url)
        if parsed_ctx.netloc:
            policy = {"allowed_domains": [parsed_ctx.netloc]}

    # Also check config.url if set directly
    ctx_config_url = config.get("url", "")
    if not policy and ctx_config_url:
        parsed_cfg = urlparse(ctx_config_url)
        if parsed_cfg.netloc:
            policy = {"allowed_domains": [parsed_cfg.netloc]}

    # No policy and no URL source = open (everything allowed, except always-blocked)
    if not policy:
        if source_url:
            blocked, reason = _check_security_blocklist(source_url)
            if blocked:
                return False, reason
        return True, "No source policy (open)"

    # Check source type
    allowed_types = policy.get("allowed_types", [])
    blocked_types = policy.get("blocked_types", [])
    if source_type:
        if blocked_types and source_type in blocked_types:
            return False, f"Source type '{source_type}' is blocked by policy"
        if allowed_types and source_type not in allowed_types:
            return False, f"Source type '{source_type}' not in allowed types: {allowed_types}"

    # Check URL domain
    if source_url:
        blocked, reason = _check_security_blocklist(source_url)
        if blocked:
            return False, reason

        parsed = urlparse(source_url)
        domain = parsed.netloc.lower()

        blocked_domains = [d.lower() for d in policy.get("blocked_domains", [])]
        allowed_domains = [d.lower() for d in policy.get("allowed_domains", [])]

        # Check blocked first
        for bd in blocked_domains:
            if domain == bd or domain.endswith("." + bd):
                return False, f"Domain '{domain}' is blocked by policy"

        # Check allowed (if set, only these are permitted)
        if allowed_domains:
            matched = False
            # Strip www. for comparison
            bare_domain = domain.removeprefix("www.")
            for ad in allowed_domains:
                bare_ad = ad.removeprefix("www.")
                if bare_domain == bare_ad or domain == ad:
                    matched = True
                    break
                if bare_domain.endswith("." + bare_ad) or domain.endswith("." + ad):
                    matched = True
                    break
                # Support path-prefix matching: "github.com/myorg"
                if "/" in ad:
                    ad_domain, ad_path = ad.split("/", 1)
                    bare_ad_domain = ad_domain.removeprefix("www.")
                    if (bare_domain == bare_ad_domain or domain == ad_domain) and parsed.path.startswith("/" + ad_path):
                        matched = True
                        break
                # Match related domains: e.g. allowed "timesofindia.com"
                # should match "timesofindia.indiatimes.com" since the
                # primary name ("timesofindia") appears as a subdomain label.
                # Skip this for path-scoped domains like "github.com/myorg".
                if "/" not in ad:
                    ad_name = bare_ad.split(".")[0]
                    if ad_name and len(ad_name) > 3 and bare_domain.startswith(ad_name + "."):
                        matched = True
                        break
            if not matched:
                return False, f"Domain '{domain}' not in allowed sources: {allowed_domains}"

    # Check file extension
    if filename:
        allowed_exts = policy.get("allowed_file_extensions", [])
        if allowed_exts:
            ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in allowed_exts:
                return False, f"File extension '{ext}' not allowed. Permitted: {allowed_exts}"

    # Check file size
    if file_size_mb is not None:
        max_size = policy.get("max_file_size_mb", 0)
        if max_size > 0 and file_size_mb > max_size:
            return False, f"File size {file_size_mb:.1f}MB exceeds limit of {max_size}MB"

    return True, "Allowed by policy"


def _check_security_blocklist(url: str) -> Tuple[bool, str]:
    """Check if URL targets a dangerous internal resource."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]  # strip port

    if host in ALWAYS_BLOCKED_DOMAINS:
        return True, f"Blocked: '{host}' is a restricted internal address"

    if any(host.startswith(prefix) for prefix in ALWAYS_BLOCKED_IP_PREFIXES):
        return True, f"Blocked: '{host}' is a private/internal IP address"

    return False, ""


def get_policy_summary(context) -> Dict[str, Any]:
    """Get a human-readable summary of the context's source policy."""
    config = context.config if hasattr(context, "config") else {}
    policy = config.get("source_policy", {})

    # Auto-derive from context URL source
    ctx_source = getattr(context, "source", "") or ""
    ctx_config_url = config.get("url", "")
    if not policy:
        url_to_check = ""
        if ctx_source.startswith("web:"):
            url_to_check = ctx_source[4:]
        elif ctx_config_url:
            url_to_check = ctx_config_url
        if url_to_check:
            parsed = urlparse(url_to_check)
            if parsed.netloc:
                return {
                    "has_policy": True,
                    "mode": "auto",
                    "source_url": url_to_check,
                    "allowed_domains": [parsed.netloc],
                    "description": f"Auto-restricted to {parsed.netloc} (from context URL)",
                }
        return {"has_policy": False, "mode": "open", "description": "All sources allowed"}

    return {
        "has_policy": True,
        "mode": "restricted",
        "allowed_domains": policy.get("allowed_domains", []),
        "blocked_domains": policy.get("blocked_domains", []),
        "allowed_types": policy.get("allowed_types", []),
        "allowed_file_extensions": policy.get("allowed_file_extensions", []),
        "max_file_size_mb": policy.get("max_file_size_mb", 0),
        "description": (
            f"Allowed: {', '.join(policy.get('allowed_domains', ['all']))}"
            if policy.get("allowed_domains")
            else "All domains allowed" + (f", blocked: {', '.join(policy.get('blocked_domains', []))}" if policy.get("blocked_domains") else "")
        ),
    }
