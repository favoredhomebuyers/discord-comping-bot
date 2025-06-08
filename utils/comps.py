# utils/comps.py

def get_comps_and_arv(sqft: int, avg_psf: float, rehab_level: int) -> dict:
    """
    Returns pricing breakdowns for Cash, RBP, and Takedown strategies based on:
    - rehab level cost
    - ARV (calculated from real comps) and As-Is value
    """
    fee = 40000
    rehab_cost = (sqft or 1500) * (10 * rehab_level)  # $10 to $50/sqft based on level

    def arv_multiplier(arv):
        if arv < 100_000: return 0.55
        elif arv < 150_000: return 0.65
        elif arv < 250_000: return 0.70
        elif arv < 350_000: return 0.75
        elif arv < 500_000: return 0.80
        else: return 0.85

    # Use the ARV calculated from real comps passed from main.py
    arv_est = (sqft or 1500) * (avg_psf or 150)
    as_is_est = arv_est # Assuming As-Is is the same as ARV for this calculation

    # Calculate offers
    cash_moa = arv_est * arv_multiplier(arv_est) - rehab_cost - fee
    rbp_moa = as_is_est * 0.90 - fee
    td_moa = as_is_est * 0.95 - 75000

    return {
        "arv": arv_est,
        "rehab_cost": int(rehab_cost),
        "fee": fee,
        "as_is_value_rbp": rbp_moa,
        "cash_offer": int(cash_moa),
        "rbp_offer": int(rbp_moa - fee),
        "takedown_offer": int(td_moa)
    }
