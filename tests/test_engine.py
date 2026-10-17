"""Engine checks against the demo bakery. Run: python tests/test_engine.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from till import engine as e  # noqa: E402

T, O = e.load_csv(), e.DEMO_OPENING_BALANCE


def test_balance_reconciles():
    assert e.snapshot(T, O)["balance"] == round(O + sum(t["amount"] for t in T), 2)


def test_finds_exactly_the_planted_problems():
    kinds = sorted((a["kind"], a["vendor"]) for a in e.anomalies(T))
    assert kinds == [("duplicate", "Golden Mill Flour Co"), ("new_vendor", "Rapid Oven Repair LLC"),
                     ("price_jump", "City Power and Water"), ("price_jump", "ShiftSlot Scheduling")], kinds


def test_anomaly_ids_are_stable():
    assert [a["id"] for a in e.anomalies(T)] == [a["id"] for a in e.anomalies(list(T))]


def test_monthly_bills_land_on_their_day():
    fc = e.forecast(T, O)
    rent = next(r for r in e.recurring_outflows(T) if r["vendor"] == "Maple St Properties")
    assert rent["cadence"] == "monthly"
    assert e._step(e._d("2026-10-01"), 30).isoformat() == "2026-11-01"
    assert e._step(e._d("2026-01-31"), 30).isoformat() == "2026-02-28"
    assert len(fc["weeks"]) == 13


def test_affordability_is_monotonic():
    lows = [e.affordability(T, O, monthly_cost=m)["low_after"] for m in (0, 500, 1500, 3000)]
    assert lows == sorted(lows, reverse=True), lows
    assert e.affordability(T, O, monthly_cost=0)["verdict"] == "comfortable"
    assert e.affordability(T, O, monthly_cost=3200)["verdict"] == "not yet"


def test_spending_change_defaults_to_last_full_month():
    r = e.spending_change(T)
    assert (r["month"], r["previous"]) == ("2026-09", "2026-08")


def test_engine_price_math():
    p = e.price_idea(1000, 10, 80, 0.30, 20.0)
    assert p["fixed_weekly"] == 300.0 and p["margin_per_cover"] == 14.0
    assert p["break_even_covers_per_week"] == 21.4 and p["monthly_cost"] == 1300.0
    assert [e.price_per_cover(x) for x in (None, 1, 1.5, 2, 4, 9)] == [22, 14, 18, 22, 52, 52]


def test_weekly_spend_matches_vendor_and_memo():
    s = e.weekly_spend(T, "flour")
    assert s["vendors"] == ["Golden Mill Flour Co"] and s["payments"] == 9
    assert s["weekly"] == round(-sum(t["amount"] for t in e._window(T, e.as_of(T), 56)
                                     if t["vendor"] == "Golden Mill Flour Co") / 8, 2)
    assert e.weekly_spend(T, "saffron")["payments"] == 0


def test_price_shock():
    s = e.weekly_spend(T, "flour")["weekly"]
    zero = e.price_shock(T, O, s, 0, weeks=3)
    assert zero["extra_weekly"] == 0 and zero["low_after"] == zero["low_before"]
    r = e.price_shock(T, O, s, 10, weeks=3)
    assert r["extra_weekly"] == round(s * 0.10, 2) and r["low_after"] < r["low_before"]
    assert r["paydays"] == ["2026-10-16", "2026-10-30"] and r["payroll"] == 6676.48   # biweekly, from the books
    assert len(r["window"]) == 3 and r["payroll_covered"]
    lows = [e.price_shock(T, O, s, p, weeks=3)["low_after"] for p in (0, 10, 50, 300)]
    assert lows == sorted(lows, reverse=True)
    assert e.price_shock(T, O, s, 300, weeks=3)["verdict"] == "not yet"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print("ALL ENGINE CHECKS PASSED")
