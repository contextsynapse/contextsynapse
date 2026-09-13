"""
SchemaComposer — compose schemas from pre-built domain modules.
================================================================

Lets users pick node types from multiple domain modules (YAML files)
and merge them with project-specific types into a single EnhancedSchema.

Usage:
    from contextsynapse.schema.composer import SchemaComposer

    composer = SchemaComposer()
    schema = composer.compose({
        "name": "insurance_claims",
        "compose": [
            {"healthcare": ["Patient", "Condition", "Treatment"]},
            {"finance": ["Claim", "Account"]},
        ],
        "node_types": {"ClaimReview": {"fields": {"reviewer": {"type": "string"}}}},
        "edge_types": {"CLAIM_FOR": {"source": "Claim", "target": "Treatment"}},
    })
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .sdl import DerivationRule, EnhancedSchema


_DEFAULT_MODULES_DIR = str(
    Path(__file__).resolve().parent.parent / "config" / "schemas"
)


class SchemaComposer:
    """Compose an EnhancedSchema from pre-built domain modules."""

    def __init__(self, modules_dir: str = ""):
        self._modules_dir = modules_dir or _DEFAULT_MODULES_DIR
        self._modules: Dict[str, EnhancedSchema] = {}
        self._raw: Dict[str, Dict[str, Any]] = {}
        self._load_modules()

    # ── public API ────────────────────────────────────────────────

    def list_modules(self) -> List[str]:
        """Return available module names."""
        return sorted(self._modules.keys())

    def list_types(self, module_name: str) -> Dict[str, List[str]]:
        """Return node and edge type names for a module."""
        schema = self._get_module(module_name)
        return {
            "node_types": sorted(schema.node_types.keys()),
            "edge_types": sorted(schema.edge_types.keys()),
        }

    def compose(self, compose_spec: Dict[str, Any]) -> EnhancedSchema:
        """Compose a schema from module selections and project-specific types.

        Parameters
        ----------
        compose_spec : dict
            Keys:
            - name (str): schema name
            - compose (list[dict]): each dict maps module_name -> [type_names]
            - node_types (dict): project-specific node type definitions
            - edge_types (dict): project-specific edge type definitions
            - derivation_rules (list): derivation rule dicts
            - context_unit (dict): context unit configuration
        """
        merged_raw: Dict[str, Any] = {
            "name": compose_spec.get("name", "composed"),
            "node_types": {},
            "edge_types": {},
            "derivation_rules": [],
        }

        selected_node_names: set = set()

        # 1. Process each compose entry — pick node types from modules
        for entry in compose_spec.get("compose", []):
            for module_name, type_names in entry.items():
                schema = self._get_module(module_name)
                raw = self._raw[module_name]

                for tname in type_names:
                    if tname not in schema.node_types:
                        available = sorted(schema.node_types.keys())
                        raise ValueError(
                            f"Type '{tname}' not found in module '{module_name}'. "
                            f"Available types: {available}"
                        )
                    # Copy raw node type definition
                    merged_raw["node_types"][tname] = raw.get("node_types", {})[tname]
                    selected_node_names.add(tname)

        # 2. Auto-include edges where BOTH source and target are selected
        for entry in compose_spec.get("compose", []):
            for module_name in entry:
                schema = self._get_module(module_name)
                raw = self._raw[module_name]
                for edge_name, edge_def in schema.edge_types.items():
                    if edge_name in merged_raw["edge_types"]:
                        continue  # already added from another module
                    if edge_def.source in selected_node_names and edge_def.target in selected_node_names:
                        merged_raw["edge_types"][edge_name] = raw.get("edge_types", {})[edge_name]

        # 3. Merge project-specific node_types (override if same name)
        for tname, tdef in compose_spec.get("node_types", {}).items():
            merged_raw["node_types"][tname] = tdef
            selected_node_names.add(tname)

        # 4. Merge project-specific edge_types
        for ename, edef in compose_spec.get("edge_types", {}).items():
            merged_raw["edge_types"][ename] = edef

        # 5. Copy derivation_rules and context_unit
        if "derivation_rules" in compose_spec:
            merged_raw["derivation_rules"] = compose_spec["derivation_rules"]

        if "context_unit" in compose_spec:
            merged_raw["context_unit"] = compose_spec["context_unit"]

        # 6. Build and return EnhancedSchema
        return EnhancedSchema.from_dict(merged_raw)

    # ── internals ─────────────────────────────────────────────────

    def _load_modules(self) -> None:
        """Load all YAML files from the modules directory."""
        modules_path = Path(self._modules_dir)
        if not modules_path.is_dir():
            return

        for fpath in sorted(modules_path.glob("*.yaml")):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                module_name = raw.get("name", fpath.stem)
                self._raw[module_name] = raw
                self._modules[module_name] = EnhancedSchema.from_dict(raw)
            except Exception:
                # Skip malformed YAML files
                continue

    def _get_module(self, name: str) -> EnhancedSchema:
        """Get a loaded module by name, raising ValueError if not found."""
        if name not in self._modules:
            available = sorted(self._modules.keys())
            raise ValueError(
                f"Module '{name}' not found. Available modules: {available}"
            )
        return self._modules[name]
