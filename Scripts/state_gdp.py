import os
from pathlib import Path

import certifi

# Use certifi's certificate bundle so HTTPS works with python.org's macOS Python
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent  # scripts live one folder below the project root
DATA_DIR = PROJECT_DIR / "data"

load_dotenv(PROJECT_DIR / ".env")
BEA_API_KEY = os.getenv("BEA_API_KEY")
if not BEA_API_KEY:
    raise RuntimeError("Missing BEA_API_KEY in .env")

BEA_URL = "https://apps.bea.gov/api/data"

# BEA GeoFips codes: 00000 = United States
GEOS = {"00000": "United States", "20000": "Kansas", "29000": "Missouri"}
START_YEAR = 2011  # one year before 2012 so 2012 growth can be computed

# Top-level NAICS sectors in BEA table SAGDP2 (line code -> name). Together they
# add up to "All industry total" (line 1), so shares sum to 100%.
SECTORS = {
    3: "Agriculture, forestry, fishing and hunting",
    6: "Mining, quarrying, and oil and gas extraction",
    10: "Utilities",
    11: "Construction",
    12: "Manufacturing",
    34: "Wholesale trade",
    35: "Retail trade",
    36: "Transportation and warehousing",
    45: "Information",
    51: "Finance and insurance",
    56: "Real estate and rental and leasing",
    60: "Professional, scientific, and technical services",
    64: "Management of companies and enterprises",
    65: "Administrative and waste management services",
    69: "Educational services",
    70: "Health care and social assistance",
    76: "Arts, entertainment, and recreation",
    79: "Accommodation and food services",
    82: "Other services",
    83: "Government and government enterprises",
}


def get_bea(table, line_code):
    """One line of a BEA Regional table for the US, Kansas, and Missouri. Returns year x geo."""
    params = {
        "UserID": BEA_API_KEY,
        "method": "GetData",
        "datasetname": "Regional",
        "TableName": table,
        "LineCode": line_code,
        "GeoFips": ",".join(GEOS),
        "Year": "ALL",
        "ResultFormat": "JSON",
    }
    resp = requests.get(BEA_URL, params=params, timeout=90)
    resp.raise_for_status()
    results = resp.json()["BEAAPI"]["Results"]
    if "Error" in results:
        raise RuntimeError(f"BEA error for {table} line {line_code}: {results['Error']}")
    df = pd.DataFrame(results["Data"])
    # BEA returns numbers as text like "1,234.5"; "(D)" = suppressed
    df["value"] = pd.to_numeric(df["DataValue"].str.replace(",", ""), errors="coerce")
    df["year"] = df["TimePeriod"].astype(int)
    df["geo"] = df["GeoFips"].map(GEOS)
    table = df.pivot(index="year", columns="geo", values="value").sort_index()
    return table.loc[START_YEAR:]


# --- 1. Real GDP and growth (chained 2017 dollars, SAGDP9 line 1) ---
real_gdp = get_bea("SAGDP9", 1) * 1e6  # BEA reports millions
real_growth = real_gdp.pct_change() * 100

# --- 2. GDP by industry, current dollars (SAGDP2) ---
nominal_gdp = get_bea("SAGDP2", 1) * 1e6
by_industry = pd.concat(
    {name: get_bea("SAGDP2", line) * 1e6 for line, name in SECTORS.items()},
    names=["sector"],
).stack().rename("gdp_usd").reset_index()
by_industry["share_pct"] = by_industry.apply(
    lambda r: r["gdp_usd"] / nominal_gdp.loc[r["year"], r["geo"]] * 100, axis=1)

# Check that the sectors add up to the total (gaps would mean suppressed "(D)" values)
coverage = by_industry.groupby(["geo", "year"])["share_pct"].sum()
if (coverage - 100).abs().max() > 0.5:
    print("Warning: sector shares don't sum to 100% for some state-years:")
    print(coverage[(coverage - 100).abs() > 0.5])

# --- 3. GDP per capita (population = Census midyear estimates, via BEA SAINC1 line 2) ---
population = get_bea("SAINC1", 2)

summary = pd.concat({
    "real_gdp_usd": real_gdp,
    "real_gdp_growth_pct": real_growth,
    "nominal_gdp_usd": nominal_gdp,
    "population": population,
    "real_gdp_per_capita_usd": real_gdp / population,
    "nominal_gdp_per_capita_usd": nominal_gdp / population,
}, names=["measure"]).stack().rename("value").reset_index()
summary = summary.pivot_table(index=["geo", "year"], columns="measure", values="value").reset_index()
summary = summary[summary["year"] >= 2012]
by_industry = by_industry[by_industry["year"] >= 2012]

DATA_DIR.mkdir(exist_ok=True)
summary.to_csv(DATA_DIR / "gdp_summary_us_ks_mo.csv", index=False)
by_industry.to_csv(DATA_DIR / "gdp_by_industry_us_ks_mo.csv", index=False)
print(f"Saved {DATA_DIR / 'gdp_summary_us_ks_mo.csv'} and {DATA_DIR / 'gdp_by_industry_us_ks_mo.csv'}")

# --- Quick look ---
print("\nReal GDP growth, % (BEA, chained 2017 dollars)")
print(real_growth.loc[2012:].round(1).to_string())

print("\nReal GDP per capita, chained 2017 dollars")
print((real_gdp / population).loc[2012:].round(0).to_string())

latest = by_industry["year"].max()
shares = by_industry.pivot_table(index="sector", columns=["geo", "year"], values="share_pct")
print(f"\nIndustry share of GDP, %: 2012 vs {latest}")
cols = [(g, y) for g in GEOS.values() for y in (2012, latest)]
print(shares[cols].sort_values(("Kansas", latest), ascending=False).round(1).to_string())
