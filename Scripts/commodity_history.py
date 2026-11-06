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
NASS_API_KEY = os.getenv("NASS_API_KEY")
if not NASS_API_KEY:
    raise RuntimeError("Missing NASS_API_KEY in .env")

NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"

STATES = ["KS", "MO"]
STATE_NAMES = {"KS": "Kansas", "MO": "Missouri"}
START_YEAR, END_YEAR = 2012, 2026

# Every commodity in either state's top 5 (see top_commodities.py), plus sorghum
# (a target of China's 2018 tariffs), with the NASS series name and the physical
# unit its quantity is reported in
COMMODITIES = {
    "Cattle":   ("CATTLE, INCL CALVES", "LB"),
    "Corn":     ("CORN, GRAIN", "BU"),
    "Soybeans": ("SOYBEANS", "BU"),
    "Wheat":    ("WHEAT", "BU"),
    "Sorghum":  ("SORGHUM, GRAIN", "BU"),
    "Milk":     ("MILK", "LB"),
    "Eggs":     ("EGGS", "EGGS"),
    "Hogs":     ("HOGS", "LB"),
}


def get_production(state, name, nass_name, unit):
    """Annual production of one commodity in one state: dollars and physical quantity."""
    frames = []
    for measure, unit_desc in [("value ($)", "$"), (f"quantity ({unit})", unit)]:
        params = {
            "key": NASS_API_KEY,
            "source_desc": "SURVEY",
            "agg_level_desc": "STATE",
            "domain_desc": "TOTAL",
            "short_desc": f"{nass_name} - PRODUCTION, MEASURED IN {unit_desc}",
            "state_alpha": state,
            "year__GE": START_YEAR,
            "year__LE": END_YEAR,
            "format": "JSON",
        }
        resp = requests.get(NASS_URL, params=params, timeout=90)
        if resp.status_code == 400:  # NASS returns 400 when nothing matches (e.g. Kansas eggs)
            continue
        resp.raise_for_status()
        df = pd.DataFrame(resp.json()["data"])
        # NASS returns numbers as text like "818,750,000"; "(D)" = withheld
        df["value"] = pd.to_numeric(df["Value"].str.replace(",", ""), errors="coerce")

        # Before the final annual report, NASS's "YEAR" row is a copy of the latest
        # forecast (e.g. 2026 corn in October). Flag those so they aren't read as final.
        forecasts = df[df["reference_period_desc"].str.contains("FORECAST")]
        annual = df[df["reference_period_desc"].isin(["YEAR", "MARKETING YEAR"])].copy()
        is_forecast = annual.set_index(["year", "load_time"]).index.isin(
            forecasts.set_index(["year", "load_time"]).index)
        annual["status"] = ["forecast" if f else "final" for f in is_forecast]

        annual["measure"] = measure
        frames.append(annual[["year", "measure", "value", "status"]])

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames)
    out.insert(0, "commodity", name)
    out.insert(0, "state", STATE_NAMES[state])
    return out


data = pd.concat(
    get_production(state, name, nass_name, unit)
    for state in STATES
    for name, (nass_name, unit) in COMMODITIES.items()
)
data = data.sort_values(["state", "commodity", "measure", "year"]).reset_index(drop=True)

DATA_DIR.mkdir(exist_ok=True)
out_path = DATA_DIR / "ks_mo_commodity_production_2012_2026.csv"
data.to_csv(out_path, index=False)
print(f"Saved {len(data)} rows to {out_path}")

# Quick look: value of production by year, $ billions
for state in STATE_NAMES.values():
    s = data[(data["state"] == state) & (data["measure"] == "value ($)")]
    table = s.pivot(index="year", columns="commodity", values="value") / 1e9
    print(f"\n{state}: value of production, $ billions (USDA NASS)")
    print(table.round(2).to_string())

forecasts = data[data["status"] == "forecast"]
if not forecasts.empty:
    print("\nForecast (not final) values:")
    print(forecasts.to_string(index=False))
