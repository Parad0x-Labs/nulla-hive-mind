from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import Mock, patch

from network.chunk_protocol import chunk_payload, chunk_to_dict, decode_frame, encode_frame, manifest_to_dict
from network.transport import UDPTransportServer, _configure_udp_socket_buffers, send_message
from storage.migrations import run_migrations


class TransportLargePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    @staticmethod
    def _transport_policy(path: str, default=None):
        overrides = {
            "system.max_datagram_bytes": 32768,
            "system.max_fragment_datagram_bytes": 1400,
            "system.max_message_bytes": 262144,
            "system.stream_transfer_threshold_bytes": 24576,
            "system.enable_stream_data_plane": True,
            "system.enable_udp_fragmentation": True,
            "system.max_fragment_buckets": 2048,
            "system.fragment_timeout_seconds": 30.0,
            "system.udp_socket_buffer_bytes": 524288,
            "system.fragment_burst_packets": 4,
            "system.fragment_pause_seconds": 0.0005,
            "system.require_mesh_encryption": False,
            "system.stream_tls_enabled": False,
            "system.stream_tls_certfile": "",
            "system.stream_tls_keyfile": "",
            "system.stream_tls_ca_file": "",
            "system.stream_tls_require_client_cert": False,
            "system.stream_tls_insecure_skip_verify": False,
            "network.stream_send_retries": 1,
        }
        return overrides.get(path, default)

    def test_fragmented_udp_delivery_for_large_payload(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payload = b"A" * 70000
            with patch("network.transport._stream_enabled", return_value=False), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                ok = send_message("127.0.0.1", runtime.port, payload)
            self.assertTrue(ok)
            self.assertTrue(signal.wait(timeout=5.0))
            self.assertEqual(received[-1], payload)
        finally:
            server.stop()

    def test_fragmented_udp_delivery_survives_small_burst_sequence(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payloads = [b"C" * 70000, b"D" * 71000, b"E" * 72000]
            for payload in payloads:
                with patch("network.transport._stream_enabled", return_value=False), patch(
                    "network.transport.policy_engine.get",
                    side_effect=self._transport_policy,
                ):
                    ok = send_message("127.0.0.1", runtime.port, payload)
                self.assertTrue(ok)
            deadline = time.time() + 6.0
            while len(received) < len(payloads) and time.time() < deadline:
                signal.wait(timeout=0.25)
                signal.clear()
            self.assertEqual(received, payloads)
        finally:
            server.stop()

    def test_stream_delivery_for_large_payload(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payload = b"B" * 90000
            self.assertIsNotNone(runtime.stream_port)
            with patch("network.transport._fragment_enabled", return_value=False), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                ok = send_message("127.0.0.1", runtime.port, payload)
            self.assertTrue(ok)
            self.assertTrue(signal.wait(timeout=6.0))
            self.assertEqual(received[-1], payload)
        finally:
            server.stop()
            time.sleep(0.1)

    def test_ephemeral_port_start_reports_real_udp_and_stream_ports(self) -> None:
        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            self.assertGreater(runtime.port, 0)
            self.assertEqual(server.port, runtime.port)
            self.assertEqual(runtime.public_port, runtime.port)
            self.assertIsNotNone(runtime.stream_port)
            self.assertEqual(runtime.stream_port, runtime.port + 1)
        finally:
            server.stop()

    def test_stream_frame_reassembly_dispatches_completed_payload(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        payload = b"stream-frame-reassembly-test" * 10
        manifest, chunks = chunk_payload("transfer-stream-test", payload, chunk_size=32)

        with patch("network.transport.policy_engine.get", side_effect=self._transport_policy):
            ack = server._on_stream_frame(encode_frame("manifest", manifest_to_dict(manifest)), ("127.0.0.1", 9000))
        self.assertIsNotNone(ack)
        msg_type, _ = decode_frame(ack or b"")
        self.assertEqual(msg_type, "ack")

        with patch("network.transport.policy_engine.get", side_effect=self._transport_policy):
            for chunk in chunks:
                server._on_stream_frame(encode_frame("chunk", chunk_to_dict(chunk)), ("127.0.0.1", 9000))

        self.assertTrue(signal.wait(timeout=1.0))
        self.assertEqual(received[-1], payload)

    def test_udp_socket_buffers_are_raised_for_large_transfers(self) -> None:
        sock = Mock()

        with patch("network.transport.policy_engine.get", side_effect=self._transport_policy):
            _configure_udp_socket_buffers(sock)

        self.assertEqual(sock.setsockopt.call_count, 2)


if __name__ == "__main__":
    unittest.main()
