from __future__ import annotations

import unittest

from sandbox.network_guard import command_uses_network, parse_command


class NetworkGuardTests(unittest.TestCase):
    def test_blocks_known_network_binaries(self) -> None:
        self.assertTrue(command_uses_network(["curl", "https://example.com"]))
        self.assertTrue(command_uses_network(["/usr/bin/wget", "https://example.com"]))

    def test_detects_network_usage_inside_interpreter_commands(self) -> None:
        argv = parse_command("python3 -c \"import requests; requests.get('https://example.com')\"")
        self.assertTrue(command_uses_network(argv))

    def test_env_wrapped_network_binary_is_detected(self) -> None:
        argv = ["/usr/bin/env", "curl", "https://example.com"]
        self.assertTrue(command_uses_network(argv))

    def test_detects_package_manager_network_actions(self) -> None:
        self.assertTrue(command_uses_network(parse_command("git pull")))
        self.assertTrue(command_uses_network(parse_command("npm install")))
        self.assertTrue(command_uses_network(parse_command("env NODE_ENV=test pnpm add vite")))

    def test_non_network_command_passes(self) -> None:
        self.assertFalse(command_uses_network(["python3", "-c", "print('ok')"]))


if __name__ == "__main__":
    unittest.main()
