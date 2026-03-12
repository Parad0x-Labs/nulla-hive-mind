from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import time
from typing import List

@dataclass
class DHTNode:
    peer_id: str
    ip: str
    port: int
    last_seen: float

class RoutingTable:
    def __init__(self, local_peer_id: str, k_bucket_size: int = 20, bucket_count: int = 256):
        self.local_peer_id = local_peer_id
        self.local_peer_int = self._peer_int(local_peer_id)
        self.k_bucket_size = max(1, int(k_bucket_size))
        self.bucket_count = max(16, int(bucket_count))
        self._buckets: list[OrderedDict[str, DHTNode]] = [OrderedDict() for _ in range(self.bucket_count)]
        # compatibility surface for existing callers
        self.nodes: dict[str, DHTNode] = {}
        
    def _distance(self, id1: str, id2: str) -> int:
        return self._peer_int(id1) ^ self._peer_int(id2)

    def _peer_int(self, peer_id: str) -> int:
        try:
            return int(peer_id, 16)
        except Exception:
            digest = hashlib.sha256(peer_id.encode("utf-8")).hexdigest()
            return int(digest, 16)

    def _bucket_index(self, peer_id: str) -> int | None:
        if peer_id == self.local_peer_id:
            return None
        distance = self.local_peer_int ^ self._peer_int(peer_id)
        if distance <= 0:
            return None
        idx = distance.bit_length() - 1
        if idx < 0:
            return None
        return min(idx, self.bucket_count - 1)

    def add_node(self, peer_id: str, ip: str, port: int) -> None:
        bucket_index = self._bucket_index(peer_id)
        if bucket_index is None:
            return
        now = time.time()
        bucket = self._buckets[bucket_index]

        existing = self.nodes.get(peer_id)
        if existing is not None:
            existing.ip = ip
            existing.port = int(port)
            existing.last_seen = now
            if peer_id in bucket:
                bucket.move_to_end(peer_id, last=True)
            else:
                bucket[peer_id] = existing
            return

        node = DHTNode(peer_id=peer_id, ip=ip, port=int(port), last_seen=now)
        if len(bucket) >= self.k_bucket_size:
            # Kademlia-style bounded buckets: evict oldest when saturated.
            oldest_peer_id, _ = bucket.popitem(last=False)
            self.nodes.pop(oldest_peer_id, None)
        bucket[peer_id] = node
        self.nodes[peer_id] = node

    def remove_node(self, peer_id: str) -> None:
        node = self.nodes.pop(peer_id, None)
        if node is None:
            return
        bucket_index = self._bucket_index(peer_id)
        if bucket_index is None:
            return
        self._buckets[bucket_index].pop(peer_id, None)

    def find_closest_peers(self, target_id: str, count: int = 20) -> List[DHTNode]:
        """
        Returns up to 'count' closest peers to 'target_id' according to XOR metric.
        """
        if not self.nodes:
            return []
            
        distances = []
        for node in self.nodes.values():
            dist = self._distance(target_id, node.peer_id)
            distances.append((dist, node))
            
        distances.sort(key=lambda x: x[0])
        
        # Return top N nodes
        return [item[1] for item in distances[: max(1, int(count))]]
        
    def get_all_nodes(self) -> List[DHTNode]:
        return list(self.nodes.values())

    def prune_stale_nodes(self, *, max_age_seconds: float = 3600.0) -> int:
        now = time.time()
        stale_peer_ids = [
            peer_id
            for peer_id, node in self.nodes.items()
            if (now - float(node.last_seen)) > float(max_age_seconds)
        ]
        for peer_id in stale_peer_ids:
            self.remove_node(peer_id)
        return len(stale_peer_ids)

_table: RoutingTable | None = None

def get_routing_table() -> RoutingTable:
    global _table
    if _table is None:
        from network.signer import get_local_peer_id
        _table = RoutingTable(get_local_peer_id())
    return _table
