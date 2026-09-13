"""DataSource — graph nodes that point to structured data in PostgreSQL.

A DataSource node is a lightweight graph node that says:
  "There is structured data about X in table Y, queryable with filter Z"

The graph is the catalog. PostgreSQL is the warehouse.
The DataSource is the pointer.

Time travel: every DataSource has a temporal_key (e.g., snapshot_month).
Queries can specify as_of to get data at any point in time.
Comparisons can diff between two time points.

Usage:
    ds = DataSourceManager(registry, db)

    # Register a data source
    ds.register("mf_holdings", table="mf_holdings",
                temporal_key="snapshot_month",
                schema={"scheme": "text", "stock": "text", "quantity": "int", ...})

    # Link to entities
    ds.link("mf_holdings", entity="tcs", edge_type="FEEDS_INTO")

    # Query (follows the pointer)
    data = ds.query("mf_holdings", filters={"stock": "TCS.NS"}, as_of="2026-08")

    # Time travel comparison
    diff = ds.diff("mf_holdings", filters={"stock": "TCS.NS"},
                   from_time="2026-05", to_time="2026-08")

    # Aggregate
    totals = ds.aggregate("mf_holdings", group_by="sector",
                          agg={"change_value_cr": "SUM"}, as_of="2026-08")
"""

import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_NAMESPACE = "_datasources"

# Allowed SQL aggregate functions (whitelist)
_ALLOWED_AGGS = {"SUM", "COUNT", "AVG", "MIN", "MAX"}


@dataclass
class DataSourceConfig:
    """Descriptor for a registered data source."""

    name: str                       # "mf_holdings"
    storage: str = "postgresql"     # "postgresql", "csv", "api"
    table: str = ""                 # PostgreSQL table name
    temporal_key: str = ""          # column for time travel ("snapshot_month", "as_of_date")
    schema: dict = field(default_factory=dict)  # {column: type} for UI display
    description: str = ""
    refresh_cron: str = ""          # when this data refreshes ("0 6 10 * *")
    row_count: int = 0
    last_refreshed: str = ""
    source_url: str = ""            # where the data comes from
    tenant_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class DataSourceManager:
    """Manages DataSource pointer nodes and follows them to query PostgreSQL.

    Parameters
    ----------
    registry : GraphRegistry or compatible
        Used to create/read graph nodes and edges in the ``_datasources``
        namespace.
    db_module : module, optional
        Module with ``execute(sql, params)`` and ``execute_one(sql, params)``
        functions.  Defaults to ``contextsynapse.db.postgres``.
    """

    def __init__(self, registry, db_module=None):
        self._registry = registry
        if db_module is None:
            from contextsynapse.db import postgres as _pg
            self._db = _pg
        else:
            self._db = db_module

        # Ensure we have a graph namespace for datasource nodes
        self._graph = self._ensure_namespace()

        # In-memory cache of registered DataSourceConfig objects
        self._configs: Dict[str, DataSourceConfig] = {}
        self._load_existing()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_namespace(self):
        """Return or create the _datasources graph."""
        try:
            g = self._registry.get_graph(_NAMESPACE, load_if_missing=True)
            if g is None:
                g = self._registry.create_graph(_NAMESPACE)
            return g
        except Exception:
            # Fallback: use default graph
            return self._registry.get_graph("default", load_if_missing=True)

    def _load_existing(self):
        """Bootstrap cache from existing graph nodes labelled DataSource."""
        if self._graph is None:
            return
        try:
            nodes = self._graph.get_nodes_by_type("DataSource")
            for n in nodes:
                props = n.get("properties", n) if isinstance(n, dict) else {}
                if isinstance(n, dict) and "properties" not in n:
                    props = n
                name = props.get("name", "")
                if not name:
                    continue
                self._configs[name] = DataSourceConfig(
                    name=name,
                    storage=props.get("storage", "postgresql"),
                    table=props.get("table", ""),
                    temporal_key=props.get("temporal_key", ""),
                    schema=props.get("schema") or {},
                    description=props.get("description", ""),
                    refresh_cron=props.get("refresh_cron", ""),
                    row_count=int(props.get("row_count", 0)),
                    last_refreshed=props.get("last_refreshed", ""),
                    source_url=props.get("source_url", ""),
                    tenant_id=props.get("tenant_id", ""),
                )
        except Exception as exc:
            log.debug("Could not load existing datasources: %s", exc)

    @staticmethod
    def _validate_table(name: str):
        """Ensure table name is safe (alphanumeric + underscores only)."""
        import re
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            raise ValueError(f"Invalid table name: {name!r}")

    def _require_source(self, name: str) -> DataSourceConfig:
        cfg = self._configs.get(name)
        if cfg is None:
            raise KeyError(f"DataSource '{name}' not registered")
        return cfg

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        table: str,
        temporal_key: str = "",
        schema: dict = None,
        description: str = "",
        refresh_cron: str = "",
        source_url: str = "",
        tenant_id: str = "",
    ) -> DataSourceConfig:
        """Register a new DataSource and create its pointer node in the graph.

        Returns the created :class:`DataSourceConfig`.
        """
        self._validate_table(table)

        cfg = DataSourceConfig(
            name=name,
            table=table,
            temporal_key=temporal_key,
            schema=schema or {},
            description=description,
            refresh_cron=refresh_cron,
            source_url=source_url,
            tenant_id=tenant_id,
        )

        # Create graph node
        node_id = f"ds_{name}"
        props = cfg.to_dict()
        if self._graph is not None:
            try:
                self._graph.add_node(node_id, label="DataSource", properties=props)
            except Exception:
                # Node may already exist — update instead
                try:
                    self._graph.update_node(node_id, properties=props)
                except Exception as exc:
                    log.warning("Failed to persist DataSource node: %s", exc)

        self._configs[name] = cfg
        log.info("Registered DataSource '%s' → table '%s'", name, table)
        return cfg

    # ------------------------------------------------------------------
    # Linking
    # ------------------------------------------------------------------

    def link(self, datasource_name: str, entity: str, edge_type: str = "FEEDS_INTO"):
        """Create an edge from a DataSource node to an entity context node."""
        self._require_source(datasource_name)
        src = f"ds_{datasource_name}"
        if self._graph is not None:
            self._graph.add_edge(
                source=src,
                target=entity,
                edge_type=edge_type,
                properties={"datasource": datasource_name},
            )
        log.info("Linked DataSource '%s' → entity '%s' (%s)", datasource_name, entity, edge_type)

    def unlink(self, datasource_name: str, entity: str):
        """Remove the edge from a DataSource node to an entity."""
        self._require_source(datasource_name)
        src = f"ds_{datasource_name}"
        if self._graph is not None:
            try:
                self._graph.remove_edge(src, entity)
            except Exception as exc:
                log.debug("unlink: %s", exc)

    # ------------------------------------------------------------------
    # Listing / getting
    # ------------------------------------------------------------------

    def list_sources(self, entity: str = None) -> List[DataSourceConfig]:
        """List all DataSources, optionally filtered to those linked to *entity*."""
        if entity is None:
            return list(self._configs.values())

        linked: List[DataSourceConfig] = []
        if self._graph is None:
            return linked

        try:
            # Look for edges targeting this entity with a datasource property
            edges = self._graph.get_edges_to(entity) if hasattr(self._graph, "get_edges_to") else []
            for e in edges:
                props = e.get("properties", {}) if isinstance(e, dict) else {}
                ds_name = props.get("datasource", "")
                if ds_name and ds_name in self._configs:
                    linked.append(self._configs[ds_name])
        except Exception:
            pass

        # Fallback: also check incoming edges by scanning configs
        if not linked:
            for cfg in self._configs.values():
                linked.append(cfg)

        return linked

    def get_source(self, name: str) -> DataSourceConfig:
        """Return a single DataSourceConfig by name."""
        return self._require_source(name)

    # ------------------------------------------------------------------
    # Query (follows the pointer → PostgreSQL)
    # ------------------------------------------------------------------

    def query(
        self,
        datasource_name: str,
        filters: Dict[str, Any] = None,
        as_of: str = None,
        limit: int = 100,
        order_by: str = None,
        tenant_id: str = None,
    ) -> List[dict]:
        """Execute a SQL query against the table the DataSource points to.

        Parameters
        ----------
        datasource_name : str
            Registered datasource name.
        filters : dict, optional
            Column=value equality filters (parameterized).
        as_of : str, optional
            Temporal snapshot value (applied to the ``temporal_key`` column).
        limit : int
            Max rows to return (default 100).
        order_by : str, optional
            Column to ORDER BY (validated against schema).
        tenant_id : str, optional
            Tenant isolation filter.  Overrides config tenant_id if provided.

        Returns
        -------
        list[dict]
            Rows from PostgreSQL as dicts.
        """
        cfg = self._require_source(datasource_name)
        self._validate_table(cfg.table)

        clauses: List[str] = []
        params: list = []

        # Tenant isolation — always applied
        tid = tenant_id or cfg.tenant_id
        if tid:
            clauses.append("tenant_id = %s")
            params.append(tid)

        # Temporal filter
        if as_of and cfg.temporal_key:
            self._validate_table(cfg.temporal_key)  # reuse for column name safety
            clauses.append(f"{cfg.temporal_key} = %s")
            params.append(as_of)

        # User filters
        if filters:
            for col, val in filters.items():
                self._validate_table(col)
                clauses.append(f"{col} = %s")
                params.append(val)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM {cfg.table} WHERE {where}"

        if order_by:
            self._validate_table(order_by)
            sql += f" ORDER BY {order_by}"

        sql += f" LIMIT %s"
        params.append(limit)

        return self._db.execute(sql, params)

    def query_for_entity(
        self,
        entity: str,
        datasource_name: str = None,
        as_of: str = None,
        tenant_id: str = None,
    ) -> Dict[str, List[dict]]:
        """Find all DataSources linked to *entity*, query each, return merged.

        Returns a dict keyed by datasource name → list of rows.
        """
        sources = self.list_sources(entity=entity)
        if datasource_name:
            sources = [s for s in sources if s.name == datasource_name]

        results: Dict[str, List[dict]] = {}
        for src in sources:
            try:
                rows = self.query(src.name, as_of=as_of, tenant_id=tenant_id)
                results[src.name] = rows
            except Exception as exc:
                log.warning("query_for_entity: %s query failed: %s", src.name, exc)
                results[src.name] = []

        return results

    # ------------------------------------------------------------------
    # Time Travel
    # ------------------------------------------------------------------

    def diff(
        self,
        datasource_name: str,
        filters: Dict[str, Any],
        from_time: str,
        to_time: str,
        key_columns: List[str] = None,
        tenant_id: str = None,
    ) -> List[dict]:
        """Compare two temporal snapshots and return the diff.

        Parameters
        ----------
        key_columns : list[str], optional
            Columns that uniquely identify a row across snapshots.
            Defaults to all filter columns.

        Returns
        -------
        list[dict]
            Each entry has ``from``, ``to``, and ``change`` sub-dicts with
            numeric deltas for every numeric column.
        """
        cfg = self._require_source(datasource_name)

        from_rows = self.query(datasource_name, filters=filters, as_of=from_time,
                               limit=10000, tenant_id=tenant_id)
        to_rows = self.query(datasource_name, filters=filters, as_of=to_time,
                             limit=10000, tenant_id=tenant_id)

        if key_columns is None:
            key_columns = list(filters.keys()) if filters else []

        def _key(row: dict) -> tuple:
            return tuple(row.get(k, "") for k in key_columns)

        from_map = {_key(r): r for r in from_rows}
        to_map = {_key(r): r for r in to_rows}

        all_keys = set(from_map.keys()) | set(to_map.keys())
        diffs: List[dict] = []

        for k in sorted(all_keys, key=str):
            fr = from_map.get(k, {})
            tr = to_map.get(k, {})

            # Build key dict
            entry: dict = {}
            for i, col in enumerate(key_columns):
                entry[col] = k[i] if i < len(k) else ""

            entry["from"] = {c: v for c, v in fr.items() if c not in key_columns and c != cfg.temporal_key}
            entry["to"] = {c: v for c, v in tr.items() if c not in key_columns and c != cfg.temporal_key}

            # Compute numeric deltas
            change: dict = {}
            all_cols = set(list(entry["from"].keys()) + list(entry["to"].keys()))
            for col in all_cols:
                fv = entry["from"].get(col)
                tv = entry["to"].get(col)
                try:
                    change[col] = (float(tv) if tv is not None else 0) - (float(fv) if fv is not None else 0)
                except (TypeError, ValueError):
                    pass  # non-numeric column
            entry["change"] = change

            diffs.append(entry)

        return diffs

    def timeline(
        self,
        datasource_name: str,
        filters: Dict[str, Any],
        time_range: List[str],
        metric: str,
        tenant_id: str = None,
    ) -> List[dict]:
        """Return a time series of a single metric across snapshots.

        Parameters
        ----------
        time_range : list[str]
            Ordered list of temporal values (e.g., ["2026-03", "2026-04", ...]).
        metric : str
            Column name to track.

        Returns
        -------
        list[dict]
            ``[{"period": "2026-03", "value": 4200000}, ...]``
        """
        cfg = self._require_source(datasource_name)
        self._validate_table(metric)

        series: List[dict] = []
        for period in time_range:
            rows = self.query(datasource_name, filters=filters, as_of=period,
                              limit=1, tenant_id=tenant_id)
            value = None
            if rows:
                raw = rows[0].get(metric)
                try:
                    value = float(raw) if raw is not None else None
                except (TypeError, ValueError):
                    value = raw
            series.append({"period": period, "value": value})

        return series

    def available_snapshots(
        self,
        datasource_name: str,
        tenant_id: str = None,
    ) -> List[str]:
        """List all distinct temporal values for a DataSource.

        Returns
        -------
        list[str]
            Sorted list of snapshot identifiers.
        """
        cfg = self._require_source(datasource_name)
        if not cfg.temporal_key:
            return []

        self._validate_table(cfg.table)
        self._validate_table(cfg.temporal_key)

        clauses: List[str] = []
        params: list = []

        tid = tenant_id or cfg.tenant_id
        if tid:
            clauses.append("tenant_id = %s")
            params.append(tid)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = (
            f"SELECT DISTINCT {cfg.temporal_key} FROM {cfg.table} "
            f"WHERE {where} ORDER BY {cfg.temporal_key}"
        )
        rows = self._db.execute(sql, params)
        return [r[cfg.temporal_key] for r in rows if cfg.temporal_key in r]

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def aggregate(
        self,
        datasource_name: str,
        group_by: str,
        agg: Dict[str, str],
        filters: Dict[str, Any] = None,
        as_of: str = None,
        tenant_id: str = None,
    ) -> List[dict]:
        """GROUP BY query through the DataSource pointer.

        Parameters
        ----------
        group_by : str
            Column to group by.
        agg : dict
            ``{column: aggregate_function}`` where function is one of
            SUM, COUNT, AVG, MIN, MAX.
        filters : dict, optional
            Equality filters.
        as_of : str, optional
            Temporal snapshot filter.

        Returns
        -------
        list[dict]
            Aggregated rows.
        """
        cfg = self._require_source(datasource_name)
        self._validate_table(cfg.table)
        self._validate_table(group_by)

        # Build SELECT columns
        select_parts = [group_by]
        for col, func in agg.items():
            self._validate_table(col)
            func_upper = func.upper()
            if func_upper not in _ALLOWED_AGGS:
                raise ValueError(f"Unsupported aggregate function: {func!r}")
            alias = f"{func_upper.lower()}_{col}"
            select_parts.append(f"{func_upper}({col}) AS {alias}")

        # Build WHERE
        clauses: List[str] = []
        params: list = []

        tid = tenant_id or cfg.tenant_id
        if tid:
            clauses.append("tenant_id = %s")
            params.append(tid)

        if as_of and cfg.temporal_key:
            self._validate_table(cfg.temporal_key)
            clauses.append(f"{cfg.temporal_key} = %s")
            params.append(as_of)

        if filters:
            for col, val in filters.items():
                self._validate_table(col)
                clauses.append(f"{col} = %s")
                params.append(val)

        where = " AND ".join(clauses) if clauses else "1=1"
        select_clause = ", ".join(select_parts)

        sql = (
            f"SELECT {select_clause} FROM {cfg.table} "
            f"WHERE {where} GROUP BY {group_by} "
            f"ORDER BY {group_by}"
        )

        return self._db.execute(sql, params)

    # ------------------------------------------------------------------
    # Enrichment (graph-side annotations)
    # ------------------------------------------------------------------

    def annotate(
        self,
        datasource_name: str,
        row_key: str,
        annotations: Dict[str, Any],
    ):
        """Add graph-side annotations to a DataSource row without touching PostgreSQL.

        Creates an Annotation node in the graph linked to the DataSource node.

        Parameters
        ----------
        row_key : str
            A human-readable key identifying the row (e.g., "hdfc_top100_tcs").
        annotations : dict
            Key-value pairs to store (e.g., ``{"fm_conviction": 0.85}``).
        """
        self._require_source(datasource_name)

        if self._graph is None:
            log.warning("No graph available for annotations")
            return

        annotation_id = f"ann_{datasource_name}_{row_key}"
        props = {
            "datasource": datasource_name,
            "row_key": row_key,
            **annotations,
        }

        try:
            self._graph.add_node(annotation_id, label="Annotation", properties=props)
        except Exception:
            try:
                self._graph.update_node(annotation_id, properties=props)
            except Exception as exc:
                log.warning("Failed to persist annotation: %s", exc)
                return

        # Link annotation to datasource node
        try:
            self._graph.add_edge(
                source=f"ds_{datasource_name}",
                target=annotation_id,
                edge_type="HAS_ANNOTATION",
                properties={"row_key": row_key},
            )
        except Exception:
            pass  # edge may already exist

        log.info("Annotated %s/%s with %d fields", datasource_name, row_key, len(annotations))
