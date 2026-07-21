import csv
import os
import random

def _card_length(bin_prefix):
    p = bin_prefix
    if p.startswith(("34", "37")):
        return 15
    if p.startswith(("300", "301", "302", "303", "304", "305", "36", "38", "39")):
        return 14
    return 16

def _cvv_length(bin_prefix):
    return 4 if bin_prefix.startswith(("34", "37")) else 3

def luhn_verification(num):
    digs = [int(d) for d in str(num)]
    check = digs.pop()
    digs.reverse()
    total = 0
    for i, d in enumerate(digs):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (total * 9) % 10 == check

def _build_valid_card(prefix, length):
    while True:
        remaining = length - len(prefix) - 1
        partial = str(prefix) + "".join(random.choices("0123456789", k=remaining))
        total = 0
        rev = partial[::-1]
        for i, ch in enumerate(rev):
            d = int(ch)
            if i % 2 == 0:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        check = (10 - (total % 10)) % 10
        card = partial + str(check)
        if len(card) == length:
            return card

def cc_gen(bin_prefix, mes, ano, cantidad):
    length = _card_length(bin_prefix)
    cvv_len = _cvv_length(bin_prefix)
    ccs = []
    seen = set()
    while len(ccs) < cantidad:
        card = _build_valid_card(bin_prefix, length)
        if card in seen:
            continue
        seen.add(card)
        cvv = "".join(random.choices("0123456789", k=cvv_len))
        ccs.append(f"{card}|{mes}|{ano}|{cvv}")
    return ccs

def cargar_bin_db():
    BIN_FILE = "tarjetas.csv"
    if not os.path.exists(BIN_FILE):
        return {}
    db = {}
    with open(BIN_FILE, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            bin_num = row.get("bin", "").strip()
            if bin_num:
                db[bin_num] = dict(row)
    return db

def buscar_bin(cc, bin_db):
    for size in (8, 6):
        prefix = cc[:size]
        if prefix in bin_db:
            return bin_db[prefix]
    return {}