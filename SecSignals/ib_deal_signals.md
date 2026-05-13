# IB Deal Opportunity Signals — SEC EDGAR Intelligence Framework

> **Signal Type:** Deterministic / Quantitative  
> **Data Source:** SEC EDGAR via `edgartools` (`pip install edgartools`)  
> **Processing Mode:** Daily nightly batch  
> **Coverage:** All US public companies  

---

## Table of Contents

1. [Practitioner Framing](#1-practitioner-framing)
2. [M&A Target Screening](#2-ma-target-screening)
3. [Capital Markets Origination](#3-capital-markets-origination)
4. [Restructuring Advisory](#4-restructuring-advisory)
5. [Activism Defense Advisory](#5-activism-defense-advisory)
6. [Daily Processing Architecture](#6-daily-processing-architecture)
7. [Signal Scoring & Deal Routing](#7-signal-scoring--deal-routing)
8. [Activist Watchlist — CIK Seed List](#8-activist-watchlist--cik-seed-list)
9. [Data Gaps & Augmentation](#9-data-gaps--augmentation)

---

## 1. Practitioner Framing

Coverage bankers live on two things: being first to know something is happening, and showing up with a credible hook. Every signal below maps to one of those two needs. Deterministic and quantitative signals are the best kind because they are defensible in a Monday morning call — *"Three 13D filers crossed 5% in industrials names this week"* is a conversation opener. Qualitative signals (MD&A tone, risk factor changes) are secondary and covered separately.

The framework organizes signals into four deal categories. Each signal maps to a specific SEC form type, a specific edgartools API call, a processing tier, and a concrete banker action.

### Tier Definitions

| Tier | Timing | Action | Examples |
|------|--------|--------|----------|
| **Tier 1** | Same day | Immediate banker call / board alert | 13D activist filing, 8-K bankruptcy, CEO sudden exit |
| **Tier 2** | Next morning | Coverage team briefing note | Leverage ratio breach, Form D >$50M, restatement |
| **Tier 3** | Weekly digest | Pipeline update / screening output | Valuation discount screen, pack formation, capex surge |

### edgartools Core Imports

```python
from edgar import (
    Company,
    get_filings,
    get_current_filings,
    get_all_current_filings,
    iter_current_filings_pages,
)
```

> **Rule:** Always check edgartools first before writing custom SEC parsing code. Custom code is written only where edgartools has no dedicated data object (primarily SC 13D/G text extraction and DEF 14A proxy data).

---

## 2. M&A Target Screening

**Goal:** Identify companies statistically likely to be acquired or to acquire — before a deal is announced. Arrive at the CFO's office with a prepared pitch before any RFP is issued.

---

### Signal 2A — 13D / 13G: Activist Stake Accumulation `Tier 1`

When a Schedule 13D is filed, an entity has crossed 5% and declared intent to influence management. An activist campaign almost always ends in one of three outcomes: sale of the company, a spin-off/breakup, or a defensive M&A transaction. All three are IB mandates. Call the CFO the same day.

A **13G → 13D amendment** is an even stronger signal: a previously passive holder has decided to get active.

**Forms:** `SC 13D`, `SC 13D/A`, `SC 13G/A`

```python
from edgar import get_all_current_filings

# Run nightly at 6 PM ET
sc13d_today = get_all_current_filings(form="SC 13D")
for filing in sc13d_today:
    filer_cik   = filing.cik
    target_name = filing.company
    is_activist = filer_cik in ACTIVIST_WATCHLIST  # see Section 8
    # → Tier 1 alert if known activist; Tier 2 alert otherwise
```

---

### Signal 2B — 13F Pack Formation: Institutional Herding `Tier 3`

When 3+ top-tier funds all accumulate meaningful new positions in the same company within the same quarter, they signal institutional conviction that something is going to happen — either deep undervaluation awaiting a catalyst, or information advantage. A banker should be pitching that company a strategic review.

**Forms:** `13F-HR`

```python
from edgar import get_filings
from collections import defaultdict

new_positions = defaultdict(list)  # ticker → [fund names]

filings = get_filings(form="13F-HR", filing_date="2025-11-01:")
for filing in filings:
    t13f = filing.obj()  # ThirteenF object
    if not t13f.has_infotable():
        continue
    prev = t13f.previous_holding_report()
    if not prev:
        continue

    new_tickers = (
        set(t13f.infotable["Ticker"].dropna())
        - set(prev.infotable["Ticker"].dropna())
    )
    for ticker in new_tickers:
        new_positions[ticker].append(t13f.investment_manager.name)

# Flag: 3+ funds opening new positions in the same ticker this quarter
pack_candidates = {
    ticker: funds
    for ticker, funds in new_positions.items()
    if len(funds) >= 3
}
```

**`ThirteenF` key properties:**

| Property | Description |
|----------|-------------|
| `t13f.infotable` | DataFrame: Issuer, Ticker, Cusip, Value ($K), Shares, Type, PutCall |
| `t13f.investment_manager.name` | Fund name |
| `t13f.report_period` | Quarter end date (e.g. `"2024-09-30"`) |
| `t13f.total_value` | Total portfolio value |
| `t13f.previous_holding_report()` | Prior quarter `ThirteenF` object |

---

### Signal 2C — XBRL: EV/EBITDA Discount to Sector Peers `Tier 3`

Any company trading more than 1.5 standard deviations below its SIC-sector median EV/EBITDA is a logical acquisition target — PE buyout or strategic acquirer paying a control premium. Systematically computable from XBRL facts. Requires market cap from price data (e.g. `yfinance`).

**Forms:** `10-K`, `10-Q` (XBRL)  
**XBRL fields:** `OperatingIncomeLoss`, `DepreciationAndAmortization`, `LongTermDebt`, `Assets`

```python
from edgar import Company
import pandas as pd

def get_xbrl_metrics(ticker: str) -> dict:
    company  = Company(ticker)
    facts_df = company.get_facts().to_pandas()

    # Filter for relevant XBRL concepts
    ebit = facts_df[facts_df["concept"] == "us-gaap/OperatingIncomeLoss"]
    debt = facts_df[facts_df["concept"] == "us-gaap/LongTermDebt"]
    da   = facts_df[facts_df["concept"] == "us-gaap/DepreciationAndAmortization"]

    # Latest annual values → compute EBITDA, Net Debt → EV proxy
    # Cross-reference with market cap from yfinance for EV/EBITDA
    ...
    return {"ticker": ticker, "ev_ebitda": ..., "sic": company.sic}
```

---

### Signal 2D — 8-K Item 5.02: Sudden C-Suite Departure `Tier 1`

A same-day or immediately effective resignation of a CEO or CFO — especially with vague language ("pursuing other opportunities") and no named successor — is a near-certain signal of corporate distress or board conflict. The company either needs a strategic review banker immediately or an activist already has one coming.

**Flag criteria:** effective date = today + no successor named + C-suite role (CEO, CFO, President)

**Forms:** `8-K Item 5.02`

```python
eightk = filing.obj()  # EightK object

if eightk.has_item("5.02"):
    item_text = eightk["Item 5.02"]
    # Parse: effective date, role, reason language, successor named
    # → Tier 1 if: immediate effective date + no successor + vague reason
```

---

### Signal 2E — 8-K Item 2.01: Repeated Asset Dispositions `Tier 2`

A series of asset dispositions across multiple 8-K 2.01 filings within 12 months indicates active portfolio reshaping — often a precursor to a broader strategic process: breakup, carve-out, or preparation for sale.

**Flag criteria:** 2+ Item 2.01 filings from the same company in a rolling 12-month window.

**Forms:** `8-K Item 2.01`

---

## 3. Capital Markets Origination

**Goal:** Identify companies that need to raise capital — equity, debt, or convertible — before they issue an RFP. The best mandate conversations happen six months before the company knows it needs a banker.

---

### Signal 3A — XBRL: Leverage Creep Approaching Refi Wall `Tier 2`

Net Debt / EBITDA drifting upward over 4–6 consecutive quarters and approaching 4–5×, combined with a debt maturity within 18–36 months. This company will need to refinance — new bond, leveraged loan refi, or equity raise to delever. Entirely derivable from XBRL.

**Forms:** `10-K`, `10-Q` (XBRL)  
**XBRL fields:** `LongTermDebt`, `ShortTermBorrowings`, `OperatingIncomeLoss`, `DepreciationAndAmortization`

```python
def get_leverage_trend(ticker: str, n_quarters: int = 6) -> list[float]:
    """Return trailing n_quarters of Net Debt / EBITDA."""
    company  = Company(ticker)
    facts_df = company.get_facts().to_pandas()

    # Filter and pivot by period → compute ratio per quarter
    # Flag: ratio increased in 3+ consecutive quarters AND now > 4.0x
    ...
```

---

### Signal 3A-ii — XBRL: Net Leverage Change (Directional Trend) `Tier 2`

A companion to static leverage threshold screening. The **directional change** in Net Debt / EBITDA across 4–6 quarters is often more actionable than the ratio at any single point — a re-leveraging inflection after a multi-year deleveraging trend is a deal signal regardless of the absolute level.

**Why it matters for IB:** A company that spent 3 years paying down debt and has now reversed — ratio increasing for 2+ consecutive quarters — has either made an acquisition, drawn on its revolver for a reason, or is seeing EBITDA erosion. All three are conversations.

**Forms:** `10-K`, `10-Q` (XBRL)

#### XBRL fields and fallback hierarchy

Not all filers use the same debt taxonomy. Use a fallback hierarchy:

```python
# Debt concepts — try in order, take first non-null match
DEBT_CONCEPTS = [
    "us-gaap/LongTermDebt",
    "us-gaap/LongTermDebtAndCapitalLeaseObligations",
    "us-gaap/LongTermDebtNoncurrent",
    "us-gaap/DebtAndCapitalLeaseObligations",
    "us-gaap/NotesPayable",
]

# Cash
CASH_CONCEPT = "us-gaap/CashAndCashEquivalentsAtCarryingValue"

# EBITDA proxy: OperatingIncomeLoss + DepreciationAndAmortization
# Note: D&A is sometimes only in the cash flow statement in XBRL —
# company.get_facts() pulls across all statements so it is always findable,
# but period alignment (quarterly vs. annual) requires care.
EBIT_CONCEPT = "us-gaap/OperatingIncomeLoss"
DA_CONCEPT   = "us-gaap/DepreciationAndAmortization"
```

#### TTM EBITDA — the key aggregation step

XBRL reports quarterly snapshots. For a meaningful ratio you need **trailing twelve months** EBITDA, not a single quarter annualized. Sum the last 4 quarterly `OperatingIncomeLoss` values:

```python
from edgar import Company
import pandas as pd

def compute_net_leverage_trend(ticker: str, n_quarters: int = 6) -> dict:
    """
    Compute trailing Net Debt / EBITDA per quarter and detect trend direction.

    Returns dict with:
      - ratios:        list of (period, ratio) tuples, oldest first
      - current_ratio: latest Net Debt / EBITDA
      - qoq_delta:     latest quarter-over-quarter change
      - trend:         "re-leveraging" | "de-leveraging" | "stable"
      - inflection:    True if trend reversed in last 2 quarters
      - threshold_flags: dict of structural threshold crossings
    """
    company  = Company(ticker)
    facts_df = company.get_facts().to_pandas()

    # ── Pull quarterly debt, cash, EBIT, D&A ───────────────────────────
    def latest_by_period(concept: str) -> pd.Series:
        subset = facts_df[facts_df["concept"].str.endswith(concept.split("/")[-1])]
        subset = subset[subset["form"].isin(["10-Q", "10-K"])]
        return (subset.sort_values("filed")
                      .groupby("end")["val"]
                      .last())

    debt_series = None
    for concept in DEBT_CONCEPTS:
        s = latest_by_period(concept)
        if not s.empty:
            debt_series = s
            break

    cash_series  = latest_by_period(CASH_CONCEPT)
    ebit_series  = latest_by_period(EBIT_CONCEPT)
    da_series    = latest_by_period(DA_CONCEPT)

    if debt_series is None or ebit_series.empty:
        return {"ticker": ticker, "error": "insufficient XBRL data"}

    # ── Align periods and compute quarterly ratios ──────────────────────
    periods = sorted(set(debt_series.index) & set(ebit_series.index))[-n_quarters:]
    ratios  = []

    for i, period in enumerate(periods):
        # TTM EBITDA: sum of last 4 available quarterly EBIT + D&A values
        ttm_periods  = [p for p in ebit_series.index if p <= period][-4:]
        ttm_ebit     = ebit_series[ttm_periods].sum()
        ttm_da       = da_series[ttm_periods].sum() if not da_series.empty else 0
        ttm_ebitda   = ttm_ebit + ttm_da

        net_debt = (debt_series.get(period, 0)
                    - cash_series.get(period, 0))

        if ttm_ebitda > 0:
            ratios.append((period, round(net_debt / ttm_ebitda, 2)))

    if len(ratios) < 2:
        return {"ticker": ticker, "error": "insufficient quarters"}

    # ── Trend detection ─────────────────────────────────────────────────
    ratio_vals    = [r for _, r in ratios]
    deltas        = [ratio_vals[i] - ratio_vals[i-1] for i in range(1, len(ratio_vals))]
    re_leveraging = len(deltas) >= 2 and all(d > 0 for d in deltas[-2:])
    de_leveraging = len(deltas) >= 2 and all(d < 0 for d in deltas[-2:])

    # Inflection: was de-leveraging, now re-leveraging
    was_deleveraging = len(deltas) >= 4 and all(d < 0 for d in deltas[-4:-2])
    inflection       = was_deleveraging and re_leveraging

    current = ratio_vals[-1]

    # ── Structural threshold crossings ─────────────────────────────────
    prev    = ratio_vals[-2]
    thresholds = {
        "crossed_3x_up":   prev < 3.0 <= current,   # entering leveraged territory
        "crossed_4_5x_up": prev < 4.5 <= current,   # approaching covenant risk
        "crossed_5_5x_up": prev < 5.5 <= current,   # distressed zone
        "crossed_2x_down": prev > 2.0 >= current,   # underleveraged / recap candidate
    }

    return {
        "ticker":          ticker,
        "ratios":          ratios,
        "current_ratio":   current,
        "qoq_delta":       deltas[-1],
        "trend":           ("re-leveraging" if re_leveraging
                            else "de-leveraging" if de_leveraging
                            else "stable"),
        "inflection":      inflection,
        "threshold_flags": thresholds,
    }
```

#### IB deal routing by leverage signal

| Signal | Direction | Route To | Hook |
|--------|-----------|----------|------|
| Net Debt/EBITDA 2.5× → 3.5× over 4 quarters | Re-leveraging, approaching leveraged | DCM / Leveraged Finance | Proactive refi / covenant review |
| Net Debt/EBITDA > 4.5× + interest coverage < 2× | Distress zone | Restructuring | Liability management pitch |
| Net Debt/EBITDA 4.0× → 1.5× over 6 quarters | Aggressive deleveraging | M&A / ECM | M&A capacity / recap opportunity |
| Net Debt/EBITDA < 0.5× (net cash position) | Underleveraged | M&A / ECM | Leveraged recap or acquisition financing |
| Re-leveraging inflection after 3+ years declining | Trend reversal | DCM / M&A | Acquisition financing or distress early warning |

#### Implementation notes

- **ASC 842 lease accounting boundary (2019):** Operating leases moved onto balance sheet as `OperatingLeaseLiability`. Cross-2018/2019 leverage trend comparisons will show an apparent jump in debt that is accounting change, not real leverage increase. Flag this in signal output for retail, airlines, and real estate-heavy companies (SIC codes 5200–5999, 4500–4599, 6500–6599).
- **D&A location in XBRL:** D&A is sometimes reported only in the cash flow statement, not the income statement. `company.get_facts().to_pandas()` pulls across all statements so it is always findable — but verify the `form` and `period` columns align correctly when joining to EBIT.
- **Negative EBITDA companies:** Skip the ratio computation (division by zero / misleading result) and route directly to restructuring screening if EBITDA has been negative for 2+ consecutive quarters.

---

### Signal 3B — XBRL: FCF Acceleration + Underleveraged Balance Sheet `Tier 3`

Strong and accelerating free cash flow with almost no debt. These companies either become acquisition currency or get pitched a leveraged recapitalization. They represent a proactive ECM or M&A advisory conversation.

**Threshold:** FCF/Revenue > 15% + Net Debt/EBITDA < 1× + FCF growth > 20% YoY for two consecutive years.

**Forms:** `10-K`, `10-Q` (XBRL)  
**XBRL fields:** `OperatingCashFlow`, `CapitalExpenditures`, `Revenues`

---

### Signal 3C — Form D: Large Private Placement Closed `Tier 2`

A Form D filing above $50M means a private company just raised a significant equity round. That company is on a trajectory toward IPO or sale within 2–5 years. For ECM, this is a pipeline seeding call — initiate the relationship now, not when the S-1 is being drafted.

edgartools provides a `FormD` data object via `filing.obj()`.

**Forms:** `Form D`

```python
form_d_filings = get_all_current_filings(form="D")
for filing in form_d_filings:
    fd = filing.obj()  # FormD object
    # fd: total offering amount, security type (equity vs debt)
    # Flag: amount > $50M + equity offering type → ECM coverage initiation
```

---

### Signal 3D — XBRL: Capex Surge vs. Revenue Base `Tier 2`

A company whose capital expenditure jumps to >15% of revenue — a step-change from a historical 3–5% baseline — is building something large: data centers, manufacturing plants, infrastructure. That capex often cannot be funded from operating cash flow alone, creating a near-certain financing need within 12–24 months.

**Forms:** `10-K`, `10-Q` (XBRL)  
**XBRL fields:** `CapitalExpendituresIncurredButNotYetPaid` or `PaymentsToAcquirePropertyPlantAndEquipment`, `Revenues`

---

## 4. Restructuring Advisory

**Goal:** Find companies in financial distress before they file Chapter 11. The restructuring advisory mandate goes to whoever is already in the room. Early entry is everything.

---

### Signal 4A — 8-K Item 1.03: Bankruptcy / Receivership `Tier 1`

The most severe distress signal. At this point restructuring advisory is already in play, but there may still be time for an unengaged advisor to participate. Alert immediately and check for existing RX coverage.

**Forms:** `8-K Item 1.03`

---

### Signal 4B — 8-K Item 4.02: Financial Restatement `Tier 1`

Non-reliance on previously issued financial statements signals accounting problems. Downstream consequences — SEC inquiry, auditor change, possible fraud investigation — frequently push a company into a distressed sale or bankruptcy. Restatements commonly precede a strategic alternatives process by 6–18 months.

**Forms:** `8-K Item 4.02`

```python
# 8-K sweep — route by item code
eightk_filings = get_all_current_filings(form="8-K")
for filing in eightk_filings:
    eightk = filing.obj()  # EightK object

    if eightk.has_item("1.03"):
        route_to("restructuring", tier=1, filing=filing)

    if eightk.has_item("4.02"):
        route_to("restructuring", tier=1, filing=filing)

    if eightk.has_item("5.02"):
        route_to("ma_activism", tier=1, filing=filing)

    if eightk.has_item("2.06"):
        route_to("restructuring", tier=2, filing=filing)
```

**`EightK` key methods:**

| Method | Description |
|--------|-------------|
| `eightk.has_item("5.02")` | Boolean check for item code |
| `eightk["Item 5.02"]` | Raw text of that item |
| `eightk.items` | List of all item codes in this filing |
| `eightk.date_of_report` | Event date |
| `eightk.has_press_release` | Whether a press release is attached |

---

### Signal 4C — XBRL: Interest Coverage < 1.5× (Declining) `Tier 2`

EBIT / Interest Expense below 1.5× means the company is not comfortably covering its debt service from operations. Below 1.0× means it is technically not covering at all. The most direct financial distress indicator computable from XBRL. Compound with leverage ratio for a composite distress score.

**XBRL fields:** `OperatingIncomeLoss` ÷ `InterestExpense`  
**Flag:** ratio declined 3+ consecutive quarters AND now < 1.5×

---

### Signal 4D — XBRL: Current Ratio Deterioration `Tier 2`

Current Assets / Current Liabilities trending below 1.0× over 2–3 consecutive quarters combined with negative operating cash flow signals liquidity distress. The company may look solvent on paper (long-term assets exceed liabilities) but is running out of cash for near-term obligations — the classic "wall of maturities + shrinking liquidity runway" scenario.

**XBRL fields:** `AssetsCurrent` ÷ `LiabilitiesCurrent`, `NetCashProvidedByUsedInOperatingActivities`

---

### Signal 4E — 8-K Item 2.06: Material Impairment (Repeated) `Tier 2`

An asset write-down is management admitting they overpaid for something or that a business unit has deteriorated below book value. When impairments cluster — multiple 8-K Item 2.06 filings in consecutive quarters — it signals fundamental business deterioration requiring a balance sheet restructuring.

**Flag criteria:** 2+ Item 2.06 filings from the same company within 18 months.  
Quantify: impairment amount from 8-K text relative to total assets from XBRL to assess materiality.

---

## 5. Activism Defense Advisory

**Goal:** Get to a company before the activist arrives — or the moment the activist arrives — to pitch defensive advisory, a friendly strategic review, or a pre-emptive value creation plan. Being second means no mandate.

---

### Signal 5A — 13D by Known Activist: Primary Trigger `Tier 1`

When a known activist files a 13D, the target company needs a financial advisor to prepare a response. The advisor who shows up first with a strategic alternatives analysis wins the mandate. Cross-reference every SC 13D filer CIK against the activist watchlist (Section 8).

**Known activists to watch:** Elliott, Starboard, Icahn, Pershing Square, Third Point, Trian, ValueAct, JANA, Corvex, Sachem Head, Engine Capital, Engaged Capital.

**Forms:** `SC 13D`

---

### Signal 5B — 13F Ownership Churn: Weak Shareholder Base `Tier 3`

High churn in institutional ownership — many institutions exiting, being replaced by shorter-duration holders — signals weakening sponsorship. Activist funds specifically target companies with this profile because the shareholder base will not resist them.

**Measure:**
- Institutions present in Q-4 but gone by Q0 ÷ total Q0 holders = **churn rate**
- Herfindahl index of ownership concentration across 4 rolling quarters (declining index = dispersing base)

```python
# Compute ownership churn across 4 quarters for a given ticker
def compute_ownership_churn(ticker: str) -> dict:
    company = Company(ticker)
    quarterly_holders = []

    for filing in company.get_filings(form="13F-HR").head(4):
        t13f = filing.obj()
        if t13f.has_infotable():
            holders = set(t13f.infotable["Cusip"].dropna())
            quarterly_holders.append((t13f.report_period, holders))

    if len(quarterly_holders) < 2:
        return {}

    q0_holders  = quarterly_holders[0][1]
    q_4_holders = quarterly_holders[-1][1]
    exited      = q_4_holders - q0_holders
    churn_rate  = len(exited) / max(len(q0_holders), 1)

    return {"ticker": ticker, "churn_rate": churn_rate, "exited_count": len(exited)}
```

---

### Signal 5C — XBRL: TSR Gap vs. Sector Peers `Tier 3`

The empirical activist playbook: find a company with 3-year total shareholder return significantly below its sector median and an identifiable operational or capital allocation fix. Screening for this before the activist does allows a banker to walk in with: *"here is what the activists will argue, and here is what you should do about it."*

**Data needed:** XBRL for operational metrics + price data (`yfinance`) for TSR calculation.

---

### Signal 5D — DEF 14A: Director Tenure / Pay Misalignment `Tier 3`

Activists routinely attack boards where directors have served 15+ years (entrenchment) and where executive compensation is decoupled from TSR. A systematic scan of proxy filings for average director tenure and CEO pay-vs-TSR correlation gives you the vulnerability map before any public campaign.

> **Note:** edgartools has no dedicated proxy data object. Access via `filing.text()` and parse the compensation and director tenure tables. For enterprise use, ISS/Glass Lewis data feeds provide structured access.

**Forms:** `DEF 14A`

```python
company = Company("AAPL")
proxy   = company.get_filings(form="DEF 14A").latest()
text    = proxy.text()
# Parse: director names, years served, compensation tables
# Flag: avg director tenure > 12 years OR CEO pay growth >> TSR growth
```

---

## 6. Daily Processing Architecture

All signals run in a nightly batch split by urgency. Tier 1 event-driven signals run first and fast. XBRL financial screeners run later as they require iterating across the full coverage universe.

```
6:00 PM ET  →  8-K event sweep (Tier 1 signals)
7:00 PM ET  →  13D / 13G activist sweep (Tier 1)
8:00 PM ET  →  Form D sweep (Tier 2, ECM pipeline)
9:00 PM ET  →  XBRL financial screener refresh (Tier 2 / 3)
Quarterly   →  13F season: pack formation + ownership churn (Tier 3)
```

### 6:00 PM — 8-K Sweep

```python
from edgar import get_all_current_filings

ITEM_ROUTING = {
    "1.03": ("restructuring", 1),   # Bankruptcy
    "4.02": ("restructuring", 1),   # Restatement
    "5.02": ("ma_activism",   1),   # Executive departure
    "2.06": ("restructuring", 2),   # Material impairment
    "2.01": ("ma",            2),   # Asset disposition
    "1.01": ("dcm",           3),   # New material agreement
}

eightk_filings = get_all_current_filings(form="8-K")
for filing in eightk_filings:
    eightk = filing.obj()
    for item_code, (desk, tier) in ITEM_ROUTING.items():
        if eightk.has_item(item_code):
            emit_signal(
                company  = filing.company,
                cik      = filing.cik,
                signal   = f"8K_{item_code.replace('.', '')}",
                desk     = desk,
                tier     = tier,
                date     = filing.filing_date,
                url      = filing.filing_url,
            )
```

### 7:00 PM — 13D / 13G Activist Sweep

```python
sc13d = get_all_current_filings(form="SC 13D")
sc13g = get_all_current_filings(form="SC 13G")

for filing in list(sc13d) + list(sc13g):
    filer_cik = str(filing.cik).zfill(10)

    if filer_cik in ACTIVIST_WATCHLIST:
        tier = 1  # Known activist → same-day alert
    else:
        tier = 2  # Unknown filer → morning briefing

    emit_signal(
        company = filing.company,   # Target company
        cik     = filing.cik,
        signal  = "13D_ACTIVIST_STAKE",
        desk    = "ma_activism_defense",
        tier    = tier,
        filer   = ACTIVIST_WATCHLIST.get(filer_cik, {}).get("name", "Unknown"),
    )
```

### 8:00 PM — Form D Sweep

```python
form_d_filings = get_all_current_filings(form="D")
for filing in form_d_filings:
    fd = filing.obj()  # FormD object
    # Filter: total offering > $50M, equity type → ECM pipeline initiation
```

### 9:00 PM — XBRL Screener Refresh

```python
COVERAGE_UNIVERSE = ["AAPL", "MSFT", "...]  # 500–2,000 tickers

for ticker in COVERAGE_UNIVERSE:
    try:
        company  = Company(ticker)
        facts_df = company.get_facts().to_pandas()

        metrics = compute_financial_metrics(facts_df)
        # Metrics: leverage_ratio, interest_coverage, current_ratio,
        #          fcf_margin, fcf_growth_yoy, capex_pct_revenue

        upsert_to_silver(ticker, metrics)
    except Exception as e:
        log_error(ticker, e)
        continue

# After all tickers → refresh gold screener views in PostgreSQL
refresh_gold_views()
```

### Quarterly — 13F Season (Pack Formation + Churn)

```python
# Run ~45 days after each quarter end
QUARTER_END = "2025-09-30"
filings = get_filings(form="13F-HR", filing_date=f"{QUARTER_END}:")

new_positions  = defaultdict(list)
churn_by_stock = defaultdict(dict)

for filing in filings:
    t13f = filing.obj()
    if not t13f.has_infotable():
        continue

    prev = t13f.previous_holding_report()
    if not prev:
        continue

    curr_tickers = set(t13f.infotable["Ticker"].dropna())
    prev_tickers = set(prev.infotable["Ticker"].dropna())
    added        = curr_tickers - prev_tickers

    for ticker in added:
        new_positions[ticker].append(t13f.investment_manager.name)

pack_signals = {
    t: funds for t, funds in new_positions.items() if len(funds) >= 3
}
```

---

## 7. Signal Scoring & Deal Routing

Individual signals are interesting. A company hitting three independent signals in the same rolling 30-day window is a mandate conversation. The scoring system produces a composite score per company; any company reaching the threshold generates a banker brief.

### Signal Weights

| Signal | Points | Tier |
|--------|--------|------|
| 13D by known activist | +5 | Tier 1 auto-alert |
| 8-K bankruptcy / restatement (Item 1.03 / 4.02) | +5 | Tier 1 auto-alert |
| Sudden CEO / CFO departure (Item 5.02) | +4 | Tier 1 |
| Pack formation (3+ new 13F holders) | +3 | Tier 3 |
| EV/EBITDA > 1.5σ below sector median | +3 | Tier 3 |
| Interest coverage < 1.5× (declining) | +3 | Tier 2 |
| Net leverage re-leveraging inflection (trend reversal) | +3 | Tier 2 |
| Net leverage crossing structural threshold (3×, 4.5×, 5.5×) | +3 | Tier 2 |
| Leverage ratio trending up 3+ quarters | +2 | Tier 2 |
| Net leverage aggressive deleveraging (recap candidate) | +2 | Tier 3 |
| High institutional ownership churn | +2 | Tier 3 |
| Form D > $50M equity raise | +2 | Tier 2 |
| Capex / Revenue surged > 15% | +2 | Tier 2 |

> **Alert threshold:** Any company scoring ≥5 points in a rolling 30-day window generates a banker brief containing: company name, signals triggered, relevant deal type, key XBRL metrics, and the suggested outreach hook.

### Deal Routing Logic

| Primary Signal(s) | Route To | Suggested Hook |
|-------------------|----------|----------------|
| 13D by known activist | M&A / Activism Defense | Strategic alternatives / defense preparation |
| Pack formation + valuation discount | M&A Coverage | Takeout screening deck for sector |
| CEO exit + pack formation | M&A Senior Coverage | Strategic review pitch with comp set |
| Leverage creep + capex surge | DCM / Leveraged Finance | Proactive refi advisory or project finance |
| Net leverage re-leveraging inflection | DCM / M&A | Acquisition financing or covenant advisory |
| Net leverage crossing 4.5× / 5.5× threshold | Restructuring | Liability management / distressed advisory |
| Net leverage aggressive deleveraging | M&A / ECM | M&A capacity or leveraged recap pitch |
| Net leverage < 0.5× (net cash) | M&A / ECM | Acquisition financing or leveraged recap |
| FCF acceleration + underleveraged | ECM / M&A | Leveraged recapitalization or acquisition financing |
| Form D > $50M | ECM (IPO Pipeline) | Coverage initiation, IPO readiness discussion |
| Bankruptcy / restatement / interest coverage < 1× | Restructuring Advisory | Distressed advisory / liability management |
| Ownership churn + TSR gap | Activism Defense | Pre-emptive vulnerability assessment |

---

## 8. Activist Watchlist — CIK Seed List

### Background

No canonical open-source activist watchlist exists as a maintained, downloadable dataset. The closest publicly available resource is the `dokson/hedge-fund-tracker` GitHub repository with a community-curated CSV of fund CIKs — but it covers institutional funds broadly, not activists specifically, and has no maintenance guarantee.

The recommended approach: **bootstrap from EDGAR's own 13D filing history**, then maintain a manually verified seed list.

> **Multi-entity problem:** Elliott, Icahn, and others operate through many legal entities, each with a distinct CIK. Any watchlist must group aliases under a canonical fund name — verify via EDGAR company lookup, matching by registered address and signatory names.

### Seed List — Primary CIKs

```python
# activist_watchlist.py
# Verify all CIKs directly on EDGAR before use:
# https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={CIK}

ACTIVIST_WATCHLIST = {
    "0000921669": {
        "name":    "Elliott Management",
        "manager": "Paul Singer",
        "style":   "aggressive",
        "aliases": ["0001038082"],          # Elliott International L.P.
    },
    "0000813917": {
        "name":    "Icahn Capital",
        "manager": "Carl Icahn",
        "style":   "aggressive",
        "aliases": ["0000813762"],          # Icahn Enterprises L.P.
    },
    "0001267813": {
        "name":    "Starboard Value",
        "manager": "Jeff Smith",
        "style":   "operational",
        "aliases": [],
    },
    "0001336528": {
        "name":    "Pershing Square",
        "manager": "Bill Ackman",
        "style":   "loud",
        "aliases": [],
    },
    "0001409970": {
        "name":    "Third Point",
        "manager": "Dan Loeb",
        "style":   "loud",
        "aliases": [],
    },
    "0001418419": {
        "name":    "Trian Fund Management",
        "manager": "Nelson Peltz / Ed Garden",
        "style":   "constructive",
        "aliases": [],
    },
    "0001418814": {
        "name":    "ValueAct Capital",
        "manager": "Jeffrey Ubben (founded)",
        "style":   "constructive",
        "aliases": ["0001418812"],          # ValueAct Capital Management
    },
    "0001603466": {
        "name":    "JANA Partners",
        "manager": "Barry Rosenstein",
        "style":   "operational",
        "aliases": [],
    },
    "0001567606": {
        "name":    "Corvex Management",
        "manager": "Keith Meister",
        "style":   "constructive",
        "aliases": [],
    },
    "0001574290": {
        "name":    "Engaged Capital",
        "manager": "Glenn Welling",
        "style":   "operational",
        "aliases": [],
    },
    "0001353702": {
        "name":    "D.E. Shaw",
        "manager": "D.E. Shaw Group",
        "style":   "quant/activist hybrid",
        "aliases": [],
    },
    "0001510281": {
        "name":    "Sachem Head Capital",
        "manager": "Scott Ferguson",
        "style":   "constructive",
        "aliases": [],
    },
    "0001596930": {
        "name":    "Land & Buildings",
        "manager": "Jonathan Litt",
        "style":   "REIT-focused",
        "aliases": [],
    },
    "0001326110": {
        "name":    "Barington Capital",
        "manager": "James Mitarotonda",
        "style":   "operational",
        "aliases": [],
    },
    "0001748157": {
        "name":    "Engine Capital",
        "manager": "Arnaud Ajdler",
        "style":   "constructive",
        "aliases": [],
    },
}

# Flatten aliases into the lookup set for O(1) checks
ACTIVIST_CIKS = set(ACTIVIST_WATCHLIST.keys())
for entry in ACTIVIST_WATCHLIST.values():
    ACTIVIST_CIKS.update(entry["aliases"])
```

### Auto-Expansion from EDGAR History

```python
from edgar import get_filings

# Any entity with 3+ distinct 13D campaigns in 5 years is definitionally an activist
filings_5yr = get_filings(form="SC 13D", filing_date="2020-01-01:")
df          = filings_5yr.to_pandas()

filer_campaign_counts = (
    df.groupby("cik")["company"]
      .nunique()
      .sort_values(ascending=False)
)

# Candidates: 3+ distinct target companies → add to watchlist with pending_review=True
repeat_activists = filer_campaign_counts[filer_campaign_counts >= 3]
# → Review weekly; verify via EDGAR company lookup before promoting to ACTIVIST_WATCHLIST
```

---

## 9. Data Gaps & Augmentation

edgartools covers all SEC filing retrieval and parsing. Two areas require external augmentation for the quantitative signal framework to be fully self-contained.

| Gap | Why Needed | Recommended Source | Cost |
|-----|------------|-------------------|------|
| **Stock price / market cap** | Required for EV calculations (EV/EBITDA screener) and TSR comparisons. XBRL does not include market data. | `yfinance` for daily close prices | Free |
| **Activist fund CIK aliases** | Multi-entity filing structure means one activist may use 3–5 different CIKs. Static seed list requires periodic verification. | Bootstrap from EDGAR 13D history (Section 8) + quarterly manual review | Free |
| **SC 13D beneficial ownership %** | edgartools has no structured `Schedule13D` data object. The beneficial ownership percentage must be parsed from `filing.text()`. Regex works for ~80% of standard-format cover pages. | Regex on `filing.text()` for standard cases; EDGAR full-text search API for edge cases | Free (regex) |
| **DEF 14A structured proxy data** | Director tenure and compensation data for activism vulnerability scoring requires parsing proxy statement tables. edgartools provides text-only access via `filing.text()`. | Parse via `filing.text()` + structured extraction; or ISS/Glass Lewis for enterprise | Free (parsing); $$$$ (ISS/GL) |
| **Debt maturity schedule** | Required for the refi-wall signal. Maturity dates are in 10-K footnotes and 8-K Item 1.01 — not in structured XBRL fields. | Parse from `tenk.get_section_text("Long-Term Debt")` footnotes; or Bloomberg/CapIQ for enterprise | Free (parsing); $$$$ (Bloomberg) |

> **Summary:** For a fully free implementation, the only two external dependencies are `yfinance` for price data and a manually maintained activist CIK CSV. Every other signal in this framework is derivable entirely from edgartools + EDGAR. The DEF 14A and debt maturity signals require custom text parsing but no paid data sources.

---

## Appendix: edgartools Quick Reference

### Company & Filing Access

```python
from edgar import Company, get_filings, get_all_current_filings

# Company lookup — by ticker or CIK (CIK is faster)
company = Company("AAPL")
company = Company(320193)

# Key company properties
company.name             # "Apple Inc."
company.cik              # 320193
company.sic              # "3571"
company.industry         # "ELECTRONIC COMPUTERS"
company.fiscal_year_end  # "0930"

# Filing access with filters
filings = company.get_filings(
    form        = "13F-HR",
    filing_date = "2024-01-01:",
    amendments  = True,
)

# Latest N filings
latest_10k = company.latest("10-K")
latest_3q  = company.latest("10-Q", 3)

# Convenience properties
tenk = company.latest_tenk   # TenK object
tenq = company.latest_tenq   # TenQ object
```

### Data Objects by Form Type

| Form | `filing.obj()` returns | Key properties / methods |
|------|----------------------|--------------------------|
| `10-K` | `TenK` | `.risk_factors`, `.management_discussion`, `.balance_sheet`, `.income_statement`, `.cashflow_statement`, `["Item 7"]` |
| `10-Q` | `TenQ` | Same as TenK |
| `8-K` | `EightK` | `.items`, `.has_item("5.02")`, `["Item 5.02"]`, `.date_of_report`, `.has_press_release` |
| `4` | `Form4` | `.transactions`, `.reporting_owner`, `.get_buy_sell_counts()`, `.to_dataframe()` |
| `13F-HR` | `ThirteenF` | `.infotable` (DataFrame), `.total_value`, `.total_holdings`, `.previous_holding_report()`, `.has_infotable()` |
| `D` | `FormD` | Total offering amount, security type |
| `3`, `5` | `Form3`, `Form5` | `.get_ownership_summary()` |

### XBRL Facts Access

```python
# All historical XBRL facts as DataFrame
facts_df = company.get_facts().to_pandas()
# Columns: concept, val, accn, fy, fp, form, filed, frame

# Financial statements from latest filings
financials = company.get_financials()         # from latest 10-K
q_fin      = company.get_quarterly_financials()  # from latest 10-Q

financials.balance_sheet    # pandas DataFrame
financials.income           # pandas DataFrame
financials.cash_flow        # pandas DataFrame

# Direct XBRL from a specific filing
xbrl = filing.xbrl()
if xbrl:
    bs  = xbrl.statements.balance_sheet()
    inc = xbrl.statements.income_statement()
```

### Current Filings (Daily Batch)

```python
from edgar import (
    get_current_filings,          # single page, with pagination
    get_all_current_filings,      # all pages — use for nightly batch
    iter_current_filings_pages,   # memory-efficient page iterator
)

# Nightly batch — fetch all of a form type filed today
all_8k     = get_all_current_filings(form="8-K")
all_13d    = get_all_current_filings(form="SC 13D")
all_form_d = get_all_current_filings(form="D")

# Memory-efficient iteration for large form types (e.g. Form 4)
for page in iter_current_filings_pages(form="4"):
    for filing in page:
        process(filing)
```

### Filing Content

```python
filing.text()        # clean plain text
filing.html()        # original HTML
filing.markdown()    # markdown format
filing.xml()         # XML if available
filing.sections()    # list of section names
filing.search("material weakness")         # text search
filing.search(r"Item\s+\d+\.\d+", regex=True)

# URLs
filing.homepage_url  # EDGAR index page
filing.filing_url    # primary document URL
filing.base_dir      # base directory for all attachments

# Persistence
filing.save("./cache/filing.pkl")
f2 = Filing.load("./cache/filing.pkl")
```

---

## Appendix: Corporate Firewall & Proxy Configuration

edgartools has explicit, first-class support for corporate network environments via `configure_http()`. It uses `httpx` under the hood, which respects standard proxy conventions. All four common enterprise network scenarios are covered natively — no monkey-patching required.

### Scenario 1 — Corporate proxy only

```python
from edgar import configure_http

configure_http(proxy="http://proxy.bankname.com:8080")
```

For authenticated proxies (NTLM / basic auth):

```python
configure_http(proxy="http://domain\\username:password@proxy.bankname.com:8080")
```

edgartools also respects standard OS proxy environment variables automatically — no code change needed if these are already set system-wide:

```bash
export HTTPS_PROXY="http://username:password@proxy.bankname.com:8080"
export HTTP_PROXY="http://username:password@proxy.bankname.com:8080"
export NO_PROXY="localhost,127.0.0.1"
```

---

### Scenario 2 — SSL inspection / TLS interception

Very common in IB environments running Zscaler, Blue Coat, or Palo Alto proxies. These replace the SEC's certificate with an internal one, causing `SSL: CERTIFICATE_VERIFY_FAILED`. Two ways to handle:

```python
# Option A — disable SSL verification (quick, acceptable on trusted internal network)
configure_http(verify_ssl=False)

# Option B — point to the bank's internal CA bundle (preferred by infosec)
import os
os.environ["REQUESTS_CA_BUNDLE"] = r"C:\certs\bank-ca-bundle.crt"
os.environ["SSL_CERT_FILE"]      = r"C:\certs\bank-ca-bundle.crt"
# Then import and use edgar normally — no configure_http() call needed
```

Option B keeps SSL verification active while trusting the bank's inspection proxy. Get the internal CA certificate bundle from your IT/infosec team and reference it via the environment variables above.

---

### Scenario 3 — VPN + proxy + SSL inspection (full enterprise stack)

```python
from edgar import configure_http

configure_http(
    verify_ssl = False,                       # or use REQUESTS_CA_BUNDLE env var
    proxy      = "http://proxy.bankname.com:8080",
    timeout    = 60.0                         # increase — corporate proxies add latency
)
```

---

### Scenario 4 — Environment variable before import

`EDGAR_VERIFY_SSL` must be set **before** importing edgartools — the HTTP client initialises at import time. If you have already imported, use `configure_http()` instead.

```python
import os
os.environ["EDGAR_VERIFY_SSL"] = "false"      # must precede the import below
os.environ["HTTPS_PROXY"]      = "http://proxy.bankname.com:8080"

from edgar import Company                      # picks up both settings at init time
```

---

### Recommended pattern for the corporate project

A single startup module called at the top of every batch script keeps network config out of application code and lets prod/dev environments differ only by environment variables.

```python
# etl/common/network_config.py
import os
from edgar import configure_http, set_identity

def configure_edgar_for_corporate() -> None:
    """Apply corporate network settings for SEC EDGAR access.

    Reads all values from environment variables so prod/dev
    environments differ only in their .env file — no hardcoded
    proxy URLs or credentials in source code.
    """

    # ── Identity (required by SEC fair access policy) ──────────────────
    set_identity(
        name         = os.getenv("EDGAR_USER_NAME",  "IB Analytics Platform"),
        email        = os.getenv("EDGAR_USER_EMAIL", "analytics@bankname.com"),
        organization = os.getenv("EDGAR_ORG",        "Bank Name"),
    )

    # ── Proxy ───────────────────────────────────────────────────────────
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")

    # ── SSL verification ────────────────────────────────────────────────
    # Prefer CA bundle (keeps verification active) over disabling it.
    # If REQUESTS_CA_BUNDLE is set, pass that path as verify_ssl so
    # httpx uses the bank's cert bundle rather than the system store.
    ca_bundle = os.getenv("REQUESTS_CA_BUNDLE")
    if ca_bundle:
        verify_ssl = ca_bundle                          # path string → use bundle
    else:
        verify_ssl = os.getenv("EDGAR_VERIFY_SSL", "true").lower() != "false"

    # ── Apply ───────────────────────────────────────────────────────────
    configure_http(
        proxy      = proxy,
        verify_ssl = verify_ssl,
        timeout    = float(os.getenv("EDGAR_TIMEOUT", "60")),
    )
```

Call it once at the top of each batch entry point before any other edgar usage:

```python
# etl/run_nightly_batch.py
from etl.common.network_config import configure_edgar_for_corporate
configure_edgar_for_corporate()  # must be first

from edgar import get_all_current_filings, Company
# ... rest of batch logic
```

**`.env` file (Windows Server VM / dev machine):**

```ini
HTTPS_PROXY=http://proxy.bankname.com:8080
REQUESTS_CA_BUNDLE=C:\certs\bank-ca-bundle.crt
EDGAR_USER_EMAIL=analytics@bankname.com
EDGAR_USER_NAME=IB Analytics Platform
EDGAR_ORG=Bank Name
EDGAR_TIMEOUT=60
```

---

### Diagnostics — check current config and test connectivity

```python
from edgar import get_http_config, Company

# Inspect active settings
config = get_http_config()
print(f"SSL verification : {config['verify_ssl']}")
print(f"Proxy            : {config['proxy']}")
print(f"Timeout          : {config['timeout']}s")

# Quick connectivity test
try:
    company = Company("AAPL")
    print(f"Connected — {company.name}")
except Exception as e:
    print(f"Connection failed: {e}")
```

Enable verbose HTTP logging to trace exactly what the proxy is doing:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)

from edgar import Company
company = Company("AAPL")  # prints full request/response trace
```

---

### Important: `yfinance` proxy gap

`configure_http()` configures edgartools' own HTTP client (httpx). The `yfinance` library — used for price data in the EV/EBITDA and TSR signals — uses `requests` under the hood, not httpx. It reads `HTTPS_PROXY` from the environment directly rather than from `configure_http()`.

**Resolution:** Always set `HTTPS_PROXY` as an environment variable (not only call `configure_http()`). With the `network_config.py` module above this is handled automatically — the environment variable is set before either library is imported.

```python
# This is why network_config.py sets env vars AND calls configure_http()
# rather than calling configure_http() alone.

import os
os.environ["HTTPS_PROXY"] = proxy   # picked up by yfinance (requests)
configure_http(proxy=proxy)         # picked up by edgartools (httpx)
```

| Library | HTTP client | Proxy config method |
|---------|-------------|---------------------|
| edgartools | httpx | `configure_http(proxy=...)` |
| yfinance | requests | `HTTPS_PROXY` env variable |
| Both | — | Set `HTTPS_PROXY` env var — works for both |

---

*Data: SEC EDGAR (public) · Library: edgartools (open source) · For internal use only*
