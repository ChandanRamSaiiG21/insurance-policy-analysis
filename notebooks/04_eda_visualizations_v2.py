"""
Insurance Policy Lifecycle Analysis
File   : notebooks/04_eda_visualizations.py
Day    : 3 — EDA Python Visualizations (v2 — CLV fan-out fixed)
Author : Chandan Ram Saii
Env    : D:\\Anaconda\\python.exe
DB     : insurance_policy_db (PostgreSQL 18, user=postgres)
Output : reports/  (5 PNG charts, 300 dpi)

Run from project root:
    D:\\Anaconda\\python.exe notebooks\\04_eda_visualizations.py

FIXES vs original:
  - SQL_CLV: pre-aggregate premiums and claims per customer separately
    before joining (fixes fan-out / row multiplication bug)
  - SQL_CLV: NTILE(5) now maps all 5 quintiles correctly
    (original only mapped 4 — quintile 5 was invisible)
"""

import os
import warnings
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
from urllib.parse import quote_plus
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore")
matplotlib.rcParams["figure.dpi"] = 150

# ─────────────────────────────────────────────────────────────
# 0. CONFIG
# ─────────────────────────────────────────────────────────────
DB_USER = "postgres"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "insurance_policy_db"

REPORTS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "reports", "v2_calibrated"
)
os.makedirs(REPORTS_DIR, exist_ok=True)

PALETTE = {
    "blue"  : "#2563EB",
    "teal"  : "#0F6E56",
    "amber" : "#BA7517",
    "red"   : "#A32D2D",
    "purple": "#534AB7",
    "gray"  : "#5F5E5A",
    "bg"    : "#F8F7F4",
    "grid"  : "#E5E3DC",
}
FONT = {
    "family": "DejaVu Sans",
    "title" : 14,
    "axis"  : 11,
    "tick"  : 9,
    "annot" : 8,
}
plt.rcParams.update({
    "font.family"      : FONT["family"],
    "axes.spines.top"  : False,
    "axes.spines.right": False,
    "axes.grid"        : True,
    "grid.color"       : PALETTE["grid"],
    "grid.linewidth"   : 0.6,
    "figure.facecolor" : PALETTE["bg"],
    "axes.facecolor"   : PALETTE["bg"],
})


# ─────────────────────────────────────────────────────────────
# 1. DB CONNECTION
# ─────────────────────────────────────────────────────────────
def get_engine():
    password = quote_plus("IIM@ABCLKi12")
    url = (
        f"postgresql+psycopg2://{DB_USER}:{password}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    return create_engine(url, pool_pre_ping=True)

def run_query(engine, sql: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)


# ─────────────────────────────────────────────────────────────
# 2. Q1 — LAPSE CURVE (unchanged — no fan-out risk)
# ─────────────────────────────────────────────────────────────
SQL_LAPSE = """
WITH monthly_lapse AS (
    SELECT
        dp.product_line,
        dd.year,
        dd.month,
        COUNT(*)                                        AS total_policies,
        COUNT(*) FILTER (WHERE fp.is_lapsed = TRUE)    AS lapsed_policies
    FROM fact_policies fp
    JOIN dim_products dp ON fp.product_id           = dp.product_id
    JOIN dim_date     dd ON fp.policy_start_date_id = dd.date_id
    GROUP BY dp.product_line, dd.year, dd.month
)
SELECT
    product_line,
    year || '-' || LPAD(month::TEXT, 2, '0') AS year_month,
    ROUND(100.0 * lapsed_policies / NULLIF(total_policies, 0), 2) AS lapse_rate_pct
FROM monthly_lapse
ORDER BY product_line, year_month;
"""

def chart_lapse_curve(engine):
    df = run_query(engine, SQL_LAPSE)
    if df.empty:
        print("  [SKIP] No data for lapse curve.")
        return

    pivot = df.pivot(
        index="year_month", columns="product_line", values="lapse_rate_pct"
    ).fillna(0)

    fig, ax = plt.subplots(figsize=(13, 5))
    fig.patch.set_facecolor(PALETTE["bg"])

    colors = [PALETTE["blue"], PALETTE["teal"],
              PALETTE["amber"], PALETTE["red"], PALETTE["purple"]]

    for i, col in enumerate(pivot.columns):
        ax.plot(
            pivot.index, pivot[col],
            label=col, color=colors[i % len(colors)],
            linewidth=2, marker="o", markersize=3,
        )

    ticks = range(0, len(pivot.index), 6)
    ax.set_xticks(list(ticks))
    ax.set_xticklabels(
        [pivot.index[t] for t in ticks],
        rotation=45, ha="right", fontsize=FONT["tick"]
    )
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
    ax.set_title(
        "Q1 — Monthly lapse rate by product line",
        fontsize=FONT["title"], fontweight="bold", pad=12
    )
    ax.set_xlabel("Month", fontsize=FONT["axis"])
    ax.set_ylabel("Lapse rate (%)", fontsize=FONT["axis"])
    ax.legend(
        title="Product line", fontsize=FONT["tick"],
        title_fontsize=FONT["tick"], loc="upper left"
    )
    fig.tight_layout()
    out = os.path.join(REPORTS_DIR, "01_lapse_curve.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved -> {out}")


# ─────────────────────────────────────────────────────────────
# 3. Q2 — LOSS RATIO HEATMAP (unchanged — already correct)
# ─────────────────────────────────────────────────────────────
SQL_LOSS = """
WITH premiums_agg AS (
    SELECT policy_id, SUM(amount_paid) AS total_premium
    FROM fact_premiums
    WHERE payment_status = 'Paid'
    GROUP BY policy_id
),
claims_agg AS (
    SELECT policy_id, SUM(approved_amount) AS total_approved_claims
    FROM fact_claims
    WHERE claim_status IN ('Settled', 'Approved')
    GROUP BY policy_id
)
SELECT
    dc.state,
    da.channel,
    ROUND(
        100.0 * COALESCE(SUM(ca.total_approved_claims), 0)
              / NULLIF(SUM(pa.total_premium), 0), 2
    ) AS loss_ratio_pct
FROM fact_policies fp
JOIN dim_customers dc   ON fp.customer_id = dc.customer_id
JOIN dim_agents    da   ON fp.agent_id    = da.agent_id
JOIN premiums_agg  pa   ON fp.policy_id   = pa.policy_id
LEFT JOIN claims_agg ca ON fp.policy_id   = ca.policy_id
GROUP BY dc.state, da.channel
ORDER BY dc.state, loss_ratio_pct DESC;
"""

def chart_loss_ratio_heatmap(engine):
    df = run_query(engine, SQL_LOSS)
    if df.empty:
        print("  [SKIP] No data for loss ratio heatmap.")
        return

    pivot = df.pivot_table(
        index="state", columns="channel",
        values="loss_ratio_pct", aggfunc="mean"
    ).fillna(0)

    fig, ax = plt.subplots(figsize=(10, max(6, len(pivot) * 0.4)))
    fig.patch.set_facecolor(PALETTE["bg"])

    sns.heatmap(
        pivot, ax=ax,
        annot=True, fmt=".1f", annot_kws={"size": FONT["annot"]},
        cmap="RdYlGn_r",
        linewidths=0.5, linecolor="#D3D1C7",
        cbar_kws={"label": "Loss ratio (%)"},
    )
    ax.set_title(
        "Q2 — Loss ratio (%) by state × sales channel",
        fontsize=FONT["title"], fontweight="bold", pad=12
    )
    ax.set_xlabel("Sales channel", fontsize=FONT["axis"])
    ax.set_ylabel("State", fontsize=FONT["axis"])
    ax.tick_params(axis="x", labelsize=FONT["tick"], rotation=30)
    ax.tick_params(axis="y", labelsize=FONT["tick"], rotation=0)

    fig.tight_layout()
    out = os.path.join(REPORTS_DIR, "02_loss_ratio_heatmap.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved -> {out}")


# ─────────────────────────────────────────────────────────────
# 4. Q3 — RENEWAL COHORT CHART (unchanged — no fan-out risk)
# ─────────────────────────────────────────────────────────────
SQL_RENEWAL = """
WITH tenure_map AS (
    SELECT
        fp.policy_id,
        EXTRACT(YEAR FROM AGE(CURRENT_DATE, dc.customer_since))::INT AS tenure_years
    FROM fact_policies fp
    JOIN dim_customers dc ON fp.customer_id = dc.customer_id
),
banded AS (
    SELECT
        policy_id, tenure_years,
        CASE
            WHEN tenure_years BETWEEN 0 AND 1  THEN '0-1 yr'
            WHEN tenure_years BETWEEN 2 AND 3  THEN '2-3 yrs'
            WHEN tenure_years BETWEEN 4 AND 6  THEN '4-6 yrs'
            WHEN tenure_years BETWEEN 7 AND 10 THEN '7-10 yrs'
            ELSE '10+ yrs'
        END AS tenure_band,
        CASE
            WHEN tenure_years BETWEEN 0 AND 1  THEN 1
            WHEN tenure_years BETWEEN 2 AND 3  THEN 2
            WHEN tenure_years BETWEEN 4 AND 6  THEN 3
            WHEN tenure_years BETWEEN 7 AND 10 THEN 4
            ELSE 5
        END AS band_order
    FROM tenure_map
)
SELECT
    b.tenure_band,
    b.band_order,
    COUNT(DISTINCT fr.policy_id)                              AS policies_up,
    COUNT(DISTINCT fr.renewal_id) FILTER (WHERE fr.renewed)  AS renewed,
    ROUND(
        100.0 * COUNT(DISTINCT fr.renewal_id) FILTER (WHERE fr.renewed)
              / NULLIF(COUNT(DISTINCT fr.policy_id), 0), 2
    )                                                         AS renewal_rate_pct
FROM banded b
JOIN fact_renewals fr ON b.policy_id = fr.policy_id
GROUP BY b.tenure_band, b.band_order
ORDER BY b.band_order;
"""

def chart_renewal_cohort(engine):
    df = run_query(engine, SQL_RENEWAL)
    if df.empty:
        print("  [SKIP] No data for renewal cohort.")
        return

    fig, ax1 = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor(PALETTE["bg"])
    ax2 = ax1.twinx()
    x, bar_w = np.arange(len(df)), 0.5

    ax1.bar(x, df["policies_up"], width=bar_w,
            color=PALETTE["blue"], alpha=0.75, label="Policies up for renewal")
    ax1.bar(x, df["renewed"], width=bar_w * 0.55,
            color=PALETTE["teal"], alpha=0.9, label="Renewed")

    ax2.plot(x, df["renewal_rate_pct"],
             color=PALETTE["amber"], linewidth=2.5,
             marker="D", markersize=7, label="Renewal rate %")
    for xi, val in zip(x, df["renewal_rate_pct"]):
        ax2.annotate(
            f"{val:.1f}%", (xi, val),
            textcoords="offset points", xytext=(0, 8),
            ha="center", fontsize=FONT["annot"], color=PALETTE["amber"]
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(df["tenure_band"], fontsize=FONT["tick"])
    ax1.set_xlabel("Customer tenure band", fontsize=FONT["axis"])
    ax1.set_ylabel("Policy count", fontsize=FONT["axis"])
    ax2.set_ylabel("Renewal rate (%)", fontsize=FONT["axis"])
    ax2.set_ylim(0, 115)

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, fontsize=FONT["tick"], loc="upper left")
    ax1.set_title(
        "Q3 — Renewal behavior by customer tenure",
        fontsize=FONT["title"], fontweight="bold", pad=12
    )
    ax1.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{int(v):,}")
    )
    fig.tight_layout()
    out = os.path.join(REPORTS_DIR, "03_renewal_cohort.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved -> {out}")


# ─────────────────────────────────────────────────────────────
# 5. Q4 — CLV SEGMENT BAR CHART
# FIXED: pre-aggregate premiums and claims per customer separately
# to avoid fan-out row multiplication.
# FIXED: NTILE(5) now correctly maps all 5 quintiles.
# ─────────────────────────────────────────────────────────────
SQL_CLV = """
WITH premiums_per_customer AS (
    SELECT
        fp.customer_id,
        SUM(fpr.amount_paid) AS total_premiums_paid
    FROM fact_policies fp
    JOIN fact_premiums  fpr ON fpr.policy_id = fp.policy_id
                            AND fpr.payment_status = 'Paid'
    GROUP BY fp.customer_id
),
claims_per_customer AS (
    SELECT
        fp.customer_id,
        SUM(fc.approved_amount) AS total_claims_paid
    FROM fact_policies fp
    JOIN fact_claims   fc  ON fc.policy_id = fp.policy_id
                           AND fc.claim_status IN ('Settled', 'Approved')
    GROUP BY fp.customer_id
),
customer_clv AS (
    SELECT
        dc.customer_id,
        COALESCE(ppc.total_premiums_paid, 0)
            - COALESCE(cpc.total_claims_paid, 0) AS clv_proxy
    FROM dim_customers dc
    LEFT JOIN premiums_per_customer ppc ON ppc.customer_id = dc.customer_id
    LEFT JOIN claims_per_customer   cpc ON cpc.customer_id = dc.customer_id
),
ranked AS (
    SELECT *, NTILE(5) OVER (ORDER BY clv_proxy DESC) AS quintile
    FROM customer_clv
)
SELECT
    CASE quintile
        WHEN 1 THEN 'Platinum'
        WHEN 2 THEN 'Gold'
        WHEN 3 THEN 'Silver'
        WHEN 4 THEN 'Bronze'
        WHEN 5 THEN 'Standard'
    END                               AS clv_segment,
    COUNT(*)                          AS customer_count,
    ROUND(AVG(clv_proxy)::NUMERIC, 2) AS avg_clv,
    ROUND(SUM(clv_proxy)::NUMERIC, 2) AS total_clv
FROM ranked
GROUP BY quintile
ORDER BY quintile;
"""

def chart_clv_segments(engine):
    df = run_query(engine, SQL_CLV)
    if df.empty:
        print("  [SKIP] No data for CLV segments.")
        return

    seg_colors = {
        "Platinum": "#7F77DD",
        "Gold"    : "#BA7517",
        "Silver"  : "#888780",
        "Bronze"  : "#D85A30",
        "Standard": "#A32D2D",
    }
    colors = [seg_colors.get(s, PALETTE["blue"]) for s in df["clv_segment"]]

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.patch.set_facecolor(PALETTE["bg"])

    # Left: customer count per segment
    axes[0].bar(df["clv_segment"], df["customer_count"],
                color=colors, alpha=0.88, edgecolor="white", linewidth=0.5)
    for i, v in enumerate(df["customer_count"]):
        axes[0].text(i, v + 50, f"{v:,}", ha="center",
                     fontsize=FONT["annot"], color=PALETTE["gray"])
    axes[0].set_title("Customers per CLV segment",
                      fontsize=FONT["title"] - 1, fontweight="bold", pad=10)
    axes[0].set_ylabel("Customer count", fontsize=FONT["axis"])
    axes[0].yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"{int(v):,}")
    )

    # Right: avg CLV per segment
    axes[1].bar(df["clv_segment"], df["avg_clv"],
                color=colors, alpha=0.88, edgecolor="white", linewidth=0.5)
    for i, v in enumerate(df["avg_clv"]):
        axes[1].text(i, max(v, 0) + 10, f"₹{v:,.0f}", ha="center",
                     fontsize=FONT["annot"], color=PALETTE["gray"])
    axes[1].set_title("Average CLV per segment",
                      fontsize=FONT["title"] - 1, fontweight="bold", pad=10)
    axes[1].set_ylabel("Avg CLV (₹)", fontsize=FONT["axis"])
    axes[1].yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"₹{v:,.0f}")
    )

    for ax in axes:
        ax.set_xlabel("CLV segment", fontsize=FONT["axis"])
        ax.set_facecolor(PALETTE["bg"])

    fig.suptitle("Q4 — Customer Lifetime Value segments",
                 fontsize=FONT["title"], fontweight="bold", y=1.02)
    fig.tight_layout()
    out = os.path.join(REPORTS_DIR, "04_clv_segments.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved -> {out}")


# ─────────────────────────────────────────────────────────────
# 6. Q5 — PRICING ADEQUACY (unchanged — already correct)
# ─────────────────────────────────────────────────────────────
SQL_PRICING = """
WITH claims_per_policy AS (
    SELECT policy_id, SUM(approved_amount) AS total_approved
    FROM fact_claims
    WHERE claim_status IN ('Settled', 'Approved')
    GROUP BY policy_id
)
SELECT
    dp.product_name,
    COUNT(DISTINCT fp.policy_id)                          AS policy_count,
    ROUND(AVG(fp.annual_premium)::NUMERIC, 2)             AS avg_annual_premium,
    ROUND(AVG(cp.total_approved)::NUMERIC, 2)             AS avg_claim,
    ROUND(
        AVG(fp.annual_premium) / NULLIF(AVG(cp.total_approved), 0), 3
    )::NUMERIC                                            AS adequacy_ratio
FROM fact_policies fp
JOIN dim_products       dp ON fp.product_id  = dp.product_id
LEFT JOIN claims_per_policy cp ON fp.policy_id = cp.policy_id
GROUP BY dp.product_name
ORDER BY adequacy_ratio ASC;
"""

def chart_pricing_adequacy(engine):
    df = run_query(engine, SQL_PRICING)
    if df.empty:
        print("  [SKIP] No data for pricing adequacy.")
        return

    n, x, bar_w = len(df), np.arange(len(df)), 0.38

    fig, ax = plt.subplots(figsize=(max(10, n * 1.1), 5))
    fig.patch.set_facecolor(PALETTE["bg"])

    ax.bar(x - bar_w / 2, df["avg_annual_premium"],
           width=bar_w, color=PALETTE["blue"], alpha=0.85,
           label="Avg annual premium")
    ax.bar(x + bar_w / 2, df["avg_claim"],
           width=bar_w, color=PALETTE["red"], alpha=0.85,
           label="Avg approved claim")

    ax2 = ax.twinx()
    ax2.plot(x, df["adequacy_ratio"], color=PALETTE["teal"],
             linewidth=2, marker="s", markersize=6, label="Adequacy ratio")
    ax2.axhline(1.0, color=PALETTE["gray"], linestyle="--",
                linewidth=1, alpha=0.7, label="Breakeven (1.0)")
    for xi, val in zip(x, df["adequacy_ratio"]):
        ax2.annotate(
            f"{val:.2f}", (xi, val),
            textcoords="offset points", xytext=(0, 8),
            ha="center", fontsize=FONT["annot"], color=PALETTE["teal"]
        )

    ax.set_xticks(x)
    ax.set_xticklabels(df["product_name"], rotation=35, ha="right",
                       fontsize=FONT["tick"])
    ax.set_ylabel("Amount (₹)", fontsize=FONT["axis"])
    ax2.set_ylabel("Adequacy ratio", fontsize=FONT["axis"])
    ax.set_title(
        "Q5 — Pricing adequacy: avg annual premium vs avg approved claim",
        fontsize=FONT["title"], fontweight="bold", pad=12
    )
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=FONT["tick"], loc="upper left")
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda v, _: f"₹{v:,.0f}")
    )
    fig.tight_layout()
    out = os.path.join(REPORTS_DIR, "05_pricing_adequacy.png")
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved -> {out}")


# ─────────────────────────────────────────────────────────────
# 7. MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("Insurance Policy Lifecycle — EDA Visualizations v2")
    print("=" * 60)

    engine = get_engine()
    print("  DB connection: OK\n")

    print("[1/5] Lapse curve ...")
    chart_lapse_curve(engine)

    print("[2/5] Loss ratio heatmap ...")
    chart_loss_ratio_heatmap(engine)

    print("[3/5] Renewal cohort chart ...")
    chart_renewal_cohort(engine)

    print("[4/5] CLV segments ...")
    chart_clv_segments(engine)

    print("[5/5] Pricing adequacy ...")
    chart_pricing_adequacy(engine)

    engine.dispose()
    print("\nAll 5 charts saved to reports/")
    print("=" * 60)
