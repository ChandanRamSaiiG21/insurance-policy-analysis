# ============================================================
# 03_generate_data_v3_final.py
# Insurance Policy Lifecycle — Synthetic Data Generator
# FINAL VERSION — all fixes applied, clean run
#
# FIXES vs original v1:
#   1. risk_score  : driven by age, income, product, tenure
#                    (not random.uniform)
#   2. is_lapsed   : driven by logistic probability function
#                    using risk_score, income, premium load,
#                    product_line, tenure — calibrated to ~20%
#   3. clv_at_renewal: premiums_collected - claims_paid - acq_cost
#                    (not cumulative premium sum → removes leakage)
#   4. claim_amount: based on annual_premium × factor
#                    (not sum_insured → loss ratio 70–110%)
#   5. UUID fix    : customer_id/product_id/agent_id cast to str
#   6. SQL alias fix: fact_policies aliased as p in renewals query
#   7. Lapse probability recalibrated: base=0.02, tighter caps
#                    → target population lapse rate ~20–22%
# ============================================================

import random
from datetime import date, timedelta

import numpy as np
import pandas as pd
from faker import Faker
import os
from dotenv import load_dotenv
from pathlib import Path
from urllib.parse import quote_plus
from sqlalchemy import create_engine, text

fake = Faker("en_IN")
Faker.seed(42)
random.seed(42)
np.random.seed(42)

# ── Database connection (credentials loaded from .env) ───────
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
    result = conn.execute(text("SELECT current_database()"))
    print(f"Connected to: {result.scalar()}")

# ── Clear all tables ─────────────────────────────────────────
print("Clearing all tables...")
with engine.begin() as conn:
    conn.execute(text("""
        TRUNCATE TABLE
            fact_renewals, fact_claims, fact_premiums,
            fact_policies, dim_customers, dim_agents,
            dim_products, dim_date
        RESTART IDENTITY CASCADE
    """))
print("All tables cleared.")

# ============================================================
# SECTION 1 — dim_date
# ============================================================
def generate_dim_date(start_year=2015, end_year=2024):
    print("Generating dim_date...")
    rows = []
    current = date(start_year, 1, 1)
    end     = date(end_year, 12, 31)
    while current <= end:
        rows.append({
            "date_id"            : int(current.strftime("%Y%m%d")),
            "full_date"          : current,
            "year"               : current.year,
            "quarter"            : (current.month - 1) // 3 + 1,
            "month"              : current.month,
            "month_name"         : current.strftime("%B"),
            "week_of_year"       : current.isocalendar()[1],
            "day_of_week"        : current.weekday(),
            "is_weekday"         : current.weekday() < 5,
            "is_fiscal_year_end" : (current.month == 3 and current.day == 31)
        })
        current += timedelta(days=1)
    df = pd.DataFrame(rows)
    df.to_sql("dim_date", engine, if_exists="append", index=False)
    print(f"  dim_date done — {len(df):,} rows")

# ============================================================
# SECTION 2 — dim_customers, dim_agents, dim_products
# ============================================================
INDIAN_STATES    = [
    "Andhra Pradesh", "Telangana", "Karnataka", "Tamil Nadu", "Maharashtra",
    "Gujarat", "Rajasthan", "Uttar Pradesh", "Madhya Pradesh", "West Bengal",
    "Punjab", "Haryana", "Delhi", "Kerala", "Odisha"
]
INCOME_BRACKETS  = ["<2L", "2L-5L", "5L-10L", "10L-25L", "25L+"]
EDUCATION_LEVELS = ["High School", "Graduate", "Post Graduate", "Doctorate"]
OCCUPATIONS      = ["Salaried", "Self Employed", "Business Owner",
                    "Government Employee", "Retired", "Student"]
CHANNELS         = ["Direct", "Broker", "Digital", "Bancassurance"]
REGIONS          = ["North", "South", "East", "West", "Central"]

# Income midpoints in ₹ (used for premium affordability calc)
INCOME_MIDPOINT_RS = {
    "<2L": 100000, "2L-5L": 350000, "5L-10L": 750000,
    "10L-25L": 1750000, "25L+": 3500000
}

def generate_dim_customers(n=50000):
    print(f"Generating dim_customers ({n:,} rows)...")
    rows = []
    for _ in range(n):
        rows.append({
            "first_name"      : fake.first_name(),
            "last_name"       : fake.last_name(),
            "date_of_birth"   : fake.date_of_birth(minimum_age=18, maximum_age=70),
            "gender"          : random.choice(["Male", "Female", "Other"]),
            "education_level" : random.choice(EDUCATION_LEVELS),
            "marital_status"  : random.choice(["Single", "Married", "Divorced", "Widowed"]),
            "occupation"      : random.choice(OCCUPATIONS),
            "income_bracket"  : random.choices(INCOME_BRACKETS, weights=[20, 35, 25, 15, 5])[0],
            "city"            : fake.city(),
            "state"           : random.choice(INDIAN_STATES),
            "customer_since"  : fake.date_between(
                                    start_date=date(2015, 1, 1),
                                    end_date=date(2022, 12, 31)
                                ),
            "is_active"       : random.choices([True, False], weights=[85, 15])[0]
        })
    df = pd.DataFrame(rows)
    df.to_sql("dim_customers", engine, if_exists="append", index=False)
    print(f"  dim_customers done — {len(df):,} rows")

def generate_dim_agents(n=500):
    print(f"Generating dim_agents ({n} rows)...")
    rows = []
    for _ in range(n):
        rows.append({
            "agent_name"  : fake.name(),
            "agency_name" : fake.company(),
            "region"      : random.choice(REGIONS),
            "channel"     : random.choice(CHANNELS),
            "hire_date"   : fake.date_between(
                                start_date=date(2010, 1, 1),
                                end_date=date(2022, 12, 31)
                            ),
            "is_active"   : random.choices([True, False], weights=[80, 20])[0]
        })
    df = pd.DataFrame(rows)
    df.to_sql("dim_agents", engine, if_exists="append", index=False)
    print(f"  dim_agents done — {len(df):,} rows")

def generate_dim_products():
    print("Generating dim_products...")
    rows = [
        {"product_name": "Private Car Comprehensive",  "product_line": "Motor",  "coverage_type": "Comprehensive",  "vehicle_category": "Private Car",  "base_premium_rate": 2.50, "min_sum_insured": 100000,  "max_sum_insured": 5000000,  "is_active": True},
        {"product_name": "Private Car Third Party",    "product_line": "Motor",  "coverage_type": "Third-Party",    "vehicle_category": "Private Car",  "base_premium_rate": 0.75, "min_sum_insured": 100000,  "max_sum_insured": 1000000,  "is_active": True},
        {"product_name": "Two Wheeler Comprehensive",  "product_line": "Motor",  "coverage_type": "Comprehensive",  "vehicle_category": "Two-Wheeler",  "base_premium_rate": 1.80, "min_sum_insured": 20000,   "max_sum_insured": 200000,   "is_active": True},
        {"product_name": "Two Wheeler Third Party",    "product_line": "Motor",  "coverage_type": "Third-Party",    "vehicle_category": "Two-Wheeler",  "base_premium_rate": 0.50, "min_sum_insured": 20000,   "max_sum_insured": 100000,   "is_active": True},
        {"product_name": "Commercial Vehicle",         "product_line": "Motor",  "coverage_type": "Comprehensive",  "vehicle_category": "Commercial",   "base_premium_rate": 3.20, "min_sum_insured": 500000,  "max_sum_insured": 10000000, "is_active": True},
        {"product_name": "Individual Health Basic",    "product_line": "Health", "coverage_type": "Individual",     "vehicle_category": None,           "base_premium_rate": 1.20, "min_sum_insured": 200000,  "max_sum_insured": 500000,   "is_active": True},
        {"product_name": "Individual Health Premium",  "product_line": "Health", "coverage_type": "Individual",     "vehicle_category": None,           "base_premium_rate": 2.10, "min_sum_insured": 500000,  "max_sum_insured": 2000000,  "is_active": True},
        {"product_name": "Family Floater Basic",       "product_line": "Health", "coverage_type": "Family Floater", "vehicle_category": None,           "base_premium_rate": 1.80, "min_sum_insured": 300000,  "max_sum_insured": 1000000,  "is_active": True},
        {"product_name": "Family Floater Premium",     "product_line": "Health", "coverage_type": "Family Floater", "vehicle_category": None,           "base_premium_rate": 2.80, "min_sum_insured": 500000,  "max_sum_insured": 3000000,  "is_active": True},
        {"product_name": "Term Life Basic",            "product_line": "Life",   "coverage_type": "Term",           "vehicle_category": None,           "base_premium_rate": 0.90, "min_sum_insured": 1000000, "max_sum_insured": 10000000, "is_active": True},
        {"product_name": "Term Life Premium",          "product_line": "Life",   "coverage_type": "Term",           "vehicle_category": None,           "base_premium_rate": 1.40, "min_sum_insured": 2000000, "max_sum_insured": 50000000, "is_active": True},
        {"product_name": "Domestic Travel",            "product_line": "Travel", "coverage_type": "Domestic",       "vehicle_category": None,           "base_premium_rate": 0.30, "min_sum_insured": 50000,   "max_sum_insured": 500000,   "is_active": True},
        {"product_name": "International Travel",       "product_line": "Travel", "coverage_type": "International",  "vehicle_category": None,           "base_premium_rate": 0.60, "min_sum_insured": 500000,  "max_sum_insured": 5000000,  "is_active": True},
    ]
    df = pd.DataFrame(rows)
    df.to_sql("dim_products", engine, if_exists="append", index=False)
    print(f"  dim_products done — {len(df):,} rows")

# ============================================================
# HELPER FUNCTIONS — Risk Score & Lapse Probability
# ============================================================

# FIX 1: risk_score is a function of real actuarial drivers.
# Gives ML model genuine signal to learn from.
#
# Component breakdown (base = 50):
#   Age      : elderly (55+) and very young (18-24) are higher risk
#   Income   : lower income = higher risk (affordability & exposure)
#   Product  : Motor/Travel = higher risk; Life = lower risk
#   Tenure   : short tenure = less committed = higher risk
#   Noise    : ±8 Gaussian noise (prevents determinism)
# Final score clipped to [10, 90]

PRODUCT_RISK_DELTA = {"Motor": +8, "Travel": +6, "Health": 0, "Life": -6}
INCOME_RISK_DELTA  = {"<2L": +18, "2L-5L": +7, "5L-10L": 0, "10L-25L": -5, "25L+": -10}

def compute_risk_score(age, income_bracket, product_line, tenure_months):
    base = 50.0

    # Age component
    if   age < 25: base += 7
    elif age < 35: base += 1
    elif age < 45: base += 0
    elif age < 55: base += 4
    else:          base += 13

    base += INCOME_RISK_DELTA.get(income_bracket, 0)
    base += PRODUCT_RISK_DELTA.get(product_line, 0)

    if   tenure_months == 12: base += 4
    elif tenure_months == 36: base -= 4

    base += np.random.normal(0, 8)   # controlled noise
    return float(np.clip(round(base, 2), 10, 90))


# FIX 2: lapse probability is a logistic-style function of real drivers.
# Recalibrated (vs v2) with lower base and tighter caps
# → target ~20–22% population lapse rate.
#
# Drivers:
#   risk_score     : high risk → higher lapse
#   income_bracket : low income → higher lapse (can't afford)
#   premium_load   : annual_premium / income → affordability stress
#   product_line   : Travel lapses most; Life lapses least
#   tenure         : short tenure → less committed

PRODUCT_LAPSE_DELTA = {"Travel": +0.06, "Motor": +0.03, "Health": 0.0, "Life": -0.04}
INCOME_LAPSE_DELTA  = {"<2L": +0.09, "2L-5L": +0.04, "5L-10L": 0.0, "10L-25L": -0.03, "25L+": -0.06}

def compute_lapse_probability(risk_score, income_bracket, annual_premium,
                               product_line, tenure_months):
    p = 0.02  # lower base vs v2 (was 0.05) → brings overall rate down

    # Risk score: normalised to [0, 0.13] contribution
    p += (risk_score - 10) / 80 * 0.13

    p += INCOME_LAPSE_DELTA.get(income_bracket, 0)
    p += PRODUCT_LAPSE_DELTA.get(product_line, 0)

    # Premium affordability stress
    income_rs = INCOME_MIDPOINT_RS.get(income_bracket, 350000)
    load_ratio = annual_premium / income_rs
    p += min(load_ratio * 0.25, 0.07)   # capped at +7%

    if   tenure_months == 12: base_adj = +0.02
    elif tenure_months == 36: base_adj = -0.02
    else:                     base_adj = 0
    p += base_adj

    return float(np.clip(p, 0.01, 0.65))

# ============================================================
# SECTION 3 — fact_policies (500,000 rows)
# ============================================================
def generate_fact_policies(n=500000):
    print(f"Generating fact_policies ({n:,} rows)...")

    with engine.connect() as conn:
        # FIX 5: UUID columns cast to ::text to avoid numeric overflow
        customers = pd.read_sql(
            "SELECT customer_id::text, date_of_birth, income_bracket FROM dim_customers",
            conn
        )
        products = pd.read_sql(
            "SELECT product_id::text, product_line FROM dim_products",
            conn
        )
        # FIX 5: agent_ids as strings
        agent_ids = [str(r[0]) for r in conn.execute(
            text("SELECT agent_id FROM dim_agents")
        )]
        date_ids = [r[0] for r in conn.execute(
            text("SELECT date_id FROM dim_date ORDER BY date_id")
        )]

    today = date(2024, 12, 31)
    NON_LAPSE_STATUSES = ["Active", "Expired", "Renewed", "Cancelled"]
    NON_LAPSE_WEIGHTS  = [40, 20, 25, 15]
    LAPSE_CODES        = [1, 2, 3, 4, 5]

    rows = []
    for i in range(n):
        cust         = customers.sample(1).iloc[0]
        prod         = products.sample(1).iloc[0]
        income_br    = cust["income_bracket"]
        product_line = prod["product_line"]

        dob = cust["date_of_birth"]
        age = today.year - (dob.year if hasattr(dob, 'year') else
              pd.to_datetime(dob).year)

        tenure     = random.choices([12, 24, 36], weights=[60, 25, 15])[0]
        start_idx  = random.randint(0, len(date_ids) - 366)
        start_did  = date_ids[start_idx]
        end_did    = date_ids[min(start_idx + tenure * 30, len(date_ids) - 1)]

        sum_insured = round(random.uniform(100000, 2000000), -3)
        annual_prem = round(sum_insured * random.uniform(0.015, 0.035), 2)

        # FIX 1: risk_score driven by real variables
        risk_score  = compute_risk_score(age, income_br, product_line, tenure)

        # FIX 2: lapse driven by real variables, calibrated to ~20%
        lapse_prob  = compute_lapse_probability(
            risk_score, income_br, annual_prem, product_line, tenure
        )
        is_lapsed   = bool(np.random.random() < lapse_prob)

        if is_lapsed:
            status     = "Lapsed"
            is_renewed = False
        else:
            status     = random.choices(NON_LAPSE_STATUSES, weights=NON_LAPSE_WEIGHTS)[0]
            is_renewed = status == "Renewed"

        rows.append({
            "customer_id"          : cust["customer_id"],   # str UUID
            "product_id"           : prod["product_id"],    # str UUID
            "agent_id"             : random.choice(agent_ids),  # str UUID
            "policy_start_date_id" : start_did,
            "policy_end_date_id"   : end_did,
            "policy_number"        : f"POL{str(i + 1).zfill(8)}",
            "policy_status"        : status,
            "sum_insured"          : sum_insured,
            "annual_premium"       : annual_prem,
            "risk_score"           : risk_score,
            "policy_tenure_months" : tenure,
            "is_renewed"           : is_renewed,
            "is_lapsed"            : is_lapsed,
            "lapse_reason_code"    : random.choice(LAPSE_CODES) if is_lapsed else None,
        })

        if i % 100000 == 0 and i > 0:
            print(f"  {i:,} policies generated...")

    df = pd.DataFrame(rows)
    df.to_sql("fact_policies", engine, if_exists="append", index=False, chunksize=5000)

    lapse_rate = df["is_lapsed"].mean() * 100
    print(f"  fact_policies done — {len(df):,} rows | Lapse rate: {lapse_rate:.1f}%")

# ============================================================
# SECTION 4 — fact_premiums
# ============================================================
def generate_fact_premiums():
    print("Generating fact_premiums...")

    with engine.connect() as conn:
        policies = pd.read_sql(
            "SELECT policy_id, annual_premium, policy_start_date_id, policy_tenure_months FROM fact_policies",
            conn
        )
        date_ids = [r[0] for r in conn.execute(
            text("SELECT date_id FROM dim_date ORDER BY date_id")
        )]

    date_id_set       = set(date_ids)
    INSTALLMENT_TYPES = ["Annual", "Semi-Annual", "Quarterly", "Monthly"]
    INST_WEIGHTS      = [40, 20, 25, 15]
    PAYMENT_METHODS   = ["UPI", "NEFT", "Credit Card", "Cheque", "Cash"]
    STATUSES          = ["Paid", "Overdue", "Waived", "Partial"]
    STATUS_WEIGHTS    = [70, 15, 5, 10]

    rows = []
    for _, pol in policies.iterrows():
        inst_type        = random.choices(INSTALLMENT_TYPES, weights=INST_WEIGHTS)[0]
        intervals        = {"Annual": 12, "Semi-Annual": 6, "Quarterly": 3, "Monthly": 1}
        months_between   = intervals[inst_type]
        num_installments = max(1, pol["policy_tenure_months"] // months_between)
        amount_due       = round(pol["annual_premium"] / (12 / months_between), 2)

        start_idx = (date_ids.index(pol["policy_start_date_id"])
                     if pol["policy_start_date_id"] in date_id_set else 0)

        for inst in range(num_installments):
            due_idx    = min(start_idx + inst * months_between * 30, len(date_ids) - 1)
            due_did    = date_ids[due_idx]
            status     = random.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
            days_late  = random.randint(-5, 30) if status == "Paid" else random.randint(1, 90)
            paid_idx   = min(due_idx + days_late, len(date_ids) - 1)
            paid_did   = date_ids[paid_idx] if status != "Overdue" else None
            amount_paid = (amount_due if status == "Paid"
                           else round(amount_due * random.uniform(0.3, 0.9), 2)
                                if status == "Partial" else 0)

            rows.append({
                "policy_id"        : pol["policy_id"],
                "due_date_id"      : due_did,
                "paid_date_id"     : paid_did,
                "installment_type" : inst_type,
                "amount_due"       : amount_due,
                "amount_paid"      : amount_paid,
                "days_to_payment"  : days_late if status != "Overdue" else None,
                "payment_method"   : random.choice(PAYMENT_METHODS) if status != "Overdue" else None,
                "payment_status"   : status,
                "is_overdue"       : status == "Overdue"
            })

    df = pd.DataFrame(rows)
    df.to_sql("fact_premiums", engine, if_exists="append", index=False, chunksize=5000)
    print(f"  fact_premiums done — {len(df):,} rows")

# ============================================================
# SECTION 5 — fact_claims
# FIX 3: claim_amount = annual_premium × factor (not sum_insured)
# → portfolio loss ratio calibrated to 70–110% (IRDAI benchmark)
# ============================================================
def generate_fact_claims():
    print("Generating fact_claims...")

    with engine.connect() as conn:
        policies = pd.read_sql(
            """SELECT policy_id, sum_insured, annual_premium,
                      policy_start_date_id, policy_end_date_id
               FROM fact_policies
               WHERE policy_status != 'Cancelled'""",
            conn
        )
        date_ids = [r[0] for r in conn.execute(
            text("SELECT date_id FROM dim_date ORDER BY date_id")
        )]

    CLAIM_TYPES    = ["Own Damage", "Third Party", "Theft", "Natural Disaster", "Medical"]
    CLAIM_STATUSES = ["Settled", "Approved", "Rejected", "Under Review", "Filed"]
    STATUS_WEIGHTS = [50, 20, 15, 10, 5]
    REJECT_REASONS = [
        "Policy lapsed", "Pre-existing condition", "Outside coverage",
        "Fraudulent claim", "Documentation incomplete"
    ]

    rows = []
    for _, pol in policies.iterrows():
        if random.random() > 0.15:   # 15% claim frequency
            continue

        num_claims = random.choices([1, 2, 3], weights=[80, 15, 5])[0]
        start_pos  = next((i for i, d in enumerate(date_ids)
                           if d >= pol["policy_start_date_id"]), 0)
        end_pos    = next((i for i, d in enumerate(date_ids)
                           if d >= pol["policy_end_date_id"]), len(date_ids) - 1)

        for _ in range(num_claims):
            inc_pos    = random.randint(start_pos, max(start_pos, end_pos - 1))
            inc_did    = date_ids[inc_pos]
            filed_pos  = min(inc_pos + random.randint(1, 30), len(date_ids) - 1)
            filed_did  = date_ids[filed_pos]
            status     = random.choices(CLAIM_STATUSES, weights=STATUS_WEIGHTS)[0]

            # FIX 3: claim_amount relative to annual_premium → realistic loss ratio
            loss_factor = random.uniform(0.30, 0.90)
            claim_amt   = round(pol["annual_premium"] * loss_factor, 2)
            approved    = round(claim_amt * random.uniform(0.70, 1.0), 2) \
                          if status in ["Settled", "Approved"] else 0
            rejected    = round(claim_amt - approved, 2) if status == "Rejected" else 0
            settle_pos  = min(filed_pos + random.randint(7, 90), len(date_ids) - 1) \
                          if status in ["Settled", "Approved"] else None
            settle_did  = date_ids[settle_pos] if settle_pos else None
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

# ============================================================
# SECTION 6 — fact_renewals
# FIX 4: CLV = premiums_collected - claims_paid - acquisition_cost
# FIX 6: SQL alias p added to fact_policies query
# ============================================================
def generate_fact_renewals():
    print("Generating fact_renewals...")

    with engine.connect() as conn:
        # FIX 6: table aliased as p — was missing in v2
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

    print(f"  Policies eligible: {len(policies):,}")

    # Merge claims into policies
    policies = policies.merge(claims, on="policy_id", how="left")
    policies["total_claims_paid"] = policies["total_claims_paid"].fillna(0)

    NON_RENEWAL_REASONS = ["Price", "Competitor", "No Response", "Total Loss", "Moved abroad"]

    rows = []
    for _, pol in policies.iterrows():
        # Lapsed policies do not renew
        renewed    = False if pol["is_lapsed"] else \
                     random.choices([True, False], weights=[60, 40])[0]

        renew_pos  = next((i for i, d in enumerate(date_ids)
                           if d >= pol["policy_end_date_id"]), len(date_ids) - 1)
        renew_did  = date_ids[renew_pos]

        new_prem   = round(pol["annual_premium"] * random.uniform(0.95, 1.15), 2) \
                     if renewed else None
        change_pct = round((new_prem - pol["annual_premium"]) /
                           pol["annual_premium"] * 100, 2) if renewed else None

        # FIX 4: CLV = premiums_collected - claims_paid - acquisition_cost
        # acquisition_cost = 15% of annual premium (one-time agent commission)
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

# ============================================================
# RUN ALL
# ============================================================
print("\n" + "="*60)
print("STARTING FULL DATA GENERATION — Insurance Policy Lifecycle")
print("="*60)

generate_dim_date()
generate_dim_customers()
generate_dim_agents()
generate_dim_products()
generate_fact_policies()
generate_fact_premiums()
generate_fact_claims()
generate_fact_renewals()

# ── Final summary & validation ───────────────────────────────
print("\n" + "="*60)
print("ALL DONE — Row counts:")
print("="*60)
with engine.connect() as conn:
    for table in ["dim_date", "dim_customers", "dim_agents", "dim_products",
                  "fact_policies", "fact_premiums", "fact_claims", "fact_renewals"]:
        count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        print(f"  {table:<25} {count:>10,} rows")

print("\n" + "="*60)
print("VALIDATION CHECKS (paste these into chat):")
print("="*60)
with engine.connect() as conn:

    lapse_rate = conn.execute(text(
        "SELECT ROUND(AVG(is_lapsed::int) * 100, 1) FROM fact_policies"
    )).scalar()
    print(f"  Lapse rate:              {lapse_rate}%   (target: 18–24%)")

    loss_ratio = conn.execute(text("""
        SELECT ROUND(
            SUM(c.approved_amount) / NULLIF(SUM(pr.amount_paid), 0) * 100, 1
        )
        FROM fact_claims c
        JOIN fact_premiums pr ON c.policy_id = pr.policy_id
        WHERE pr.payment_status = 'Paid'
    """)).scalar()
    print(f"  Portfolio loss ratio:    {loss_ratio}%   (target: 70–110%)")

    avg_clv = conn.execute(text(
        "SELECT ROUND(AVG(clv_at_renewal), 0) FROM fact_renewals"
    )).scalar()
    print(f"  Avg CLV:                 ₹{avg_clv:,}")

    pos_clv = conn.execute(text("""
        SELECT ROUND(
            AVG(CASE WHEN clv_at_renewal > 0 THEN 1.0 ELSE 0.0 END) * 100, 1
        ) FROM fact_renewals
    """)).scalar()
    print(f"  Positive CLV %:          {pos_clv}%")

    risk_avg = conn.execute(text(
        "SELECT ROUND(AVG(risk_score), 1) FROM fact_policies"
    )).scalar()
    risk_std = conn.execute(text(
        "SELECT ROUND(STDDEV(risk_score), 1) FROM fact_policies"
    )).scalar()
    print(f"  Risk score avg ± std:    {risk_avg} ± {risk_std}   (expect spread, not flat 50.0)")

    lapse_by_income = conn.execute(text("""
        SELECT dc.income_bracket,
               ROUND(AVG(fp.is_lapsed::int) * 100, 1) AS lapse_pct
        FROM fact_policies fp
        JOIN dim_customers dc ON fp.customer_id = dc.customer_id
        GROUP BY dc.income_bracket
        ORDER BY lapse_pct DESC
    """)).fetchall()
    print(f"\n  Lapse rate by income bracket (should decrease with income):")
    for row in lapse_by_income:
        print(f"    {row[0]:<12} {row[1]}%")