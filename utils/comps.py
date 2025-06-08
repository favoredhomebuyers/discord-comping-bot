# utils/comps.py

def get_comps_and_arv(arv_estimate: float, notes: str, rehab_level: int) -> dict:
    """
    Returns pricing breakdowns for Cash, RBP, and Takedown strategies.
    Now correctly uses the pre-calculated ARV.
    """
    fee = 40000
    # Note: Rehab cost calculation might need adjustment.
    # It assumes sqft is part of the 'notes' or handled elsewhere.
    # For now, we'll make it a fixed placeholder.
    rehab_cost = 10000 * rehab_level # Placeholder: $10k, $20k, $30k etc.

    def arv_multiplier(arv):
        if arv < 100_000: return 0.55
        elif arv < 150_000: return 0.65
        elif arv < 250_000: return 0.70
        elif arv < 350_000: return 0.75
        elif arv < 500_000: return 0.80
        else: return 0.85

    # Use the ARV calculated from real comps
    cash_moa = arv_estimate * arv_multiplier(arv_estimate) - rehab_cost - fee
    
    # As-Is value for RBP is the same as ARV in this simplified model
    as_is_est = arv_estimate
    rbp_moa = as_is_est * 0.90 # Using 90% for RBP
    
    # Takedown calculation
    td_moa = as_is_est * 0.95 - 75000

    return {
        "arv": arv_estimate,
        "rehab_cost": int(rehab_cost),
        "fee": fee,
        "as_is_value_rbp": rbp_moa,
        "cash_offer": int(cash_moa),
        "rbp_offer": int(rbp_moa - fee),
        "takedown_offer": int(td_moa)
    }
