"""
Entity enrichment: derive human-readable intelligence from raw CDR identifiers.

- operator_from_imsi / operator_from_mcc_mnc: map the IMSI's MCC+MNC to the
  network operator (focused on Indian operators, MCC 404/405).
- imei_details: decompose an IMEI into TAC / serial / check-digit, validate the
  Luhn check digit, and look up handset make/model for known TACs.

These are pure functions (no DB, no network) so they're cheap and testable.
"""

from typing import Optional, Dict

# ---------------------------------------------------------------------------
# Network operator lookup (MCC-MNC -> operator)
# ---------------------------------------------------------------------------
# India uses MCC 404 and 405. This is a representative (not exhaustive) map of
# the major operators and their circles. Post-consolidation, most MNCs belong to
# Airtel, Jio, Vi (Vodafone Idea) or BSNL.
MCC_MNC_OPERATORS: Dict[str, str] = {
    # Bharti Airtel
    "404-45": "Airtel", "404-10": "Airtel", "404-31": "Airtel", "404-40": "Airtel",
    "404-49": "Airtel", "404-90": "Airtel", "404-92": "Airtel", "404-93": "Airtel",
    "404-94": "Airtel", "404-95": "Airtel", "404-96": "Airtel", "404-97": "Airtel",
    "404-98": "Airtel", "405-51": "Airtel", "405-52": "Airtel", "405-53": "Airtel",
    # Reliance Jio
    "405-840": "Jio", "405-854": "Jio", "405-855": "Jio", "405-856": "Jio",
    "405-857": "Jio", "405-858": "Jio", "405-859": "Jio", "405-860": "Jio",
    "405-863": "Jio", "405-873": "Jio", "405-874": "Jio",
    # Vodafone Idea (Vi)
    "404-01": "Vi (Vodafone Idea)", "404-05": "Vi (Vodafone Idea)",
    "404-11": "Vi (Vodafone Idea)", "404-15": "Vi (Vodafone Idea)",
    "404-20": "Vi (Vodafone Idea)", "404-27": "Vi (Vodafone Idea)",
    "404-30": "Vi (Vodafone Idea)", "404-43": "Vi (Vodafone Idea)",
    "404-46": "Vi (Vodafone Idea)", "404-60": "Vi (Vodafone Idea)",
    "404-84": "Vi (Vodafone Idea)", "404-86": "Vi (Vodafone Idea)",
    "405-66": "Vi (Vodafone Idea)", "405-67": "Vi (Vodafone Idea)",
    # BSNL / MTNL
    "404-34": "BSNL", "404-38": "BSNL", "404-51": "BSNL", "404-53": "BSNL",
    "404-54": "BSNL", "404-55": "BSNL", "404-57": "BSNL", "404-58": "BSNL",
    "404-62": "BSNL", "404-64": "BSNL", "404-71": "BSNL", "404-72": "BSNL",
    "404-73": "BSNL", "404-74": "BSNL", "404-75": "BSNL", "404-76": "BSNL",
    "404-77": "BSNL", "404-80": "BSNL", "404-81": "BSNL",
    "404-68": "MTNL", "404-69": "MTNL",
}

MCC_COUNTRY = {"404": "India", "405": "India", "410": "Pakistan", "413": "Sri Lanka",
               "470": "Bangladesh", "310": "USA", "311": "USA", "234": "United Kingdom"}


def operator_from_mcc_mnc(mcc: str, mnc: str) -> Optional[str]:
    """Look up operator by MCC + MNC, trying both 2- and 3-digit MNC forms."""
    for key in (f"{mcc}-{mnc}", f"{mcc}-{int(mnc):02d}" if mnc.isdigit() else None):
        if key and key in MCC_MNC_OPERATORS:
            return MCC_MNC_OPERATORS[key]
    return None


def operator_from_imsi(imsi: Optional[str]) -> Dict:
    """Derive network operator + country from an IMSI (MCC=3 digits, MNC=2–3)."""
    result = {"imsi": imsi, "mcc": None, "mnc": None, "operator": None, "country": None}
    if not imsi or not str(imsi).isdigit() or len(str(imsi)) < 5:
        return result
    imsi = str(imsi)
    mcc = imsi[:3]
    result["mcc"] = mcc
    result["country"] = MCC_COUNTRY.get(mcc)
    # Try 2-digit MNC first (India uses 2), then 3-digit.
    for mnc_len in (2, 3):
        mnc = imsi[3:3 + mnc_len]
        op = operator_from_mcc_mnc(mcc, mnc)
        if op:
            result["mnc"] = mnc
            result["operator"] = op
            return result
    result["mnc"] = imsi[3:5]
    return result


# ---------------------------------------------------------------------------
# IMEI decomposition + validation
# ---------------------------------------------------------------------------
# TAC (Type Allocation Code) -> handset make/model. Real TACs are assigned by
# the GSMA; this small map covers a few well-known ones plus any demo TACs used
# in sample datasets. Unknown TACs still get a full structural breakdown.
TAC_DEVICES: Dict[str, str] = {
    "35242150": "Apple iPhone (representative)",
    "01326700": "Apple iPhone",
    "35326005": "Samsung Galaxy",
    "86800102": "Xiaomi Redmi",
    "35847909": "OnePlus",
}


def _luhn_check_digit(number_without_check: str) -> int:
    """Compute the Luhn check digit for the first 14 IMEI digits."""
    total = 0
    # Digits are processed right-to-left; every second digit (from the right of
    # the full 15-digit number, i.e. the even positions here) is doubled.
    for i, ch in enumerate(reversed(number_without_check)):
        d = int(ch)
        if i % 2 == 0:  # positions that get doubled
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


def imei_details(imei: Optional[str]) -> Dict:
    """Decompose an IMEI: TAC (8), serial (6), check digit (1); validate Luhn;
    resolve make/model when the TAC is known."""
    result = {
        "imei": imei, "valid": False, "tac": None, "serial": None,
        "check_digit": None, "expected_check_digit": None,
        "make_model": None, "note": None,
    }
    if not imei:
        return result
    digits = "".join(ch for ch in str(imei) if ch.isdigit())
    if len(digits) < 14:
        result["note"] = "IMEI too short — expected 15 digits"
        return result
    if len(digits) == 14:
        # IMEI without the check digit — compute and append it.
        digits = digits + str(_luhn_check_digit(digits))
    digits = digits[:15]

    tac = digits[:8]
    serial = digits[8:14]
    check = int(digits[14])
    expected = _luhn_check_digit(digits[:14])

    result.update({
        "tac": tac,
        "serial": serial,
        "check_digit": check,
        "expected_check_digit": expected,
        "valid": (check == expected),
        "make_model": TAC_DEVICES.get(tac, "Unknown handset (TAC not in local database)"),
    })
    if check != expected:
        result["note"] = "Luhn check digit mismatch — IMEI may be invalid or synthetic"
    return result
