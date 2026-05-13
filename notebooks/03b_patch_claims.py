# ============================================================
# 03c_patch_claims.py
# Regenerates ONLY fact_claims with calibrated parameters
# to achieve portfolio loss ratio of 70–110%
#
# Root cause of 5.5% loss ratio:
#   - Claim frequency: 13% (too low, need ~40%)
#   - Avg claim / avg premium: 51% (need 60–90% per claim)
#
# Fix:
#   - Claim frequency: 40% of non-cancelled policies
#   - loss_factor: Uniform(0.5, 1.5) — allows claim > 1 year premium
#     which is realistic (multi-year policies, large medical/motor claims)
#   - approval rate stays at 70% of settled/approved claims
#
# Expected outcome:
#   Loss ratio = 40% frequency × ~80% avg severity = ~72–90%
# ============================================================

import random
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

import os
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote_plus
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

random.seed(42)
np.random.seed(42)

load_dotenv(
    dotenv_path=Path("D:/DataAnalyticsProjects/insurance-policy-analysis/.env"),
    encoding="utf-8-sig"
)
_pwd   = quote_plus(os.getenv("DB_PASSWORD", "NOT_FOUND"))
engine = create_engine(
    f"postgresql+psycopg2://postgres:{_pwd}@localhost:5432/insurance_policy_db",
    echo=False
)

with engine.connect() as conn:
    print(f"Connected to: {conn.execute(text('SELECT current_database()')).scalar()}")

# Truncate only fact_claims
print("Truncating fact_claims...")
with engine.begin() as conn:
    conn.execute(text("TRUNCATE TABLE fact_claims RESTART IDENTITY CASCADE"))
print("fact_claims cleared.")

def generate_fact_claims():
    print("Generating fact_claims (calibrated)...")

    with engine.connect() as conn:
        policies = pd.read_sql(
            """SELECT policy_id, sum_insured, annual_premium,
                      policy_start_date_id, policy_end_date_id,
                      risk_score
               FROM fact_policies
               WHERE policy_status != 'Cancelled'""",
            conn
        )
        date_ids = [r[0] for r in conn.execute(
            text("SELECT date_id FROM dim_date ORDER BY date_id")
        )]

    print(f"  Eligible policies: {len(policies):,}")

    CLAIM_TYPES    = ["Own Damage", "Third Party", "Theft", "Natural Disaster", "Medical"]
    CLAIM_STATUSES = ["Settled", "Approved", "Rejected", "Under Review", "Filed"]
    STATUS_WEIGHTS = [50, 20, 15, 10, 5]
    REJECT_REASONS = [
        "Policy lapsed", "Pre-existing condition", "Outside coverage",
        "Fraudulent claim", "Documentation incomplete"
    ]

    rows = []
    for _, pol in policies.iterrows():

        # Claim frequency driven by risk_score
        # High risk (score 70+) → 55% chance of claim
        # Low risk  (score <40) → 25% chance of claim
        # Base: 40% — gives portfolio frequency ~40%
        risk = pol["risk_score"]
        if   risk >= 70: freq = 0.55
        elif risk >= 55: freq = 0.42
        elif risk >= 40: freq = 0.35
        else:            freq = 0.25

        if random.random() > freq:
            continue

        # Higher risk → more claims possible
        if risk >= 70:
            num_claims = random.choices([1, 2, 3], weights=[65, 25, 10])[0]
        else:
            num_claims = random.choices([1, 2, 3], weights=[80, 15,  5])[0]

        start_pos = next((i for i, d in enumerate(date_ids)
                          if d >= pol["policy_start_date_id"]), 0)
        end_pos   = next((i for i, d in enumerate(date_ids)
                          if d >= pol["policy_end_date_id"]), len(date_ids) - 1)

        for _ in range(num_claims):
            inc_pos    = random.randint(start_pos, max(start_pos, end_pos - 1))
            inc_did    = date_ids[inc_pos]
            filed_pos  = min(inc_pos + random.randint(1, 30), len(date_ids) - 1)
            filed_did  = date_ids[filed_pos]
            status     = random.choices(CLAIM_STATUSES, weights=STATUS_WEIGHTS)[0]

            # Loss factor: 0.5–1.5× annual premium
            # High risk policies skewed toward higher claims
            if risk >= 70:
                loss_factor = random.uniform(0.7, 1.5)
            elif risk >= 50:
                loss_factor = random.uniform(0.5, 1.2)
            else:
                loss_factor = random.uniform(0.3, 0.9)

            claim_amt  = round(pol["annual_premium"] * loss_factor, 2)
            approved   = round(claim_amt * random.uniform(0.70, 1.0), 2) \
                         if status in ["Settled", "Approved"] else 0
            rejected   = round(claim_amt - approved, 2) \
                         if status == "Rejected" else 0
            settle_pos = min(filed_pos + random.randint(7, 90), len(date_ids) - 1) \
                         if status in ["Settled", "Approved"] else None
            settle_did = date_ids[settle_pos] if settle_pos else None
            days_settle = (settle_pos - filed_pos) if settle_pos else None

            rows.append({
                "policy_id"          : pol["policy_id"],
                "incident_date_id"   : inc_did,
                "filed_date_id"      : filed_did,
                "settled_date_id"    : settle_did,
                "claim_type"         : random.choice(CLAIM_TYPES),
                "claim_status"       : status,
                "claim_amount"       : claim_amt,
                "approved_amount"    : approved,
                "rejected_amount"    : rejected,
                "days_to_settlement" : days_settle,
                "rejection_reason"   : random.choice(REJECT_REASONS)
                                       if status == "Rejected" else None
            })

    df = pd.DataFrame(rows)
    df.to_sql("fact_claims", engine, if_exists="append", index=False, chunksize=5000)
    print(f"  fact_claims done — {len(df):,} rows")

    # Quick in-memory loss ratio check
    approved_total = df[df["claim_status"].isin(["Settled", "Approved"])]["approved_amount"].sum()
    print(f"  Total approved claims: ₹{approved_total/1e7:.2f} Cr")

generate_fact_claims()

# Also need to regenerate fact_renewals since CLV depends on claims paid
print("\nRegenerating fact_renewals (CLV depends on claims)...")

with engine.begin() as conn:
    conn.execute(text("TRUNCATE TABLE fact_renewals RESTART IDENTITY CASCADE"))
print("fact_renewals cleared.")

def generate_fact_renewals():
    with engine.connect() as conn:
        policies = pd.read_sql(
            """SELECT p.policy_id, p.annual_premium, p.policy_tenure_months,
                      p.policy_end_date_id, p.policy_start_date_id,
                      p.risk_score, p.is_lapsed
               FROM fact_policies p
               WHERE p.policy_status IN ('Renewed', 'Lapsed', 'Expired')""",
            conn
        )
        claims = pd.read_sql(
            """SELECT policy_id,
                      COALESCE(SUM(approved_amount), 0) AS total_claims_paid
               FROM fact_claims
               GROUP BY policy_id""",
            conn
        )
        date_ids = [r[0] for r in conn.execute(
            text("SELECT date_id FROM dim_date ORDER BY date_id")
        )]

    policies = policies.merge(claims, on="policy_id", how="left")
    policies["total_claims_paid"] = policies["total_claims_paid"].fillna(0)

    NON_RENEWAL_REASONS = ["Price", "Competitor", "No Response", "Total Loss", "Moved abroad"]
    rows = []

    for _, pol in policies.iterrows():
        renewed    = False if pol["is_lapsed"] else \
                     random.choices([True, False], weights=[60, 40])[0]
        renew_pos  = next((i for i, d in enumerate(date_ids)
                           if d >= pol["policy_end_date_id"]), len(date_ids) - 1)
        renew_did  = date_ids[renew_pos]
        new_prem   = round(pol["annual_premium"] * random.uniform(0.95, 1.15), 2) \
                     if renewed else None
        change_pct = round((new_prem - pol["annual_premium"]) /
                           pol["annual_premium"] * 100, 2) if renewed else None

        tenure_years       = pol["policy_tenure_months"] / 12
        premiums_collected = round(pol["annual_premium"] * tenure_years, 2)
        acquisition_cost   = round(pol["annual_premium"] * 0.15, 2)
        clv = round(premiums_collected - pol["total_claims_paid"] - acquisition_cost, 2)

        rows.append({
            "policy_id"              : pol["policy_id"],
            "new_policy_id"          : None,
            "renewal_date_id"        : renew_did,
            "original_start_date_id" : pol["policy_start_date_id"],
            "renewal_number"         : 1,
            "previous_premium"       : pol["annual_premium"],
            "new_premium"            : new_prem,
            "premium_change_pct"     : change_pct,
            "renewed"                : renewed,
            "non_renewal_reason"     : random.choice(NON_RENEWAL_REASONS)
                                       if not renewed else None,
            "clv_at_renewal"         : clv
        })

    df = pd.DataFrame(rows)
    df.to_sql("fact_renewals", engine, if_exists="append", index=False, chunksize=5000)

    pos_pct = (df["clv_at_renewal"] > 0).mean() * 100
    neg_pct = (df["clv_at_renewal"] <= 0).mean() * 100
    avg_clv = df["clv_at_renewal"].mean()
    print(f"  fact_renewals done — {len(df):,} rows")
    print(f"  CLV: {pos_pct:.1f}% positive | {neg_pct:.1f}% negative | Avg: ₹{avg_clv:,.0f}")

generate_fact_renewals()

# ── Final validation ─────────────────────────────────────────
print("\n" + "="*60)
print("FINAL VALIDATION:")
print("="*60)
with engine.connect() as conn:

    for table in ["fact_claims", "fact_renewals"]:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        print(f"  {table:<25} {count:>10,} rows")

    # Correct loss ratio (Method 2 — separate aggregates)
    loss_ratio = conn.execute(text("""
        WITH total_claims AS (
            SELECT SUM(approved_amount) AS claims_paid
            FROM fact_claims
            WHERE claim_status IN ('Settled', 'Approved')
        ),
        total_premiums AS (
            SELECT SUM(amount_paid) AS premiums_collected
            FROM fact_premiums
            WHERE payment_status = 'Paid'
        )
        SELECT ROUND(claims_paid / NULLIF(premiums_collected, 0) * 100, 1)
        FROM total_claims, total_premiums
    """)).scalar()
    print(f"\n  Portfolio loss ratio:    {loss_ratio}%   (target: 70–110%)")

    avg_clv = conn.execute(text(
        "SELECT ROUND(AVG(clv_at_renewal), 0) FROM fact_renewals"
    )).scalar()
    pos_clv = conn.execute(text("""
        SELECT ROUND(AVG(CASE WHEN clv_at_renewal > 0 THEN 1.0 ELSE 0.0 END)*100,1)
        FROM fact_renewals
    """)).scalar()
    neg_clv = conn.execute(text("""
        SELECT ROUND(AVG(CASE WHEN clv_at_renewal <= 0 THEN 1.0 ELSE 0.0 END)*100,1)
        FROM fact_renewals
    """)).scalar()
    print(f"  Avg CLV:                 ₹{avg_clv:,}")
    print(f"  Positive CLV:            {pos_clv}%")
    print(f"  Negative CLV:            {neg_clv}%")

    freq = conn.execute(text("""
        SELECT ROUND(
            COUNT(DISTINCT c.policy_id)::numeric /
            COUNT(DISTINCT fp.policy_id) * 100, 1
        )
        FROM fact_policies fp
        LEFT JOIN fact_claims c ON fp.policy_id = c.policy_id
    """)).scalar()
    print(f"  Claim frequency:         {freq}%   (target: ~35–45%)")

    print("\n  Loss ratio by product line:")
    rows = conn.execute(text("""
        WITH c AS (
            SELECT dp.product_line, SUM(fc.approved_amount) AS claims_paid
            FROM fact_claims fc
            JOIN fact_policies fp ON fc.policy_id = fp.policy_id
            JOIN dim_products dp  ON fp.product_id = dp.product_id
            WHERE fc.claim_status IN ('Settled','Approved')
            GROUP BY dp.product_line
        ),
        p AS (
            SELECT dp.product_line, SUM(pr.amount_paid) AS premiums_collected
            FROM fact_premiums pr
            JOIN fact_policies fp ON pr.policy_id = fp.policy_id
            JOIN dim_products dp  ON fp.product_id = dp.product_id
            WHERE pr.payment_status = 'Paid'
            GROUP BY dp.product_line
        )
        SELECT c.product_line,
               ROUND(c.claims_paid / NULLIF(p.premiums_collected,0)*100,1) AS lr
        FROM c JOIN p ON c.product_line = p.product_line
        ORDER BY lr DESC
    """)).fetchall()
    for row in rows:
        status = "✅" if 60 <= float(row[1]) <= 130 else "⚠️"
        print(f"    {status} {row[0]:<12} {row[1]}%")