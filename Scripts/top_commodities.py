import os
from pathlib import Path

import certifi

# Use certifi's certificate bundle so HTTPS works with python.org's macOS Python
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent  # scripts live one folder below the project root

load_dotenv(PROJECT_DIR / ".env")
NASS_API_KEY = os.getenv("NASS_API_KEY")
if not NASS_API_KEY:
    raise RuntimeError("Missing NASS_API_KEY in .env")

NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"

STATES = ["KS", "MO"]
STATE_NAMES = {"KS": "Kansas", "MO": "Missouri"}
YEAR = 2025
TOP_N = 5

# Rows that are totals or subsets of another commodity, so they'd double count
EXCLUDE = [
    "TOTALS",          # e.g. FIELD CROP TOTALS
    "WHEAT, WINTER",   # subset of WHEAT
    "HAY & HAYLAGE",   # HAY is kept instead
    "HAY, ",           # alfalfa / excl alfalfa splits of HAY
]


def get_value_of_production(state):
    """Dollar value of production for every commodity NASS reports for a state."""
    params = {
        "key": NASS_API_KEY,
        "source_desc": "SURVEY",
        "agg_level_desc": "STATE",
        "statisticcat_desc": "PRODUCTION",
        "unit_desc": "$",
        "domain_desc": "TOTAL",
        "state_alpha": state,
        "year": YEAR,
        "format": "JSON",
    }
    resp = requests.get(NASS_URL, params=params, timeout=90)
    resp.raise_for_status()
    df = pd.DataFrame(resp.json()["data"])
    df = df[df["reference_period_desc"].isin(["YEAR", "MARKETING YEAR"])]
    # NASS returns numbers as text like "9,236,865,000"; "(D)" = withheld
    df["value"] = pd.to_numeric(df["Value"].str.replace(",", ""), errors="coerce")
    df["commodity"] = df["short_desc"].str.replace(" - PRODUCTION, MEASURED IN $", "", regex=False)
    for pattern in EXCLUDE:
        df = df[~df["commodity"].str.contains(pattern, regex=False)]
    return df[["commodity", "value"]].dropna().sort_values("value", ascending=False)


for state in STATES:
    top = get_value_of_production(state).head(TOP_N).reset_index(drop=True)
    top.index += 1
    top["value ($ bil)"] = (top.pop("value") / 1e9).round(2)
    print(f"\nTop {TOP_N} commodities by value of production, {STATE_NAMES[state]} {YEAR} (USDA NASS)")
    print(top.to_string())
