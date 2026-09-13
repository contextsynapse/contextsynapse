"""
LMDB-backed graph storage with CSR-compatible API.

Uses msgpack for fast serialization. Supports node CRUD with an in-memory
type index rebuilt on startup. Edges are stored but not yet queried by this
module — the edge sub-database is reserved for future use.

Environment variable:
    AICONTEXTDB_LMDB_MAP_SIZE — map size in bytes (default: 2 GB)
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

try:
    import lmdb
    import msgpack
    LMDB_AVAILABLE = True
except ImportError:
    LMDB_AVAILABLE = False

# 2 GB default map size
_DEFAULT_MAP_SIZE = int(os.environ.get("CONTEXTSYNAPSE_LMDB_MAP_SIZE") or os.environ.get("AICONTEXTDB_LMDB_MAP_SIZE", 2 * 1024 ** 3))

# Sub-database names (bytes)
_DB_NODES = b"nodes"
_DB_EDGES = b"edges"
_DB_EDGE_IDX = b"edge_idx"
_DB_META = b"meta"


@dataclass
class LMDBNode:
    id: str
    node_type: str
    properties: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.node_type


class LMDBGraphStorage:
    """
    Persistent graph node store backed by LMDB.

    Sub-databases:
        nodes    — node_id (bytes) → msgpack({t, p})
        edges    — edge_id (bytes) → msgpack edge record (future)
        edge_idx — composite index for adjacency lookups (future)
        meta     — store metadata (future)
    """

    def __init__(
        self,
        path: str,
        map_size: Optional[int] = None,
        namespace: str = "",
    ) -> None:
        if not LMDB_AVAILABLE:
            raise RuntimeError(
                "lmdb and msgpack are required for LMDBGraphStorage. "
                "Install them with: pip install lmdb msgpack"
            )

        self._path = path
        self._namespace = namespace
        self._map_size = map_size if map_size is not None else _DEFAULT_MAP_SIZE

        os.makedirs(path, exist_ok=True)

        self._env = lmdb.open(
            path,
            map_size=self._map_size,
            max_dbs=4,
            writemap=True,
            metasync=False,
            sync=False,
        )

        # Open sub-databases
        with self._env.begin(write=True) as txn:
            self._db_nodes = self._env.open_db(_DB_NODES, txn=txn)
            self._db_edges = self._env.open_db(_DB_EDGES, txn=txn)
            self._db_edge_idx = self._env.open_db(_DB_EDGE_IDX, txn=txn)
            self._db_meta = self._env.open_db(_DB_META, txn=txn)

        # In-memory type index: type_str -> set of node_ids
        self._type_index: Dict[str, set] = {}
        self._build_type_index()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _encode_key(self, node_id: str) -> bytes:
        return node_id.encode("utf-8")

    def _pack_node(self, node_type: str, properties: Dict[str, Any]) -> bytes:
        return msgpack.packb({"t": node_type, "p": properties}, use_bin_type=True)

    def _unpack_node(self, node_id: str, raw: bytes) -> LMDBNode:
        data = msgpack.unpackb(raw, raw=False)
        return LMDBNode(
            id=node_id,
            node_type=data["t"],
            properties=dict(data.get("p") or {}),
        )

    def _build_type_index(self) -> None:
        """Scan all nodes on startup and populate the in-memory type index."""
        self._type_index = {}
        with self._env.begin(db=self._db_nodes) as txn:
            cursor = txn.cursor()
            for raw_key, raw_val in cursor.iternext_dup() if False else cursor:
                node_id = raw_key.decode("utf-8")
                data = msgpack.unpackb(raw_val, raw=False)
                ntype = data["t"]
                self._type_index.setdefault(ntype, set()).add(node_id)

    def _index_add(self, node_type: str, node_id: str) -> None:
        self._type_index.setdefault(node_type, set()).add(node_id)

    def _index_remove(self, node_type: str, node_id: str) -> None:
        if node_type in self._type_index:
            self._type_index[node_type].discard(node_id)
            if not self._type_index[node_type]:
                del self._type_index[node_type]

    def _check_resize(self) -> None:
        """Auto-resize the map if usage exceeds 80% of the current map size."""
        info = self._env.info()
        stat = self._env.stat()
        # info() exposes last_pgno and map_size; stat() exposes psize (page size)
        page_size = stat.get("psize", 4096)
        used = info["last_pgno"] * page_size
        capacity = info.get("map_size", self._map_size)
        if capacity > 0 and used / capacity > 0.8:
            new_size = capacity * 2
            self._map_size = new_size
            self._env.set_mapsize(new_size)

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        node_type: str,
        properties: Optional[Dict[str, Any]] = None,
        alias: Optional[str] = None,
    ) -> bool:
        """
        Add a node. Returns False (without modifying storage) if the ID
        already exists. Returns True on success.
        """
        self._check_resize()
        key = self._encode_key(node_id)
        props = properties or {}

        with self._env.begin(write=True, db=self._db_nodes) as txn:
            existing = txn.get(key)
            if existing is not None:
                return False
            txn.put(key, self._pack_node(node_type, props))

        self._index_add(node_type, node_id)
        return True

    def get_node(self, node_id: str) -> Optional[LMDBNode]:
        """
        Retrieve a node by ID. Returns a copy (mutations do not affect storage).
        Returns None if not found.
        """
        key = self._encode_key(node_id)
        with self._env.begin(db=self._db_nodes) as txn:
            raw = txn.get(key)
            if raw is None:
                return None
            node = self._unpack_node(node_id, bytes(raw))  # copy out of mmap
        # Return a deep copy so callers cannot mutate storage state via the object
        return copy.deepcopy(node)

    def update_node_properties(self, node_id: str, updates: Dict[str, Any]) -> None:
        """
        Merge *updates* into the existing node's properties (read-modify-write
        in a single transaction). No-op if the node does not exist.
        """
        key = self._encode_key(node_id)
        with self._env.begin(write=True, db=self._db_nodes) as txn:
            raw = txn.get(key)
            if raw is None:
                return
            data = msgpack.unpackb(bytes(raw), raw=False)
            props = dict(data.get("p") or {})
            props.update(updates)
            txn.put(key, self._pack_node(data["t"], props))

    def delete_node(self, node_id: str) -> bool:
        """
        Delete a node and its connected edges.
        Returns True if the node existed and was deleted, False otherwise.
        """
        key = self._encode_key(node_id)
        with self._env.begin(write=True, db=self._db_nodes) as txn:
            raw = txn.get(key)
            if raw is None:
                return False
            data = msgpack.unpackb(bytes(raw), raw=False)
            node_type = data["t"]
            txn.delete(key)

        self._index_remove(node_type, node_id)
        self._delete_edges_for_node(node_id)
        return True

    def _delete_edges_for_node(self, node_id: str) -> None:
        """Delete all edges where source or target matches *node_id*."""
        prefix_as_source = (node_id + "\x00").encode("utf-8")
        to_delete: List[bytes] = []

        with self._env.begin(db=self._db_edges) as txn:
            cursor = txn.cursor()
            if cursor.first():
                while True:
                    raw_key = bytes(cursor.key())
                    parts = raw_key.split(b"\x00")
                    # parts: [source, target, label]
                    if len(parts) == 3:
                        src = parts[0].decode("utf-8")
                        tgt = parts[1].decode("utf-8")
                        if src == node_id or tgt == node_id:
                            to_delete.append(raw_key)
                    if not cursor.next():
                        break

        if to_delete:
            with self._env.begin(write=True, db=self._db_edges) as txn:
                for k in to_delete:
                    txn.delete(k)

    # ------------------------------------------------------------------
    # Edge CRUD
    # ------------------------------------------------------------------

    def _encode_edge_key(self, source_id: str, target_id: str, label: str) -> bytes:
        """Composite edge key: source\x00target\x00label (all utf-8)."""
        return "\x00".join([source_id, target_id, label]).encode("utf-8")

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Add a directed edge from *source_id* to *target_id* with *label*.
        Both nodes must already exist; returns False otherwise.
        Returns True on success.
        """
        # Verify both nodes exist
        src_key = self._encode_key(source_id)
        tgt_key = self._encode_key(target_id)
        with self._env.begin(db=self._db_nodes) as txn:
            if txn.get(src_key) is None or txn.get(tgt_key) is None:
                return False

        edge_key = self._encode_edge_key(source_id, target_id, label)
        packed = msgpack.packb({"p": properties or {}}, use_bin_type=True)
        with self._env.begin(write=True, db=self._db_edges) as txn:
            txn.put(edge_key, packed)
        return True

    def get_edges(self, node_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Return a list of edge dicts.  Each dict has keys:
            source, target, label, properties
        If *node_id* is given, only edges where source OR target equals
        *node_id* are returned.  If None, all edges are returned.
        """
        results: List[Dict[str, Any]] = []
        with self._env.begin(db=self._db_edges) as txn:
            cursor = txn.cursor()
            if not cursor.first():
                return results
            while True:
                raw_key = bytes(cursor.key())
                raw_val = bytes(cursor.value())
                parts = raw_key.split(b"\x00", 2)
                if len(parts) == 3:
                    src = parts[0].decode("utf-8")
                    tgt = parts[1].decode("utf-8")
                    lbl = parts[2].decode("utf-8")
                    if node_id is None or src == node_id or tgt == node_id:
                        data = msgpack.unpackb(raw_val, raw=False)
                        results.append({
                            "source": src,
                            "target": tgt,
                            "label": lbl,
                            "properties": dict(data.get("p") or {}),
                        })
                if not cursor.next():
                    break
        return results

    def get_edge_count(self) -> int:
        """Return the number of edges via LMDB stat (O(1))."""
        with self._env.begin(db=self._db_edges) as txn:
            return txn.stat()["entries"]

    def remove_edge(self, source_id: str, target_id: str, label: str) -> bool:
        """
        Delete the edge identified by the composite key.
        Returns True if deleted, False if not found.
        """
        edge_key = self._encode_edge_key(source_id, target_id, label)
        with self._env.begin(write=True, db=self._db_edges) as txn:
            existing = txn.get(edge_key)
            if existing is None:
                return False
            txn.delete(edge_key)
        return True

    def get_all_nodes(self) -> Iterator[LMDBNode]:
        """
        Lazy cursor scan over all nodes. Yields one LMDBNode at a time.
        Each yielded node is a copy (safe for mutation by callers).
        """
        with self._env.begin(db=self._db_nodes) as txn:
            cursor = txn.cursor()
            for raw_key, raw_val in cursor:
                node_id = raw_key.decode("utf-8")
                node = self._unpack_node(node_id, bytes(raw_val))
                yield copy.deepcopy(node)

    def get_node_count(self) -> int:
        """Return the number of nodes via LMDB stat (O(1))."""
        with self._env.begin(db=self._db_nodes) as txn:
            return txn.stat()["entries"]

    # ------------------------------------------------------------------
    # Stats & lifecycle
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return a summary dict about the store."""
        info = self._env.info()
        return {
            "namespace": self._namespace,
            "path": self._path,
            "node_count": self.get_node_count(),
            "map_size": self._map_size,
            "last_pgno": info.get("last_pgno"),
            "page_size": info.get("page_size"),
            "type_index": {k: len(v) for k, v in self._type_index.items()},
        }

    def close(self) -> None:
        """Flush and close the LMDB environment."""
        self._env.sync()
        self._env.close()
