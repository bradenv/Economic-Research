import os
import zipfile
from pathlib import Path

import certifi

# Use certifi's certificate bundle so HTTPS works with python.org's macOS Python
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import pandas as pd
import requests

PROJECT_DIR = Path(__file__).resolve().parent.parent  # scripts live one folder below the project root
DATA_DIR = PROJECT_DIR / "data"
ERS_CACHE = DATA_DIR / "ers_raw"

# USDA ERS Farm Income and Wealth Statistics (no API key; one zip per release).
# Newer releases are listed at:
# https://www.ers.usda.gov/data-products/farm-income-and-wealth-statistics/data-files-us-and-state-level-farm-income-and-wealth-statistics
ERS_RELEASE_URL = "https://www.ers.usda.gov/media/29517/september-3-2026-release.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (economic-research-script)"}

STATES = {"KS": "Kansas", "MO": "Missouri", "US": "United States"}
START_YEAR = 2012

# ERS variable name -> our measure name
MEASURES = {
    # Income
    "Gross cash income": "Gross cash income",
    "Gross farm income": "Gross farm income",
    "Production expenses, all, excl. operator dwellings": "Production expenses",
    "Net cash income": "Net cash income",
    "Net farm income": "Net farm income",
    # Cash receipts (farm sales)
    "Cash receipts value, all commodities , all": "Cash receipts, all commodities",
    "Cash receipts value, livestock and products , all": "Cash receipts, livestock and products",
    "Cash receipts value, cattle and calves , all": "Cash receipts, cattle and calves",
    "Cash receipts value, hogs , all": "Cash receipts, hogs",
    "Cash receipts value, corn , all": "Cash receipts, corn",
    "Cash receipts value, soybeans , all": "Cash receipts, soybeans",
    "Cash receipts value, sorghum grain, all": "Cash receipts, sorghum",
    "Cash receipts value, wheat , all": "Cash receipts, wheat",
    # Government payments
    "Value of government payments, total": "Government payments, total",
    "Value of market facilitation program payments": "Market Facilitation Program (MFP)",
    "Value of ad hoc and emergency program payments, all": "Ad hoc and emergency payments (incl. MFP, pandemic)",
    "Value of USDA pandemic assistance to producers, all": "USDA pandemic assistance",
    "Value of price loss coverage payments, all": "Price Loss Coverage (PLC)",
    "Value of agricultural risk coverage payments, all": "Agriculture Risk Coverage (ARC)",
    "Value of conservation program payments, all": "Conservation payments",
    "Other farm income, insurance indemnities, federal": "Federal crop insurance indemnities",
}


def get_ers_release():
    """Download (once) and read the ERS release CSV."""
    zip_path = ERS_CACHE / Path(ERS_RELEASE_URL).name
    if not zip_path.exists():
        resp = requests.get(ERS_RELEASE_URL, headers=HEADERS, timeout=300)
        resp.raise_for_status()
        ERS_CACHE.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(resp.content)
    with zipfile.ZipFile(zip_path) as z:
        csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
        # ERS files aren't UTF-8 (non-breaking spaces in labels)
        return pd.read_csv(z.open(csv_name), dtype=str, encoding="latin-1")


raw = get_ers_release()
raw["VariableDescriptionTotal"] = raw["VariableDescriptionTotal"].str.strip()
raw["unit_desc"] = raw["unit_desc"].str.strip()
raw["year"] = raw["Year"].astype(int)

df = raw[raw["State"].isin(STATES) & raw["VariableDescriptionTotal"].isin(MEASURES)
         & (raw["unit_desc"] == "$1,000") & (raw["year"] >= START_YEAR)].copy()
df["state"] = df["State"].map(STATES)
df["measure"] = df["VariableDescriptionTotal"].map(MEASURES)
df["value_usd"] = pd.to_numeric(df["Amount"].str.replace(",", ""), errors="coerce") * 1000
# ERS state data stops at the last full year; the US table adds a forecast year
last_state_year = df.loc[df["state"] != "United States", "year"].max()
df["status"] = (df["year"] > last_state_year).map({True: "forecast", False: "estimate"})
df["release"] = df["PublicationDate"].str.strip()

missing = set(MEASURES.values()) - set(df["measure"])
if missing:
    print(f"Warning: not found in this ERS release: {sorted(missing)}")

farm = df[["state", "year", "measure", "value_usd", "status", "release"]]
farm = farm.sort_values(["state", "measure", "year"]).reset_index(drop=True)

DATA_DIR.mkdir(exist_ok=True)
out_path = DATA_DIR / "ers_farm_income_ks_mo_us.csv"
farm.to_csv(out_path, index=False)
print(f"Saved {len(farm):,} rows to {out_path} ({farm['release'].iloc[0]})")

# --- Quick look: did MFP offset the trade war? ---
wide = farm.pivot_table(index=["state", "year"], columns="measure", values="value_usd") / 1e6
cols = ["Net farm income", "Government payments, total", "Market Facilitation Program (MFP)"]
for state in ("Kansas", "Missouri"):
    view = wide.loc[state, cols].loc[2015:last_state_year].copy()
    view["Gov't payments % of net farm income"] = (
        view["Government payments, total"] / view["Net farm income"] * 100)
    print(f"\n{state}, $ millions (USDA ERS)")
    print(view.round(0).to_string())
