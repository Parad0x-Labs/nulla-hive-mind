from __future__ import annotations

import unittest

from core.dna_wallet_manager import DNAWalletManager
from storage.db import get_connection
from storage.migrations import run_migrations


class DNAWalletManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM dna_wallet_ledger")
            conn.execute("DELETE FROM dna_wallet_security")
            conn.execute("DELETE FROM dna_wallet_profiles")
            conn.commit()
        finally:
            conn.close()
        self.manager = DNAWalletManager()

    def test_configure_and_status_roundtrip(self) -> None:
        status = self.manager.configure_wallets(
            hot_wallet_address="hot_wallet_address_12345678901234567890",
            cold_wallet_address="cold_wallet_address_123456789012345678",
            cold_secret="correct horse battery staple",
            initial_hot_usdc=1.25,
            initial_cold_usdc=9.75,
        )
        self.assertAlmostEqual(status.hot_balance_usdc, 1.25)
        self.assertAlmostEqual(status.cold_balance_usdc, 9.75)

        read_back = self.manager.get_status()
        self.assertIsNotNone(read_back)
        self.assertEqual(read_back.hot_wallet_address, "hot_wallet_address_12345678901234567890")

    def test_topup_requires_valid_cold_secret(self) -> None:
        self.manager.configure_wallets(
            hot_wallet_address="hot_wallet_address_12345678901234567890",
            cold_wallet_address="cold_wallet_address_123456789012345678",
            cold_secret="secret-1234",
            initial_hot_usdc=0.0,
            initial_cold_usdc=5.0,
        )
        with self.assertRaises(PermissionError):
            self.manager.top_up_hot_from_cold(1.0, cold_secret="wrong-secret")
        status = self.manager.get_status()
        self.assertIsNotNone(status)
        self.assertAlmostEqual(status.hot_balance_usdc, 0.0)
        self.assertAlmostEqual(status.cold_balance_usdc, 5.0)

    def test_topup_and_move_to_cold_updates_balances(self) -> None:
        self.manager.configure_wallets(
            hot_wallet_address="hot_wallet_address_12345678901234567890",
            cold_wallet_address="cold_wallet_address_123456789012345678",
            cold_secret="secret-1234",
            initial_hot_usdc=1.0,
            initial_cold_usdc=4.0,
        )
        after_topup = self.manager.top_up_hot_from_cold(2.0, cold_secret="secret-1234")
        self.assertAlmostEqual(after_topup.hot_balance_usdc, 3.0)
        self.assertAlmostEqual(after_topup.cold_balance_usdc, 2.0)

        after_move = self.manager.move_hot_to_cold(1.5, cold_secret="secret-1234")
        self.assertAlmostEqual(after_move.hot_balance_usdc, 1.5)
        self.assertAlmostEqual(after_move.cold_balance_usdc, 3.5)


if __name__ == "__main__":
    unittest.main()
