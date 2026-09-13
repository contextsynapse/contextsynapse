"""ContextCore Governance -- unified data governance layer.

Provides:
  - Access control (who can see/modify what)
  - Data lineage (where did this data come from, who transformed it)
  - Audit trail (who did what, when)
  - PII detection and masking
  - Retention policies (auto-delete after TTL)
  - Data quality scores
"""
from contextsynapse.governance.layer import GovernanceLayer, get_governance

__all__ = ["GovernanceLayer", "get_governance"]
