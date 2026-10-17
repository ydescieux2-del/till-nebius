"""Synthetic books for a fictional bakery ("Sunrise Bakery"). No real business or person.

    python3 -m till.demo_data      # writes data/demo_bakery.csv

Deterministic (fixed seed). Four problems are planted so the demo has something to find:
a duplicate supplier payment, a software price creep, a utility spike, and a large first-time vendor.
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

START, END = date(2026, 4, 1), date(2026, 10, 13)
OPENING_BALANCE = 18_000.00
OUT = Path(__file__).resolve().parent.parent / "data" / "demo_bakery.csv"


def generate():
    rng = random.Random(20261014)
    rows = []

    def add(d, vendor, category, amount, memo=""):
        rows.append({"date": d.isoformat(), "vendor": vendor, "category": category,
                     "amount": round(amount, 2), "memo": memo})

    d = START
    while d <= END:
        wd = d.weekday()
        # card-sales deposits, closed Mondays; summer is a little slower
        if wd != 0:
            base = 1_360 if wd >= 4 else 935
            season = 0.9 if d.month in (7, 8) else 1.0
            add(d, "Card sales deposit", "Sales", base * season * rng.uniform(0.85, 1.15))
        if wd == 1:
            add(d, "Golden Mill Flour Co", "Ingredients", -rng.uniform(820, 980), "weekly flour and sugar")
        if wd == 3:
            add(d, "Valley Dairy", "Ingredients", -rng.uniform(380, 470), "weekly dairy")
        if wd == 4 and (d - START).days // 7 % 2 == 0:
            add(d, "Payroll", "Payroll", -rng.uniform(6_650, 6_950), "biweekly payroll, 5 staff")
        if d.day == 1:
            add(d, "Maple St Properties", "Rent", -4_200, "monthly rent")
        if d.day == 5:
            add(d, "Eastside Equipment Finance", "Loan", -780, "oven loan")
        if d.day == 8:
            add(d, "City Power and Water", "Utilities",
                -(1_390 if d.month == 9 else rng.uniform(610, 690)), "monthly utilities")
        if d.day == 12:
            add(d, "TillPOS", "Software", -89)
            add(d, "Bookkeeper Cloud", "Software", -60)
            add(d, "ShiftSlot Scheduling", "Software", -(129 if d.month >= 9 else 45))
        if d.day == 20:
            add(d, "Card processor fees", "Fees", -rng.uniform(780, 900))
        if d.day == 15 and d.month in (4, 7, 10):
            add(d, "Harbor Mutual Insurance", "Insurance", -2_340, "quarterly policy")
        if wd == 5 and rng.random() < 0.5:
            add(d, "Corner Hardware", "Supplies", -rng.uniform(40, 220))
        d += timedelta(days=1)

    # planted problems
    dup = next(r for r in rows if r["vendor"] == "Golden Mill Flour Co" and r["date"] >= "2026-09-22")
    dd = date.fromisoformat(dup["date"]) + timedelta(days=2)
    add(dd, dup["vendor"], dup["category"], dup["amount"], "weekly flour and sugar")
    add(date(2026, 10, 2), "Rapid Oven Repair LLC", "Repairs", -2_450, "emergency oven repair")

    rows.sort(key=lambda r: (r["date"], r["vendor"]))
    return rows


def main():
    rows = generate()
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "vendor", "category", "amount", "memo"])
        w.writeheader()
        w.writerows(rows)
    print(f"{OUT}  {len(rows)} rows  net {sum(r['amount'] for r in rows):,.2f}")


if __name__ == "__main__":
    main()
