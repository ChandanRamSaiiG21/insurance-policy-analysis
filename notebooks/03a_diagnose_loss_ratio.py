# ============================================================
# diagnose_loss_ratio.py
# Checks whether the 129.8% loss ratio is a data issue
# or a measurement issue (duplicate join)
# ============================================================

import os
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote_plus
from sqlalchemy import create_engine, text

load_dotenv(
    dotenv_path=Path("D:/DataAnalyticsProjects/insurance-policy-analysis/.env"),
    encoding="utf-8-sig"
)
_pwd   = quote_plus(os.getenv("DB_PASSWORD", "NOT_FOUND"))
engine = create_engine(
    f"postgresql+psycopg2://postgres:{_pwd}@localhost:5432/insurance_policy_db",
    echo=False
)

print("="*60)
print("LOSS RATIO DIAGNOSTICS")
print("="*60)

with engine.connect() as conn:

    # Method 1: Original query (JOIN claims to premiums — may duplicate)
    lr1 = conn.execute(text("""
        SELECT ROUND(
            SUM(c.approved_amount) / NULLIF(SUM(pr.amount_paid), 0) * 100, 1
        )
        FROM fact_claims c
        JOIN fact_premiums pr ON c.policy_id = pr.policy_id
        WHERE pr.payment_status = 'Paid'
    """)).scalar()
    print(f"\nMethod 1 (original JOIN):         {lr1}%")

    # Method 2: Aggregate each side separately first, then divide
    lr2 = conn.execute(text("""
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
        SELECT ROUND(
            claims_paid / NULLIF(premiums_collected, 0) * 100, 1
        )
        FROM total_claims, total_premiums
    """)).scalar()
    print(f"Method 2 (separate aggregates):   {lr2}%  ← correct method")

    # Raw numbers
    total_claims = conn.execute(text("""
        SELECT ROUND(SUM(approved_amount) / 1e7, 2)
        FROM fact_claims
        WHERE claim_status IN ('Settled', 'Approved')
    """)).scalar()
    total_premiums = conn.execute(text("""
        SELECT ROUND(SUM(amount_paid) / 1e7, 2)
        FROM fact_premiums
        WHERE payment_status = 'Paid'
    """)).scalar()
    print(f"\nTotal claims paid:    ₹{total_claims} Cr")
    print(f"Total premiums collected: ₹{total_premiums} Cr")
    print(f"True loss ratio:      {round(float(total_claims)/float(total_premiums)*100,1)}%")

    # Loss ratio by product line
    print("\nLoss ratio by product line (Method 2):")
    rows = conn.execute(text("""
        WITH claims_by_product AS (
            SELECT dp.product_line,
                   SUM(c.approved_amount) AS claims_paid
            FROM fact_claims c
            JOIN fact_policies fp ON c.policy_id = fp.policy_id
            JOIN dim_products dp  ON fp.product_id = dp.product_id
            WHERE c.claim_status IN ('Settled', 'Approved')
            GROUP BY dp.product_line
        ),
        premiums_by_product AS (
            SELECT dp.product_line,
                   SUM(pr.amount_paid) AS premiums_collected
            FROM fact_premiums pr
            JOIN fact_policies fp ON pr.policy_id = fp.policy_id
            JOIN dim_products dp  ON fp.product_id = dp.product_id
            WHERE pr.payment_status = 'Paid'
            GROUP BY dp.product_line
        )
        SELECT c.product_line,
               ROUND(c.claims_paid / NULLIF(p.premiums_collected, 0) * 100, 1) AS loss_ratio
        FROM claims_by_product c
        JOIN premiums_by_product p ON c.product_line = p.product_line
        ORDER BY loss_ratio DESC
    """)).fetchall()
    for row in rows:
        status = "✅" if 70 <= float(row[1]) <= 110 else "⚠️"
        print(f"  {status} {row[0]:<12} {row[1]}%")

    # Average claim vs average annual premium
    avg_claim = conn.execute(text(
        "SELECT ROUND(AVG(approved_amount), 0) FROM fact_claims WHERE claim_status IN ('Settled','Approved')"
    )).scalar()
    avg_premium = conn.execute(text(
        "SELECT ROUND(AVG(annual_premium), 0) FROM fact_policies"
    )).scalar()
    claim_freq = conn.execute(text("""
        SELECT ROUND(
            COUNT(DISTINCT c.policy_id)::numeric /
            COUNT(DISTINCT fp.policy_id) * 100, 1
        )
        FROM fact_policies fp
        LEFT JOIN fact_claims c ON fp.policy_id = c.policy_id
    """)).scalar()
    print(f"\nAvg approved claim:   ₹{avg_claim:,}")
    print(f"Avg annual premium:   ₹{avg_premium:,}")
    print(f"Claim frequency:      {claim_freq}%  (generated at 15%)")
    print(f"\nImplication: avg claim/avg premium ratio = {round(float(avg_claim)/float(avg_premium)*100,1)}%")
    print("If this is > 110%, the claim amounts are too high relative to premiums.")
    print("If Method 2 loss ratio is 70-110%, the data is fine — original query had duplicate join.")