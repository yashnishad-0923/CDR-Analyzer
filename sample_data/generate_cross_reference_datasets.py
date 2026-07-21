"""
Generate MULTIPLE CDR datasets — one CSV per suspect — for testing the
cross-reference / multi-CDR analysis feature.

Workflow this supports:
    Cyber analyst creates ONE case, then uploads all of these files into it.
    get_cross_analysis() then links the separate dumps by finding:
      * COMMON NUMBERS   contacted by 2+ suspects (the coordinator / bridge)
      * SHARED HANDSETS  one IMEI seen across suspects (SIM swap / handed phone)
      * SHARED SIMs      one IMSI moved between handsets
      * SHARED CELLS     towers where multiple suspects were co-located

Everything is deterministic (seed=7) and forensically realistic:
  * IMEIs carry real GSMA TAC prefixes + a valid Luhn check digit.
  * IMSIs use real Indian MCC-MNC pairs (Airtel 404-45, Jio 405-840, Vi 404-01).
  * Cell IDs use the real MCC-MNC-LAC-CI shape so geolocation can resolve them.

Planted cross-case ground truth (so you can verify the feature works):
  1. COMMON NUMBER  919800011122 ("the handler") is called by ALL three suspects.
  2. SHARED HANDSET  IMEI_SHARED is used by Suspect A AND Suspect B (a swapped
     phone passed between them).
  3. SHARED SIM      IMSI_SHARED (Airtel) appears in two different handsets.
  4. SHARED CELL     the "Nehru Place" tower is used by Suspect B AND Suspect C.
  5. DIRECT CONTACT  the three suspects also phone each other directly.
"""

import csv
import os
import random

random.seed(7)

# --- Real GSMA TAC prefixes -------------------------------------------------
TACS = {
    "iphone13": "35242150", "iphone12": "01326700",
    "galaxy": "35326005", "redmi": "86800102", "oneplus": "35847909",
}


def luhn_check_digit(number14: str) -> int:
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
    body = tac + serial6
    return body + str(luhn_check_digit(body))


# Handsets --------------------------------------------------------------------
IMEI_A       = make_imei(TACS["iphone13"], "300111")   # Suspect A primary
IMEI_B       = make_imei(TACS["galaxy"],   "300222")   # Suspect B primary
IMEI_C       = make_imei(TACS["redmi"],    "300333")   # Suspect C primary
IMEI_SHARED  = make_imei(TACS["oneplus"],  "300999")   # passed A -> B
IMEI_D       = make_imei(TACS["iphone12"], "300444")   # receives shared SIM

# SIMs (IMSIs) ----------------------------------------------------------------
IMSI_A       = "404450100030011"   # Airtel — Suspect A
IMSI_B       = "405840200030022"   # Jio    — Suspect B
IMSI_C       = "404010300030033"   # Vi     — Suspect C
IMSI_SHARED  = "404450100039999"   # Airtel — moved between IMEI_SHARED & IMEI_D

# Cell towers: MCC-MNC-LAC-CI (real Delhi/NCR grid) --------------------------
CELLS = {
    "cp":         "404-45-1201-30501",   # Connaught Place (A)
    "karolbagh":  "404-45-1201-30502",   # Karol Bagh (A)
    "nehruplace": "404-45-1210-30777",   # Nehru Place  (SHARED B & C)
    "gurgaon":    "404-45-1305-30733",   # Gurgaon (B)
    "noida":      "405-840-2210-30915",  # Noida (C)
    "rohini":     "404-45-1202-30640",   # Rohini (C)
    "saket":      "404-45-1204-30877",   # Saket (B)
}
CELL_CITY = {
    "404-45-1201-30501": "Connaught Place, New Delhi",
    "404-45-1201-30502": "Karol Bagh, New Delhi",
    "404-45-1210-30777": "Nehru Place, New Delhi",
    "404-45-1305-30733": "Cyber City, Gurgaon",
    "405-840-2210-30915": "Sector 18, Noida",
    "404-45-1202-30640": "Rohini, New Delhi",
    "404-45-1204-30877": "Saket, New Delhi",
}

# Cast -----------------------------------------------------------------------
SUS_A = "919810000001"
SUS_B = "919820000002"
SUS_C = "919830000003"
HANDLER = "919800011122"          # common number — contacted by all three
ASSOC_A = ["919711100011", "919711100012", "919711100013"]
ASSOC_B = ["919722200021", "919722200022", "919722200023"]
ASSOC_C = ["919733300031", "919733300032", "919733300033"]

HEADER = [
    "Subject", "Call Type", "Calling Number", "Called Number",
    "Call Date", "Call Time", "Duration", "First Cell ID", "Last Cell ID",
    "IMEI", "IMSI", "Roaming Center",
]


def rtime():
    return f"{random.randint(7,23):02d}:{random.randint(0,59):02d}:{random.randint(0,59):02d}"


def rdate():
    return f"2026-06-{random.randint(1,25):02d}"


def row(subject, ctype, a, b, imei, imsi, cell):
    is_sms = ctype.upper().startswith("SMS")
    dur = 0 if is_sms else random.randint(20, 780)
    return [subject, ctype, a, b, rdate(), rtime(), dur,
            cell, cell, imei, imsi, CELL_CITY.get(cell, "")]


def block(subject, imei, imsi, cells, contacts, n):
    """n rows where `subject` owns the handset (IMEI/IMSI keyed to subject)."""
    rows = []
    for _ in range(n):
        peer = random.choice(contacts)
        cell = random.choice(cells)
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


def build_suspect_a():
    rows = []
    # A on primary handset; frequently calls the handler and the other suspects.
    rows += block(SUS_A, IMEI_A, IMSI_A,
                  [CELLS["cp"], CELLS["karolbagh"]],
                  ASSOC_A + [HANDLER, HANDLER, SUS_B, SUS_C], 34)
    # *** SHARED HANDSET ***: A also uses IMEI_SHARED (later handed to B).
    rows += block(SUS_A, IMEI_SHARED, IMSI_A,
                  [CELLS["cp"], CELLS["nehruplace"]],
                  ASSOC_A + [HANDLER], 8)
    random.shuffle(rows)
    return rows


def build_suspect_b():
    rows = []
    rows += block(SUS_B, IMEI_B, IMSI_B,
                  [CELLS["gurgaon"], CELLS["saket"], CELLS["nehruplace"]],
                  ASSOC_B + [HANDLER, HANDLER, SUS_A, SUS_C], 32)
    # *** SHARED HANDSET ***: B now uses the SAME IMEI_SHARED that A used.
    rows += block(SUS_B, IMEI_SHARED, IMSI_B,
                  [CELLS["nehruplace"], CELLS["gurgaon"]],
                  ASSOC_B + [HANDLER], 9)
    random.shuffle(rows)
    return rows


def build_suspect_c():
    rows = []
    rows += block(SUS_C, IMEI_C, IMSI_C,
                  [CELLS["noida"], CELLS["rohini"], CELLS["nehruplace"]],
                  ASSOC_C + [HANDLER, HANDLER, SUS_A, SUS_B], 30)
    # *** SHARED SIM ***: C moves the shared Airtel SIM into a 2nd handset.
    rows += block(SUS_C, IMEI_D, IMSI_SHARED,
                  [CELLS["rohini"], CELLS["noida"]],
                  ASSOC_C + [HANDLER], 10)
    # give the shared SIM a prior life in IMEI_SHARED too (so IMSI_SHARED spans
    # two handsets -> "shared SIM" detection).
    rows += block(SUS_C, IMEI_SHARED, IMSI_SHARED,
                  [CELLS["nehruplace"]], ASSOC_C + [HANDLER], 4)
    random.shuffle(rows)
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    return len(rows)


def main():
    out_dir = os.path.dirname(os.path.abspath(__file__))
    files = {
        "cross_suspect_A_delhi.csv": build_suspect_a(),
        "cross_suspect_B_gurgaon.csv": build_suspect_b(),
        "cross_suspect_C_noida.csv": build_suspect_c(),
    }
    total = 0
    print("Generated per-suspect CDR files (upload ALL into ONE case):\n")
    for name, rows in files.items():
        n = write_csv(os.path.join(out_dir, name), rows)
        total += n
        print(f"  {name:32s} {n:4d} rows")

    print(f"\n  TOTAL {total} rows across {len(files)} files")
    print("\nCross-reference ground truth to expect after uploading all three:")
    print(f"  COMMON NUMBER   {HANDLER}  -> contacted by all 3 suspects")
    print(f"  SHARED HANDSET  IMEI {IMEI_SHARED}")
    print(f"                    used by {SUS_A}, {SUS_B}, {SUS_C}")
    print(f"  SHARED SIM      IMSI {IMSI_SHARED}")
    print(f"                    in IMEI {IMEI_D} and {IMEI_SHARED}")
    print(f"  SHARED CELL     Nehru Place {CELLS['nehruplace']} -> A, B and C")
    print(f"  DIRECT LINKS    {SUS_A} <-> {SUS_B} <-> {SUS_C}")


if __name__ == "__main__":
    main()
