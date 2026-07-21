"""
Generate a realistic Indian telecom CDR for testing the CDR Analysis Platform.

Everything here is deterministic (fixed seed) so the same CSV is produced every
run — important for reproducible demos and for verifying the SIM-swap / cross-
analysis features against a KNOWN ground truth.

What makes it "real":
  * IMEIs use genuine GSMA TAC prefixes (iPhone / Samsung / Xiaomi / OnePlus)
    with a correct Luhn check digit, so imei_details() validates them.
  * IMSIs use real Indian MCC-MNC pairs (404-45 Airtel, 405-840 Jio,
    404-01 Vi) so operator_from_imsi() resolves the network.
  * Cell IDs use the real "MCC-MNC-LAC-CI" shape so geolocation._parse_real_cell()
    can hand them to OpenCellID.

Ground-truth scenarios deliberately planted (so you can confirm the tools work):
  1. SIM-SWAP: subject 919812345678 and burner 919845678901 are used on the
     SAME handset (IMEI ending ...A). One physical phone, two numbers.
  2. SHARED SIM: one IMSI (Airtel ...4477) is moved between two handsets.
  3. COMMON NUMBER: coordinator 919800011122 is contacted by all three subjects.
"""

import csv
import random
import os

random.seed(42)

# --- Real GSMA TACs (first 8 digits of the IMEI) -> handset -----------------
TACS = {
    "iphone13":  "35242150",   # Apple iPhone (in TAC_DEVICES)
    "iphone12":  "01326700",   # Apple iPhone
    "galaxy":    "35326005",   # Samsung Galaxy
    "redmi":     "86800102",   # Xiaomi Redmi
    "oneplus":   "35847909",   # OnePlus
}


def luhn_check_digit(number14: str) -> int:
    """Standard IMEI Luhn check digit for the first 14 digits."""
    total = 0
    for i, ch in enumerate(reversed(number14)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - (total % 10)) % 10


def make_imei(tac: str, serial6: str) -> str:
    """Build a valid 15-digit IMEI: 8-digit TAC + 6-digit serial + Luhn digit."""
    body = tac + serial6
    return body + str(luhn_check_digit(body))


# Fixed handsets used in the dataset. Serials are fixed for reproducibility.
IMEI_A = make_imei(TACS["iphone13"], "104771")   # the SWAP handset
IMEI_B = make_imei(TACS["galaxy"],   "220015")
IMEI_C = make_imei(TACS["redmi"],    "556210")
IMEI_D = make_imei(TACS["oneplus"],  "778430")   # receives the shared SIM
IMEI_E = make_imei(TACS["iphone12"], "900142")

# --- Real Indian IMSIs (MCC-MNC + 9/10 digit subscriber part) ---------------
# 404-45 Airtel, 405-840 Jio, 404-01 Vi (Vodafone Idea)
IMSI_AIRTEL_1 = "404450100004477"   # the SHARED SIM (moves between handsets)
IMSI_AIRTEL_2 = "404450100008812"
IMSI_JIO_1    = "405840200003391"
IMSI_JIO_2    = "405840200007725"
IMSI_VI_1     = "404010300006654"

# --- Real cell-tower IDs: MCC-MNC-LAC-CellID (Delhi/NCR Airtel & Jio) --------
CELLS = {
    "cp_delhi":     "404-45-1201-10501",   # Connaught Place
    "karolbagh":    "404-45-1201-10502",   # Karol Bagh
    "saket":        "404-45-1204-10877",   # Saket
    "gurgaon":      "404-45-1305-20733",   # Gurgaon Cyber City
    "noida":        "405-840-2210-40915",  # Noida Sector 18 (Jio)
    "dwarka":       "404-45-1207-11290",   # Dwarka
    "rohini":       "404-45-1202-10640",   # Rohini
}
CELL_TO_CITY = {
    "404-45-1201-10501": "Connaught Place, New Delhi",
    "404-45-1201-10502": "Karol Bagh, New Delhi",
    "404-45-1204-10877": "Saket, New Delhi",
    "404-45-1305-20733": "Cyber City, Gurgaon",
    "405-840-2210-40915": "Sector 18, Noida",
    "404-45-1207-11290": "Dwarka, New Delhi",
    "404-45-1202-10640": "Rohini, New Delhi",
}

# --- Cast of numbers --------------------------------------------------------
KINGPIN   = "919812345678"   # subject 1 — main phone (IMEI_A)
BURNER    = "919845678901"   # subject 1's burner SIM — ALSO on IMEI_A (swap!)
LT_TWO    = "919823456789"   # subject 2 (IMEI_B, then IMEI_D via shared SIM)
LT_THREE  = "919834567890"   # subject 3 (IMEI_C)
COORD     = "919800011122"   # coordinator — contacted by all three (bridge)
ASSOCIATES = [
    "919911223344", "919922334455", "919933445566",
    "919944556677", "919955667788", "918800112233",
]

HEADER = [
    "Subject", "Call Type", "Calling Number", "Called Number",
    "Call Date", "Call Time", "Duration", "First Cell ID", "Last Cell ID",
    "IMEI", "IMSI", "Roaming Center",
]


def rtime():
    h = random.randint(7, 23)
    m = random.randint(0, 59)
    s = random.randint(0, 59)
    return f"{h:02d}:{m:02d}:{s:02d}"


def rdate():
    day = random.randint(1, 20)
    return f"2026-06-{day:02d}"


def row(subject, ctype, a, b, imei, imsi, cell):
    """One CDR row. Duration 0 for SMS, else a realistic call length."""
    is_sms = ctype.upper().startswith("SMS")
    dur = 0 if is_sms else random.randint(20, 900)
    return [
        subject, ctype, a, b, rdate(), rtime(), dur,
        cell, cell, imei, imsi, CELL_TO_CITY.get(cell, ""),
    ]


def subject_block(subject, imei, imsi, home_cells, contacts, n, extra_called=None):
    """Generate n CDR rows for one subject/handset/SIM combination.
    Half incoming, half outgoing, a few SMS. `subject` always owns the handset,
    so the IMEI/IMSI on every row belong to `subject` — this is what makes
    SIM-swap detection meaningful (IMEI is keyed to the subject, not the peer)."""
    rows = []
    pool = list(contacts)
    if extra_called:
        pool += list(extra_called)
    for _ in range(n):
        peer = random.choice(pool)
        cell = random.choice(home_cells)
        roll = random.random()
        if roll < 0.4:
            rows.append(row(subject, "Outgoing", subject, peer, imei, imsi, cell))
        elif roll < 0.8:
            rows.append(row(subject, "Incoming", peer, subject, imei, imsi, cell))
        elif roll < 0.9:
            rows.append(row(subject, "SMS-MO", subject, peer, imei, imsi, cell))
        else:
            rows.append(row(subject, "SMS-MT", peer, subject, imei, imsi, cell))
    return rows


def build():
    rows = []
    delhi = list(CELLS.values())

    # Subject 1 — kingpin on his primary handset (IMEI_A + Airtel SIM 1).
    rows += subject_block(
        KINGPIN, IMEI_A, IMSI_AIRTEL_1,
        [CELLS["cp_delhi"], CELLS["karolbagh"], CELLS["saket"]],
        ASSOCIATES + [LT_TWO, LT_THREE], 40,
        extra_called=[COORD, COORD, COORD])

    # *** SIM-SWAP ***: the SAME physical handset (IMEI_A) later carries the
    # burner number's SIM (Jio 1). IMEI_A now maps to two subject numbers.
    rows += subject_block(
        BURNER, IMEI_A, IMSI_JIO_1,
        [CELLS["cp_delhi"], CELLS["gurgaon"]],
        ASSOCIATES + [COORD], 22)

    # Subject 2 — starts on IMEI_B (Airtel SIM 2)...
    rows += subject_block(
        LT_TWO, IMEI_B, IMSI_AIRTEL_2,
        [CELLS["gurgaon"], CELLS["dwarka"]],
        ASSOCIATES + [KINGPIN], 30,
        extra_called=[COORD, COORD])

    # *** SHARED SIM ***: subject 2 moves the SHARED Airtel SIM (IMSI_AIRTEL_1)
    # into a different handset (IMEI_D). Same IMSI now seen in IMEI_A and IMEI_D.
    rows += subject_block(
        LT_TWO, IMEI_D, IMSI_AIRTEL_1,
        [CELLS["dwarka"], CELLS["noida"]],
        ASSOCIATES + [COORD], 12)

    # Subject 3 — clean single handset (IMEI_C + Jio SIM 2).
    rows += subject_block(
        LT_THREE, IMEI_C, IMSI_JIO_2,
        [CELLS["rohini"], CELLS["noida"]],
        ASSOCIATES + [KINGPIN, COORD], 28)

    # A little background traffic on a standalone handset (no swap) for realism.
    rows += subject_block(
        "919966778899", IMEI_E, IMSI_VI_1,
        [CELLS["saket"], CELLS["rohini"]],
        ASSOCIATES, 15)

    random.shuffle(rows)
    return rows


def main():
    rows = build()
    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, "sample_cdr_realistic.csv")
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)

    print(f"Wrote {len(rows)} CDR rows -> {out_path}")
    print("\nGround truth planted:")
    print(f"  SIM-SWAP handset  IMEI {IMEI_A}")
    print(f"      used by {KINGPIN} (Airtel) AND {BURNER} (Jio)")
    print(f"  SHARED SIM        IMSI {IMSI_AIRTEL_1}")
    print(f"      used in IMEI {IMEI_A} AND IMEI {IMEI_D}")
    print(f"  COMMON NUMBER     {COORD} contacted by all three subjects")
    print("\nHandset IMEIs (all Luhn-valid):")
    for name, imei in [("iPhone13", IMEI_A), ("Galaxy", IMEI_B), ("Redmi", IMEI_C),
                       ("OnePlus", IMEI_D), ("iPhone12", IMEI_E)]:
        print(f"  {name:9s} {imei}")


if __name__ == "__main__":
    main()
