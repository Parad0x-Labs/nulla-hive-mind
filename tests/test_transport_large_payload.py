from __future__ import annotations

import socket
import threading
import time
import unittest
from unittest.mock import patch

from network.chunk_protocol import chunk_payload, chunk_to_dict, decode_frame, encode_frame, manifest_to_dict
from network.transport import UDPTransportServer, send_message
from storage.migrations import run_migrations


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


class TransportLargePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    def test_fragmented_udp_delivery_for_large_payload(self) -> None:
        try:
            port = _free_udp_port()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=port, on_message=_on_message)
        try:
            server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payload = b"A" * 70000
            with patch("network.transport._stream_enabled", return_value=False):
                ok = send_message("127.0.0.1", port, payload)
            self.assertTrue(ok)
            self.assertTrue(signal.wait(timeout=5.0))
            self.assertEqual(received[-1], payload)
        finally:
            server.stop()

    def test_stream_delivery_for_large_payload(self) -> None:
        try:
            port = _free_udp_port()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=port, on_message=_on_message)
        try:
            server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payload = b"B" * 90000
            with patch("network.transport._fragment_enabled", return_value=False):
                ok = send_message("127.0.0.1", port, payload)
            self.assertTrue(ok)
            self.assertTrue(signal.wait(timeout=6.0))
            self.assertEqual(received[-1], payload)
        finally:
            server.stop()
            time.sleep(0.1)

    def test_stream_frame_reassembly_dispatches_completed_payload(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        payload = b"stream-frame-reassembly-test" * 10
        manifest, chunks = chunk_payload("transfer-stream-test", payload, chunk_size=32)

        ack = server._on_stream_frame(encode_frame("manifest", manifest_to_dict(manifest)), ("127.0.0.1", 9000))
        self.assertIsNotNone(ack)
        msg_type, _ = decode_frame(ack or b"")
        self.assertEqual(msg_type, "ack")

        for chunk in chunks:
            server._on_stream_frame(encode_frame("chunk", chunk_to_dict(chunk)), ("127.0.0.1", 9000))

        self.assertTrue(signal.wait(timeout=1.0))
        self.assertEqual(received[-1], payload)


if __name__ == "__main__":
    unittest.main()
