"""
SAP Connector Ingestor — discover RFC modules, tables, and fields via SAP NW RFC SDK.

Requires the ``pyrfc`` package **and** the SAP NW RFC SDK C library.  If
either is missing the ingestor raises a clear ImportError so the rest of
the system keeps working.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .base import ConnectorIngestor, ConnectorGraphResult

logger = logging.getLogger(__name__)

_PYRFC_IMPORT_ERROR = (
    "SAP connector requires pyrfc package and SAP NW RFC SDK"
)


def _require_pyrfc():
    """Import and return the pyrfc module, or raise."""
    try:
        import pyrfc  # noqa: F811
        return pyrfc
    except ImportError:
        raise ImportError(_PYRFC_IMPORT_ERROR)


class SAPIngestor(ConnectorIngestor):
    """Ingest metadata and data from an SAP system via RFC."""

    connector_type = "sap"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _connection_params(self) -> Dict[str, str]:
        return {
            "ashost": self.config["host"],
            "sysnr": str(self.config.get("system_number", "00")),
            "client": str(self.config.get("client", "100")),
            "user": self.credentials["user"],
            "passwd": self.credentials["password"],
        }

    def _get_connection(self):
        pyrfc = _require_pyrfc()
        return pyrfc.Connection(**self._connection_params())

    # ------------------------------------------------------------------
    # ConnectorIngestor interface
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        _require_pyrfc()
        try:
            conn = self._get_connection()
            # Ping via RFC_PING
            conn.call("RFC_PING")
            conn.close()
            return {"success": True, "message": f"Connected to SAP {self.config['host']}"}
        except ImportError:
            raise
        except Exception as exc:
            return {"success": False, "message": str(exc)}

    def discover_metadata(self, context_id: str) -> ConnectorGraphResult:
        _require_pyrfc()
        result = ConnectorGraphResult()
        conn = self._get_connection()
        rfc_modules: List[str] = self.config.get("rfc_modules", [])

        # Root connector node
        conn_node, conn_edge = self._make_connector_node(
            context_id,
            name=f"SAP: {self.config['host']}",
            extra_props={
                "host": self.config["host"],
                "system_number": str(self.config.get("system_number", "00")),
                "client": str(self.config.get("client", "100")),
            },
        )
        result.nodes.append(conn_node)
        result.edges.append(conn_edge)

        try:
            # ----------------------------------------------------------
            # Discover RFC modules
            # ----------------------------------------------------------
            for module_name in rfc_modules:
                mod_node = self._node("SAPModule", {
                    "name": module_name,
                })
                result.nodes.append(mod_node)
                result.edges.append(self._edge(conn_node["id"], mod_node["id"], "HAS_MODULE"))

                # Try to discover tables associated with this module
                # by calling RFC_READ_TABLE with a short list
                try:
                    rfc_result = conn.call(
                        "RFC_READ_TABLE",
                        QUERY_TABLE=module_name,
                        ROWCOUNT=1,
                    )
                    # If the module name is itself a table, discover its fields
                    table_node = self._node("SAPTable", {
                        "name": module_name,
                    })
                    result.nodes.append(table_node)
                    result.edges.append(self._edge(mod_node["id"], table_node["id"], "HAS_TABLE"))

                    # Use DDIF_FIELDINFO_GET for detailed field metadata
                    try:
                        field_info = conn.call(
                            "DDIF_FIELDINFO_GET",
                            TABNAME=module_name,
                        )
                        for fld in field_info.get("DFIES_TAB", []):
                            field_node = self._node("SAPField", {
                                "name": fld.get("FIELDNAME", ""),
                                "label": fld.get("FIELDTEXT", fld.get("FIELDNAME", "")),
                                "type": fld.get("DATATYPE", ""),
                                "length": fld.get("LENG", 0),
                                "key": fld.get("KEYFLAG", "") == "X",
                            })
                            result.nodes.append(field_node)
                            result.edges.append(self._edge(table_node["id"], field_node["id"], "HAS_FIELD"))
                    except Exception as exc:
                        logger.debug("DDIF_FIELDINFO_GET failed for %s: %s", module_name, exc)

                        # Fallback: use FIELDS returned by RFC_READ_TABLE
                        for fld in rfc_result.get("FIELDS", []):
                            field_node = self._node("SAPField", {
                                "name": fld.get("FIELDNAME", ""),
                                "label": fld.get("FIELDTEXT", fld.get("FIELDNAME", "")),
                                "type": fld.get("TYPE", ""),
                                "length": int(fld.get("LENGTH", 0)),
                                "key": False,
                            })
                            result.nodes.append(field_node)
                            result.edges.append(self._edge(table_node["id"], field_node["id"], "HAS_FIELD"))

                except Exception as exc:
                    logger.debug("RFC_READ_TABLE failed for %s: %s", module_name, exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        result.stats = {
            "modules_discovered": len(rfc_modules),
            "nodes_created": len(result.nodes),
            "edges_created": len(result.edges),
        }
        return result

    def pull_data(self, context_id: str, options: Optional[Dict[str, Any]] = None) -> ConnectorGraphResult:
        _require_pyrfc()
        options = options or {}
        result = ConnectorGraphResult()
        conn = self._get_connection()
        rfc_modules: List[str] = self.config.get("rfc_modules", [])
        row_limit = options.get("limit", 100)

        try:
            for module_name in rfc_modules:
                try:
                    rfc_result = conn.call(
                        "RFC_READ_TABLE",
                        QUERY_TABLE=module_name,
                        ROWCOUNT=row_limit,
                        DELIMITER="|",
                    )
                except Exception as exc:
                    logger.warning("RFC_READ_TABLE failed for %s: %s", module_name, exc)
                    continue

                fields = [f.get("FIELDNAME", "") for f in rfc_result.get("FIELDS", [])]
                for row in rfc_result.get("DATA", []):
                    wa = row.get("WA", "")
                    values = wa.split("|")
                    record = dict(zip(fields, values)) if len(values) == len(fields) else {"raw": wa}

                    title = record.get("MANDT", record.get(fields[0], module_name)) if fields else module_name
                    text = json.dumps(record, indent=2, default=str)
                    result.data_items.append({
                        "title": str(title),
                        "content": text,
                        "source": f"sap://{self.config['host']}/{module_name}",
                        "metadata": {"table": module_name},
                    })
        finally:
            try:
                conn.close()
            except Exception:
                pass

        result.stats = {
            "tables_fetched": len(rfc_modules),
            "data_items": len(result.data_items),
        }
        return result
