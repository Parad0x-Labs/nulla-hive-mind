from __future__ import annotations

import unittest

from core.credit_ledger import get_credit_balance
from core.dna_payment_bridge import DNAPaymentBridge
from core.dna_wallet_manager import DNAWalletManager
from network.signer import get_local_peer_id
from storage.db import get_connection
from storage.migrations import run_migrations


class DNAPaymentBridgeWalletModeTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM dna_wallet_ledger")
            conn.execute("DELETE FROM dna_wallet_security")
            conn.execute("DELETE FROM dna_wallet_profiles")
            conn.execute("DELETE FROM compute_credit_ledger WHERE peer_id = ?", (get_local_peer_id(),))
            conn.commit()
        finally:
            conn.close()

    def test_purchase_credits_debits_hot_wallet_when_configured(self) -> None:
        manager = DNAWalletManager()
        manager.configure_wallets(
            hot_wallet_address="hot_wallet_address_12345678901234567890",
            cold_wallet_address="cold_wallet_address_123456789012345678",
            cold_secret="secret-1234",
            initial_hot_usdc=3.0,
            initial_cold_usdc=2.0,
        )
        bridge = DNAPaymentBridge()
        bridge.link_wallet("solana_wallet_address_for_bridge_123456789")

        peer_id = get_local_peer_id()
        result = bridge.purchase_credits(1.0, local_peer_id=peer_id)
        self.assertTrue(result["success"])
        self.assertEqual(result["wallet_mode"], "hot_wallet")
        self.assertAlmostEqual(float(result["hot_wallet_balance_usdc"]), 2.0)
        self.assertAlmostEqual(get_credit_balance(peer_id), 1000.0)


if __name__ == "__main__":
    unittest.main()
