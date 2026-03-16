import time


# Mock signer to control peer IDs must be set BEFORE core imports
class MockSigner:
    def __init__(self, target_id="abcd000011112222"):
        self.target_id = target_id
    def __call__(self):
        return self.target_id

import network.signer

network.signer.get_local_peer_id = MockSigner()

import network.protocol

network.protocol.verify_signature = lambda env: True

from core.credit_dex import check_and_generate_credit_offer, global_credit_market
from core.credit_ledger import award_credits, get_credit_balance
from storage.db import get_connection
from storage.migrations import run_migrations


def _zero_ledger_for_testing():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM compute_credit_ledger")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def test_dex():
    # Setup fresh state
    run_migrations()
    _zero_ledger_for_testing()

    # 1. Test Seller generating an offer
    # Node must have > threshold to auto sell. Let's say threshold is 1000.
    award_credits("abcd000011112222", 1500, "farmed_from_tasks")

    offer_dict = check_and_generate_credit_offer(auto_sell_threshold=1000, usdc_ask_price=0.08)
    assert offer_dict is not None, "Miner should generate an offer if balance > threshold"
    assert offer_dict["credits_available"] == 1400, "Miner should sell balance minus 100 buffer"
    assert offer_dict["usdc_per_credit"] == 0.08, "Miner ask price should match config"

    print(f"Miner generated offer: {offer_dict['credits_available']} credits at ${offer_dict['usdc_per_credit']}")

    # 2. Test Buyer viewing the market
    # Let's seed the global queue with multiple remote offers
    global_credit_market._offers = [] # clear queue
    global_credit_market._offer_map = {}

    global_credit_market.push({
        "offer_id": "offer_whale",
        "seller_peer_id": "deadbeef11112222",
        "credits_available": 10000,
        "usdc_per_credit": 0.10, # Expensive
        "seller_wallet_address": "wallet_whale"
    })

    global_credit_market.push({
        "offer_id": "offer_cheap",
        "seller_peer_id": "1111222233334444",
        "credits_available": 50,
        "usdc_per_credit": 0.04, # Very Cheap, but not enough quantity
        "seller_wallet_address": "wallet_cheap"
    })

    global_credit_market.push({
        "offer_id": "offer_mid",
        "seller_peer_id": "5555666677778888",
        "credits_available": 500,
        "usdc_per_credit": 0.06, # Good price, good quantity
        "seller_wallet_address": "wallet_mid"
    })

    # Buyer needs 300 credits for a massive task
    print("\nBuyer needs 300 credits. Querying the DEX via DNA Bridge...")
    from core.dna_payment_bridge import dna_bridge

    # Needs a mock wallet linked first
    dna_bridge.link_wallet("simulated_solana_wallet_buyer_address_long_enough")
    result = dna_bridge.purchase_credits_from_dex(300, "abcd000011112222")

    assert result["success"] is True, f"DEX purchase failed: {result.get('reason')}"

    total_cost = result["total_usdc_cost"]
    print(f"Total USDC cost for 300 credits: ${total_cost:.2f}")
    assert total_cost == 17.0, f"Expected 17.0 USDC cost, got {total_cost}"

    # Verify ledger was credited via the CREDIT_TRANSFER message routing
    time.sleep(0.5) # Let the local router process the spoofed mesh message
    new_balance = get_credit_balance("abcd000011112222")
    print(f"New Local Credit Balance: {new_balance}")
    assert new_balance == 1800.0, f"Expected balance of 1800.0 (1500 init + 300 bought), got {new_balance}"

    print("\nSUCCESS: Credit Market DEX logic verified.")

if __name__ == "__main__":
    test_dex()
