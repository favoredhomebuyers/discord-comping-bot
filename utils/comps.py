def get_comps_and_arv(address: str, market_type: str, notes: str, sqft: int, rehab_level: int) -> dict:
    """
    Returns pricing breakdowns for Cash, RBP, and Takedown strategies based on:
    - rehab level cost
    - ARV and As-Is value
    """
    fee = 40000
    rehab_cost = sqft * (10 * rehab_level)  # $10 to $50/sqft based on level

    # Determine ARV pricing tier by total ARV value
    def arv_multiplier(arv):
        if arv < 100_000: return 0.55
        elif arv < 150_000: return 0.65
        elif arv < 250_000: return 0.70
        elif arv < 350_000: return 0.75
        elif arv < 500_000: return 0.80
        else: return 0.85

    # We'll back-calculate ARV from your notes and comps
    # This logic assumes you call this after avg PSF and sqft are already known
    arv_est = sqft * 120  # Placeholder ARV (120 psf)
    as_is_est = sqft * 100  # Placeholder As-Is (100 psf)

    cash_arv = arv_est
    cash_moa = cash_arv * arv_multiplier(cash_arv) - rehab_cost - fee
    rbp_moa = as_is_est * 0.95 - fee
    td_moa = as_is_est * 0.95 - 75000

    return {
        "rehab_cost": int(rehab_cost),
        "cash_offer": int(cash_moa),
        "rbp_offer": int(rbp_moa),
        "takedown_offer": int(td_moa)
    }
