from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from core import audit_logger, policy_engine
from network.chunk_protocol import decode_frame
from network.transfer_manager import TransferManager
from network.stream_transport import (
    StreamClientTlsConfig,
    StreamEndpoint,
    StreamServerTlsConfig,
    StreamTransportServer,
)

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:  # pragma: no cover
    AESGCM = None  # type: ignore[assignment]


_FRAG_MAGIC = b"NFR1"
_FRAG_VERSION = 1
_FRAG_HEADER_LEN = 32
_ENC_MAGIC = b"NEN1"
_ENC_AAD = b"nulla-mesh-v1"


def _datagram_limit() -> int:
    return int(policy_engine.get("system.max_datagram_bytes", 32768))


def _fragment_datagram_limit() -> int:
    configured = int(policy_engine.get("system.max_fragment_datagram_bytes", 1400))
    return max(_FRAG_HEADER_LEN + 1, min(_datagram_limit(), configured))


def _message_limit() -> int:
    return int(policy_engine.get("system.max_message_bytes", 262144))


def _stream_threshold() -> int:
    return int(policy_engine.get("system.stream_transfer_threshold_bytes", 24576))


def _stream_enabled() -> bool:
    return bool(policy_engine.get("system.enable_stream_data_plane", True))


def _fragment_enabled() -> bool:
    return bool(policy_engine.get("system.enable_udp_fragmentation", True))


def _stream_port_for_udp_port(port: int) -> int:
    return int(port) + 1


def _frag_bucket_limit() -> int:
    return int(policy_engine.get("system.max_fragment_buckets", 2048))


def _frag_timeout_seconds() -> float:
    return float(policy_engine.get("system.fragment_timeout_seconds", 30.0))


def _mesh_psk_bytes() -> bytes | None:
    raw = os.environ.get("NULLA_MESH_PSK_B64") or str(policy_engine.get("system.mesh_psk_b64", "") or "")
    raw = raw.strip()
    if not raw:
        return None
    try:
        key = base64.b64decode(raw.encode("ascii"))
    except Exception:
        return None
    if len(key) != 32:
        return None
    return key


def _mesh_encryption_required() -> bool:
    return bool(policy_engine.get("system.require_mesh_encryption", False))


def _stream_tls_server_config() -> StreamServerTlsConfig:
    return StreamServerTlsConfig(
        enabled=bool(policy_engine.get("system.stream_tls_enabled", False)),
        certfile=str(policy_engine.get("system.stream_tls_certfile", "") or "").strip() or None,
        keyfile=str(policy_engine.get("system.stream_tls_keyfile", "") or "").strip() or None,
        ca_file=str(policy_engine.get("system.stream_tls_ca_file", "") or "").strip() or None,
        require_client_cert=bool(policy_engine.get("system.stream_tls_require_client_cert", False)),
    )


def _stream_tls_client_config() -> StreamClientTlsConfig:
    return StreamClientTlsConfig(
        enabled=bool(policy_engine.get("system.stream_tls_enabled", False)),
        ca_file=str(policy_engine.get("system.stream_tls_ca_file", "") or "").strip() or None,
        insecure_skip_verify=bool(policy_engine.get("system.stream_tls_insecure_skip_verify", False)),
    )


def _encrypt_for_mesh(payload: bytes) -> bytes:
    key = _mesh_psk_bytes()
    if key is None and _mesh_encryption_required():
        raise ValueError("Mesh encryption is required but mesh PSK is not configured.")
    if key is None or AESGCM is None:
        return payload
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(nonce, payload, _ENC_AAD)
    return _ENC_MAGIC + nonce + encrypted


def _decrypt_for_mesh(payload: bytes) -> bytes:
    if _mesh_encryption_required() and not payload.startswith(_ENC_MAGIC):
        raise ValueError("Mesh encryption is required but an unencrypted payload was received.")
    if not payload.startswith(_ENC_MAGIC):
        return payload
    key = _mesh_psk_bytes()
    if key is None or AESGCM is None:
        raise ValueError("Encrypted mesh payload received but mesh PSK is not configured.")
    if len(payload) <= len(_ENC_MAGIC) + 12:
        raise ValueError("Encrypted mesh payload malformed.")
    nonce_start = len(_ENC_MAGIC)
    nonce = payload[nonce_start : nonce_start + 12]
    ciphertext = payload[nonce_start + 12 :]
    return AESGCM(key).decrypt(nonce, ciphertext, _ENC_AAD)


def _frag_header(transfer_hex: str, index: int, total: int) -> bytes:
    transfer_raw = bytes.fromhex(transfer_hex)
    if len(transfer_raw) != 16:
        raise ValueError("transfer id must be 16 bytes")
    return (
        _FRAG_MAGIC
        + bytes([_FRAG_VERSION])
        + transfer_raw
        + int(index).to_bytes(2, "big")
        + int(total).to_bytes(2, "big")
        + b"\x00" * 7
    )


def _parse_frag_header(packet: bytes) -> tuple[str, int, int] | None:
    if len(packet) < _FRAG_HEADER_LEN:
        return None
    if packet[:4] != _FRAG_MAGIC:
        return None
    if packet[4] != _FRAG_VERSION:
        return None
    transfer_id = packet[5:21].hex()
    index = int.from_bytes(packet[21:23], "big")
    total = int.from_bytes(packet[23:25], "big")
    if total <= 0 or index >= total:
        return None
    return transfer_id, index, total


def _kill_stale_udp_holder(port: int) -> bool:
    """Find and kill a stale process holding a UDP port (Windows + POSIX)."""
    import subprocess
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "UDP"],
            capture_output=True, text=True, timeout=5,
        )
        for line in (result.stdout or "").splitlines():
            if "UDP" not in line or f":{port}" not in line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                holder_pid = int(parts[-1])
            except ValueError:
                continue
            if holder_pid == my_pid or holder_pid == 0:
                continue
            audit_logger.log(
                "killing_stale_port_holder",
                target_id=f"pid={holder_pid}",
                target_type="transport",
                details={"port": port},
            )
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(holder_pid)],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


@dataclass
class TransportRuntime:
    host: str
    port: int
    public_host: str
    public_port: int
    running: bool
    stream_host: str | None = None
    stream_port: int | None = None


class UDPTransportServer:
    """
    Minimal UDP transport for v1.
    Safe defaults:
    - bounded packet size
    - no connection state
    - callback gets raw bytes + source addr
    """

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 49152,
        on_message: Callable[[bytes, tuple[str, int]], None] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.on_message = on_message
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stream_server: StreamTransportServer | None = None
        self._stream_endpoint: StreamEndpoint | None = None
        self._transfer = TransferManager()
        self._frag_lock = threading.Lock()
        self._frag_buckets: dict[str, dict[str, object]] = {}
        self._stop = threading.Event()

    def start(self) -> TransportRuntime:
        if self._thread and self._thread.is_alive():
            return TransportRuntime(
                self.host,
                self.port,
                getattr(self, "public_host", self.host),
                getattr(self, "public_port", self.port),
                True,
                self._stream_endpoint.host if self._stream_endpoint else None,
                self._stream_endpoint.port if self._stream_endpoint else None,
            )

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host, self.port))
        except OSError as bind_err:
            if getattr(bind_err, "winerror", None) == 10048 or bind_err.errno == 10048:
                killed = _kill_stale_udp_holder(self.port)
                if killed:
                    time.sleep(0.5)
                    try:
                        sock.bind((self.host, self.port))
                    except OSError:
                        sock.close()
                        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        sock.bind((self.host, self.port))
                else:
                    raise
            else:
                raise
        
        # Phase 23: STUN Public Endpoint Discovery
        from network.stun_client import discover_public_endpoint
        public_endpoint = discover_public_endpoint(sock)
        
        if public_endpoint:
            self.public_host, self.public_port = public_endpoint
            audit_logger.log(
                "stun_discovery_success",
                target_id=f"{self.public_host}:{self.public_port}",
                target_type="transport",
                details={},
            )
        else:
            self.public_host = self.host
            self.public_port = self.port
            audit_logger.log("stun_discovery_failed", target_id=self.host, target_type="transport", details={})

        sock.settimeout(1.0)
        self._sock = sock
        self._stop.clear()

        self._thread = threading.Thread(
            target=self._loop,
            name="nulla-udp-transport",
            daemon=True,
        )
        self._thread.start()

        if _stream_enabled():
            try:
                self._stream_server = StreamTransportServer(
                    host=self.host,
                    port=_stream_port_for_udp_port(self.port),
                    on_frame=self._on_stream_frame,
                    tls_config=_stream_tls_server_config(),
                )
                self._stream_endpoint = self._stream_server.start()
            except Exception as exc:
                self._stream_server = None
                self._stream_endpoint = None
                audit_logger.log(
                    "stream_transport_start_failed",
                    target_id=f"{self.host}:{_stream_port_for_udp_port(self.port)}",
                    target_type="transport",
                    details={"error": str(exc)},
                )

        audit_logger.log(
            "transport_started",
            target_id=(
                f"{self.host}:{self.port} (public {self.public_host}:{self.public_port})"
                + (
                    f" stream={self._stream_endpoint.host}:{self._stream_endpoint.port}"
                    if self._stream_endpoint
                    else " stream=disabled"
                )
            ),
            target_type="transport",
            details={},
        )

        return TransportRuntime(
            self.host,
            self.port,
            self.public_host,
            self.public_port,
            True,
            self._stream_endpoint.host if self._stream_endpoint else None,
            self._stream_endpoint.port if self._stream_endpoint else None,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._stream_server:
            try:
                self._stream_server.stop()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        audit_logger.log(
            "transport_stopped",
            target_id=f"{self.host}:{self.port}",
            target_type="transport",
            details={},
        )

    def _loop(self) -> None:
        assert self._sock is not None
        max_bytes = _datagram_limit()

        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(max_bytes)
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                audit_logger.log(
                    "transport_receive_error",
                    target_id=f"{self.host}:{self.port}",
                    target_type="transport",
                    details={"error": str(e)},
                )
                continue

            if not data:
                continue

            try:
                payload = self._handle_fragment_packet(data)
                if payload is None:
                    continue
                decoded = _decrypt_for_mesh(payload)
                if self.on_message:
                    self.on_message(decoded, addr)
            except Exception as e:
                audit_logger.log(
                    "transport_callback_error",
                    target_id=f"{self.host}:{self.port}",
                    target_type="transport",
                    details={"error": str(e)},
                )

    def _handle_fragment_packet(self, packet: bytes) -> bytes | None:
        parsed = _parse_frag_header(packet)
        if parsed is None:
            return packet
        transfer_id, index, total = parsed
        chunk = packet[_FRAG_HEADER_LEN:]
        now = time.time()
        timeout_seconds = max(1.0, _frag_timeout_seconds())
        bucket_limit = max(32, _frag_bucket_limit())
        with self._frag_lock:
            stale = [
                tid
                for tid, bucket in self._frag_buckets.items()
                if now - float(bucket.get("created_at", now)) > timeout_seconds
            ]
            for tid in stale:
                self._frag_buckets.pop(tid, None)
            bucket = self._frag_buckets.get(transfer_id)
            if bucket is None:
                if len(self._frag_buckets) >= bucket_limit:
                    return None
                bucket = {"total": total, "chunks": {}, "created_at": now}
                self._frag_buckets[transfer_id] = bucket
            if int(bucket.get("total", total)) != total:
                self._frag_buckets.pop(transfer_id, None)
                return None
            chunks = bucket["chunks"]  # type: ignore[index]
            if isinstance(chunks, dict):
                chunks[index] = chunk
                if len(chunks) == total:
                    out = b"".join(chunks[i] for i in range(total))
                    self._frag_buckets.pop(transfer_id, None)
                    if len(out) > _message_limit():
                        return None
                    return out
        return None

    def _on_stream_frame(self, payload: bytes, addr: tuple[str, int]) -> bytes | None:
        response = self._transfer.receive_frame(payload)
        try:
            _msg_type, body = decode_frame(payload)
            transfer_id = str(body.get("transfer_id") or "")
            if transfer_id:
                completed = self._transfer.completed_payload(transfer_id)
                if completed is not None and self.on_message:
                    self.on_message(_decrypt_for_mesh(completed), addr)
        except Exception as exc:
            audit_logger.log(
                "stream_frame_processing_error",
                target_id=f"{self.host}:{self.port}",
                target_type="transport",
                details={"error": str(exc)},
            )
        return response


def send_message(host: str, port: int, payload: bytes, timeout_seconds: float = 1.0) -> bool:
    msg_limit = _message_limit()
    if len(payload) > msg_limit:
        return False

    try:
        prepared = _encrypt_for_mesh(payload)
    except Exception:
        return False
    if len(prepared) > _stream_threshold() and _stream_enabled():
        stream_port = _stream_port_for_udp_port(int(port))
        retries = max(0, int(policy_engine.get("network.stream_send_retries", 1)))
        attempts = 1 + retries
        for attempt in range(1, attempts + 1):
            try:
                TransferManager(tls_client_config=_stream_tls_client_config()).send_payload(
                    StreamEndpoint(host=host, port=stream_port),
                    prepared,
                )
                return True
            except Exception:
                if attempt < attempts:
                    time.sleep(min(0.25, 0.05 * attempt))
                continue

    datagram_limit = _datagram_limit()
    if len(prepared) > datagram_limit:
        if not _fragment_enabled():
            return False
        return _send_fragmented(host, int(port), prepared, timeout_seconds=timeout_seconds)

    try:
        with _get_send_socket(timeout_seconds) as sock:
            sent = sock.sendto(prepared, (host, int(port)))
            return sent == len(prepared)
    except Exception:
        return False


def _send_fragmented(host: str, port: int, payload: bytes, *, timeout_seconds: float) -> bool:
    datagram_limit = _fragment_datagram_limit()
    chunk_space = datagram_limit - _FRAG_HEADER_LEN
    if chunk_space <= 0:
        return False
    total = (len(payload) + chunk_space - 1) // chunk_space
    if total > 65535:
        return False
    transfer_id = uuid4().hex
    try:
        with _get_send_socket(timeout_seconds) as sock:
            for index in range(total):
                start = index * chunk_space
                end = min(len(payload), start + chunk_space)
                chunk = payload[start:end]
                packet = _frag_header(transfer_id, index, total) + chunk
                sent = sock.sendto(packet, (host, port))
                if sent != len(packet):
                    return False
        return True
    except Exception:
        return False


def _get_send_socket(timeout_seconds: float) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_seconds)
    return sock
