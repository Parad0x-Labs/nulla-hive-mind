import time
import os
import secrets
from typing import Dict, Any, Optional
from core import audit_logger
from core.credit_ledger import award_credits
from core.dna_wallet_manager import DNAWalletManager

# Rate for purchasing credits (1 USDC = 1000 Compute Credits)
USDC_TO_CREDIT_RATE = 1000.0

class DNAPaymentBridge:
    """
    Simulates the DNA x402 payment bridge.
    Instead of staking massive deposits, new users can spin up a node, 
    authorize their Solana wallet, and buy compute credits on-demand.
    """
    def __init__(self):
        self.wallet_address = os.environ.get("NULLA_SOLANA_WALLET", None)
        self.bridge_active = bool(self.wallet_address)
        self.wallets = DNAWalletManager()
        
    def link_wallet(self, solana_address: str) -> bool:
        """Links a Solana address to this local mesh node."""
        # Hardcoded validation mock for v1 shoestring
        if len(solana_address) < 32:
            return False
            
        self.wallet_address = solana_address
        self.bridge_active = True
        return True
        
    def purchase_credits(self, usdc_amount: float, local_peer_id: str) -> Dict[str, Any]:
        """
        Executes a smart-contract transaction transferring `usdc_amount` to the Swarm DAO.
        Upon success, awards local Compute Credits.
        """
        if not self.bridge_active:
            raise ValueError("No Solana wallet linked to DNA bridge.")
            
        if usdc_amount < 0.1:
            raise ValueError("Minimum purchase is 0.1 USDC.")
            
        # [MOCK BEHAVIOR]
        # In actual production: await dna_sdk.sign_and_send_transaction(amount=usdc_amount, mint="USDC")
        time.sleep(1.5) # Simulate Solana finality
        
        # Determine credits
        credits_earned = usdc_amount * USDC_TO_CREDIT_RATE
        tx_id = f"sol_tx_{secrets.token_hex(16)}"

        wallet_status_before = self.wallets.get_status()
        if wallet_status_before and wallet_status_before.hot_wallet_address:
            wallet_status_after = self.wallets.consume_hot_for_credit_purchase(
                usdc_amount,
                local_peer_id=local_peer_id,
                reference_id=tx_id,
                initiated_by="agent",
            )
        else:
            wallet_status_after = None
        
        # Deposit into local SQLite ledger
        award_credits(local_peer_id, credits_earned, reason=f"dna_purchase_tx:{tx_id}", receipt_id=tx_id)
        
        audit_logger.log(
            "dna_credit_purchase",
            target_id=local_peer_id,
            target_type="agent",
            details={
                "usdc_spent": usdc_amount,
                "credits_awarded": credits_earned,
                "tx_id": tx_id,
                "wallet_mode": "hot_wallet" if wallet_status_after else "bridge_only_simulated",
            }
        )
        
        
        return {
            "success": True,
            "tx_id": tx_id,
            "credits_added": credits_earned,
            "new_balance": "handled_by_ledger",
            "settlement_mode": "simulated",
            "wallet_mode": "hot_wallet" if wallet_status_after else "bridge_only_simulated",
            "hot_wallet_balance_usdc": (
                round(float(wallet_status_after.hot_balance_usdc), 6)
                if wallet_status_after
                else None
            ),
            "cold_wallet_balance_usdc": (
                round(float(wallet_status_after.cold_balance_usdc), 6)
                if wallet_status_after
                else None
            ),
        }

    def purchase_credits_from_dex(self, compute_credits_needed: int, local_peer_id: str) -> Dict[str, Any]:
        """
        Phase 29: Decentralized Exchange purchasing.
        Queries the global order book for the cheapest sellers, simulates transferring USDC to them,
        and broadcasts CREDIT_TRANSFER mesh messages to trigger the credit handoff.
        """
        from core.credit_dex import global_credit_market
        from network.protocol import encode_message
        from network.transport import send_message
        from core.discovery_index import endpoint_for_peer
        import uuid
        
        matches = global_credit_market.get_cheapest_offers(compute_credits_needed)
        if not matches:
            return {"success": False, "reason": "No sellers available on the DEX."}
            
        total_bought = sum(m["credits_taking"] for m in matches)
        if total_bought < compute_credits_needed:
            return {"success": False, "reason": f"DEX liquidity too low. Found {total_bought}/{compute_credits_needed}."}
            
        total_usdc_cost = sum(m["total_usdc_cost"] for m in matches)
        
        # Simulate bridging USDC
        time.sleep(1.0)
        
        transactions = []
        for match in matches:
            seller_id = match["seller_peer_id"]
            amount = match["credits_taking"]
            cost = match["total_usdc_cost"]
            wallet = match["seller_wallet_address"]
            
            tx_hash = f"sol_usdc_tx_{secrets.token_hex(16)}"
            transactions.append({
                "seller": seller_id,
                "wallet": wallet,
                "credits": amount,
                "usdc": cost,
                "tx_hash": tx_hash
            })
            
            # Broadcast CREDIT_TRANSFER message simulating the seller's cryptographic signature
            # (In production, the seller's node observes the Solana chain and broadcasts this themselves)
            payload = {
                "transfer_id": str(uuid.uuid4()),
                "seller_peer_id": seller_id,
                "buyer_peer_id": local_peer_id,
                "credits_transferred": amount,
                "on_chain_tx_hash": tx_hash,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
            
            msg = encode_message(
                msg_id=str(uuid.uuid4()),
                msg_type="CREDIT_TRANSFER",
                sender_peer_id=local_peer_id, # Must use local ID so Ed25519 signature is valid for this node
                nonce=uuid.uuid4().hex,
                payload=payload
            )
            
            # Send directly to ourselves to process it locally
            from network.assist_router import handle_incoming_assist_message
            res = handle_incoming_assist_message(raw_bytes=msg)
            print(f"Message Process Result: {res}")

        audit_logger.log(
            "dex_credit_purchase",
            target_id=local_peer_id,
            target_type="agent",
            details={
                "total_usdc_spent": total_usdc_cost,
                "credits_bought": total_bought,
                "sellers_matched": len(matches)
            }
        )
            
        return {
            "success": True,
            "credits_added": total_bought,
            "total_usdc_cost": total_usdc_cost,
            "transactions": transactions
        }

# Global Singleton
dna_bridge = DNAPaymentBridge()
