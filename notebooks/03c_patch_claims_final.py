# ============================================================
# 03d_patch_claims_final.py
# Final claims regeneration — loss factor calibrated to
# achieve portfolio loss ratio of 70–110%
#
# Diagnosis from 03c output:
#   Claim frequency:  39.4%  ✅ correct
#   Loss ratio:       27.7%  ❌ too low
#   Approved claims:  ₹393 Cr vs ₹1,424 Cr premiums
#
# Root cause: loss_factor range too low.
# Fix: multiply loss factors by ~3x
#   High risk (70+): 2.0–4.5× annual premium per claim
#   Mid  risk (50+): 1.5–3.0× annual premium per claim
#   Low  risk (<50): 0.8–2.0× annual premium per claim
#
# With 70% approval rate on Settled/Approved (50% of claims):
#   Effective payout ≈ loss_factor × 0.7 × 50% status weight
#   High risk avg ≈ 3.25 × 0.7 × 0.7 = 1.59 → ~159% per claim
#   Portfolio avg with 39% frequency ≈ 85–95% loss ratio ✅
# ============================================================

import random
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

random.seed(42)
np.random.seed(42)

connection_url = URL.create(
    drivername="postgresql+psycopg2",
    username="postgres",
    password="IIM@ABCLKi12",
    host="localhost",
    port=5432,
    database="insurance_policy_db"
)
engine = create_engine(connection_url, echo=False)

with engine.connect() as conn:
    print(f"Connected to: {conn.execute(text('SELECT current_database()')).scalar()}")

print("Truncating fact_claims and fact_renewals...")
with engine.begin() as conn:
    conn.execute(text("TRUNCATE TABLE fact_renewals RESTART IDENTITY CASCADE"))
    conn.execute(text("TRUNCATE TABLE fact_claims RESTART IDENTITY CASCADE"))
print("Cleared.")

# ── Generate fact_claims ─────────────────────────────────────
def generate_fact_claims():
    print("\nGenerating fact_claims (final calibration)...")

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
        risk = pol["risk_score"]

        # Frequency driven by risk score (same as 03c — working correctly)
        if   risk >= 70: freq = 0.55
        elif risk >= 55: freq = 0.42
        elif risk >= 40: freq = 0.35
        else:            freq = 0.25

        if random.random() > freq:
            continue

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

            # Loss factors recalibrated now that approved = full claim_amt
            # (previously discounted by 0.7 which was incorrect)
            # Effective payout = loss_factor × 70% status weight (Settled+Approved)
            # Target portfolio LR 75–100%:
            #   need avg_factor × 0.70 × 39% freq ≈ 85% → avg_factor ≈ 3.1
            if   risk >= 70: loss_factor = random.uniform(1.5, 3.5)
            elif risk >= 55: loss_factor = random.uniform(1.0, 2.5)
            elif risk >= 40: loss_factor = random.uniform(0.7, 1.8)
            else:            loss_factor = random.uniform(0.5, 1.5)

            claim_amt  = round(pol["annual_premium"] * loss_factor, 2)

            # Correct approval logic:
            # Settled/Approved → insurer pays full claim amount
            # Rejected         → insurer pays nothing, full amount rejected
            # Under Review / Filed → pending, no payout yet
            if status in ["Settled", "Approved"]:
                approved = claim_amt
                rejected = 0
            elif status == "Rejected":
                approved = 0
                rejected = claim_amt
            else:
                approved = 0
                rejected = 0
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

    approved_total = df[df["claim_status"].isin(["Settled","Approved"])]["approved_amount"].sum()
    print(f"  fact_claims done — {len(df):,} rows")
    print(f"  Total approved: ₹{approved_total/1e7:.1f} Cr  (need ~₹1,100–1,500 Cr for 80–100% LR)")

# ── Generate fact_renewals ───────────────────────────────────
def generate_fact_renewals():
    print("\nGenerating fact_renewals...")

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

# ── Run ──────────────────────────────────────────────────────
generate_fact_claims()
generate_fact_renewals()

# ── Final validation ─────────────────────────────────────────
print("\n" + "="*60)
print("FINAL VALIDATION:")
print("="*60)
with engine.connect() as conn:

    for table in ["fact_claims", "fact_renewals"]:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        print(f"  {table:<25} {count:>10,} rows")

    loss_ratio = conn.execute(text("""
        WITH c AS (SELECT SUM(approved_amount) AS cp
                   FROM fact_claims
                   WHERE claim_status IN ('Settled','Approved')),
             p AS (SELECT SUM(amount_paid) AS pp
                   FROM fact_premiums
                   WHERE payment_status = 'Paid')
        SELECT ROUND(cp / NULLIF(pp,0) * 100, 1) FROM c, p
    """)).scalar()
    print(f"\n  Portfolio loss ratio:    {loss_ratio}%   (target: 70–110%)")

    freq = conn.execute(text("""
        SELECT ROUND(COUNT(DISTINCT c.policy_id)::numeric /
               COUNT(DISTINCT fp.policy_id) * 100, 1)
        FROM fact_policies fp
        LEFT JOIN fact_claims c ON fp.policy_id = c.policy_id
    """)).scalar()
    print(f"  Claim frequency:         {freq}%   (target: 35–45%)")

    avg_clv = conn.execute(text(
        "SELECT ROUND(AVG(clv_at_renewal),0) FROM fact_renewals"
    )).scalar()
    pos_clv = conn.execute(text("""
        SELECT ROUND(AVG(CASE WHEN clv_at_renewal > 0
               THEN 1.0 ELSE 0.0 END)*100,1) FROM fact_renewals
    """)).scalar()
    neg_clv = conn.execute(text("""
        SELECT ROUND(AVG(CASE WHEN clv_at_renewal <= 0
               THEN 1.0 ELSE 0.0 END)*100,1) FROM fact_renewals
    """)).scalar()
    print(f"  Avg CLV:                 ₹{avg_clv:,}")
    print(f"  Positive CLV:            {pos_clv}%")
    print(f"  Negative CLV:            {neg_clv}%")

    print("\n  Loss ratio by product line:")
    rows = conn.execute(text("""
        WITH c AS (
            SELECT dp.product_line, SUM(fc.approved_amount) AS cp
            FROM fact_claims fc
            JOIN fact_policies fp ON fc.policy_id = fp.policy_id
            JOIN dim_products dp  ON fp.product_id = dp.product_id
            WHERE fc.claim_status IN ('Settled','Approved')
            GROUP BY dp.product_line
        ),
        p AS (
            SELECT dp.product_line, SUM(pr.amount_paid) AS pp
            FROM fact_premiums pr
            JOIN fact_policies fp ON pr.policy_id = fp.policy_id
            JOIN dim_products dp  ON fp.product_id = dp.product_id
            WHERE pr.payment_status = 'Paid'
            GROUP BY dp.product_line
        )
        SELECT c.product_line,
               ROUND(c.cp / NULLIF(p.pp,0)*100,1) AS lr
        FROM c JOIN p ON c.product_line = p.product_line
        ORDER BY lr DESC
    """)).fetchall()
    for row in rows:
        status = "✅" if 60 <= float(row[1]) <= 130 else "⚠️"
        print(f"    {status} {row[0]:<12} {row[1]}%")

    print("\n  Lapse rate by income (sanity check):")
    rows = conn.execute(text("""
        SELECT dc.income_bracket,
               ROUND(AVG(fp.is_lapsed::int)*100,1) AS lapse_pct
        FROM fact_policies fp
        JOIN dim_customers dc ON fp.customer_id = dc.customer_id
        GROUP BY dc.income_bracket
        ORDER BY lapse_pct DESC
    """)).fetchall()
    for row in rows:
        print(f"    {row[0]:<12} {row[1]}%")