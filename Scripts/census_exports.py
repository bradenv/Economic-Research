import os
import json
import time
from pathlib import Path

import certifi

# Use certifi's certificate bundle so HTTPS works with python.org's macOS Python
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent  # scripts live one folder below the project root
DATA_DIR = PROJECT_DIR / "data"
CENSUS_CACHE = DATA_DIR / "census_raw"  # raw API responses, so reruns don't re-download

load_dotenv(PROJECT_DIR / ".env")
CENSUS_API_KEY = os.getenv("CENSUS_API_KEY")
if not CENSUS_API_KEY:
    raise RuntimeError("Missing CENSUS_API_KEY in .env")

# State exports by Harmonized System (HS) product code and destination country
CENSUS_URL = "https://api.census.gov/data/timeseries/intltrade/exports/statehs"

STATES = {"KS": "Kansas", "MO": "Missouri"}
START_YEAR, END_YEAR = 2012, 2026

# HS chapters 01-24 = agricultural and food products (incl. fish, chapter 03)
AG_CHAPTERS = [f"{c:02d}" for c in range(1, 25)]

# 4-digit products tied to the commodities in our NASS data and the trade-war tariffs
HS4_PRODUCTS = {
    "0201": "Beef, fresh or chilled",
    "0202": "Beef, frozen",
    "0203": "Pork",
    "0206": "Edible offal (beef, pork)",
    "1001": "Wheat",
    "1005": "Corn",
    "1007": "Grain sorghum",
    "1201": "Soybeans",
    "1507": "Soybean oil",
    "2303": "Distillers' grains and starch residues",
    "2304": "Soybean meal",
}


def census_get(state, level, period, codes=None, world_only=False):
    """Exports for one state and month at one HS level, optionally limited to some
    product codes or to the all-countries total. ALL_VAL_YR is the year-to-date
    value, so December = the full year. Returns the API's rows (header first)."""
    name = "world" if world_only else "ag" if codes else "all"
    path = CENSUS_CACHE / f"{state}_{level}_{name}_{period}.json"
    if path.exists():
        return json.loads(path.read_text())
    params = [
        ("get", "CTY_CODE,CTY_NAME,E_COMMODITY,E_COMMODITY_SDESC,ALL_VAL_YR"),
        ("STATE", state),
        ("COMM_LVL", level),
        ("time", period),
        ("key", CENSUS_API_KEY),
    ]
    # Repeating a predicate means "any of these", and keeps responses small
    params += [("E_COMMODITY", c) for c in codes or []]
    if world_only:
        params.append(("CTY_CODE", "-"))
    for attempt in range(3):
        resp = requests.get(CENSUS_URL, params=params, timeout=180)
        if resp.status_code == 204:  # month not published yet
            return None
        if resp.ok:
            break
        time.sleep(5 * (attempt + 1))
    resp.raise_for_status()
    if "json" not in resp.headers.get("content-type", ""):
        raise RuntimeError("Census returned a non-JSON page; check CENSUS_API_KEY")
    data = resp.json()
    CENSUS_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def latest_month(year):
    """Most recent month Census has published for a year."""
    for month in range(12, 0, -1):
        if census_get(next(iter(STATES)), "HS2", f"{year}-{month:02d}", world_only=True) is not None:
            return month
    return None


# Full years, plus the current year to date and the same months a year earlier
last_month = latest_month(END_YEAR)
periods = [(year, 12, "Annual") for year in range(START_YEAR, END_YEAR)]
if last_month:
    ytd = f"Jan-{pd.Timestamp(2000, last_month, 1):%b} YTD"
    periods += [(END_YEAR - 1, last_month, ytd), (END_YEAR, last_month, ytd)]

frames = []
for state, state_name in STATES.items():
    for year, month, period in periods:
        time_period = f"{year}-{month:02d}"
        requests_needed = [
            ("HS2", census_get(state, "HS2", time_period, codes=AG_CHAPTERS)),
            ("HS4", census_get(state, "HS4", time_period, codes=list(HS4_PRODUCTS))),
            ("HS2", census_get(state, "HS2", time_period, world_only=True)),  # all goods
        ]
        for level, data in requests_needed:
            df = pd.DataFrame(data[1:], columns=data[0])
            df = df.loc[:, ~df.columns.duplicated()]  # filtered columns are echoed back
            frames.append(df.assign(state=state_name, year=year, period=period, hs_level=level))
    print(f"Downloaded {state_name}")

raw = pd.concat(frames, ignore_index=True)
# Ag chapters' world totals come back in both the ag and all-goods requests
raw = raw.drop_duplicates(["state", "year", "period", "hs_level", "E_COMMODITY", "CTY_CODE"])
raw["exports_usd"] = pd.to_numeric(raw["ALL_VAL_YR"], errors="coerce")
# Keep individual countries (codes 1000-7999) and the world total ("-"); drop
# regional groupings like EUROPEAN UNION or ASEAN, which would double count
raw = raw[raw["CTY_CODE"].str.match(r"^[1-7]\d{3}$") | (raw["CTY_CODE"] == "-")]
raw.loc[raw["CTY_CODE"] == "-", "CTY_NAME"] = "WORLD TOTAL"
raw["CTY_NAME"] = raw["CTY_NAME"].str.title()

# --- Detailed file: ag chapters and selected 4-digit products, by country ---
is_ag = (raw["hs_level"] == "HS2") & raw["E_COMMODITY"].isin(AG_CHAPTERS)
is_product = (raw["hs_level"] == "HS4") & raw["E_COMMODITY"].isin(HS4_PRODUCTS)
exports = raw[is_ag | is_product].copy()
exports["product"] = exports["E_COMMODITY"].map(HS4_PRODUCTS).fillna(
    exports["E_COMMODITY_SDESC"].str.capitalize())
exports = exports.rename(columns={"E_COMMODITY": "hs_code", "CTY_CODE": "country_code",
                                  "CTY_NAME": "country"})
exports = exports[["state", "year", "period", "hs_level", "hs_code", "product",
                   "country_code", "country", "exports_usd"]]
exports = exports.sort_values(["state", "period", "year", "hs_level", "hs_code", "country"])

# --- Totals file: all goods vs ag, and how much of each state's ag went to China ---
world = raw[raw["CTY_CODE"] == "-"]
hs2_world = world[world["hs_level"] == "HS2"]
keys = ["state", "year", "period"]
totals = hs2_world.groupby(keys)["exports_usd"].sum().rename("total_goods_usd").to_frame()
totals["ag_exports_usd"] = (hs2_world[hs2_world["E_COMMODITY"].isin(AG_CHAPTERS)]
                            .groupby(keys)["exports_usd"].sum())
totals["ag_share_of_exports_pct"] = totals["ag_exports_usd"] / totals["total_goods_usd"] * 100
ag_china = exports[(exports["hs_level"] == "HS2") & (exports["country"] == "China")]
totals["ag_exports_to_china_usd"] = ag_china.groupby(keys)["exports_usd"].sum()
totals["ag_exports_to_china_usd"] = totals["ag_exports_to_china_usd"].fillna(0)
totals["china_share_of_ag_exports_pct"] = (totals["ag_exports_to_china_usd"]
                                           / totals["ag_exports_usd"] * 100)
totals = totals.reset_index()

DATA_DIR.mkdir(exist_ok=True)
outputs = {
    "census_ag_exports_by_country_ks_mo.csv": exports,
    "census_export_totals_ks_mo.csv": totals,
}
for name, df in outputs.items():
    df.to_csv(DATA_DIR / name, index=False)
    print(f"Saved {len(df):,} rows to {DATA_DIR / name}")

# --- Quick look ---
annual = totals[totals["period"] == "Annual"]
print("\nAg exports (HS 01-24), $ millions, and share going to China (Census, state of origin)")
view = annual.assign(ag_mil=annual["ag_exports_usd"] / 1e6)
print(view.pivot(index="year", columns="state",
                 values=["ag_mil", "china_share_of_ag_exports_pct"]).round(1).to_string())

ag_by_country = exports[(exports["hs_level"] == "HS2") & (exports["country"] != "World Total")
                        & (exports["period"] == "Annual")]
ag_by_country = ag_by_country.groupby(["state", "year", "country"])["exports_usd"].sum()
for state in STATES.values():
    print(f"\n{state}: top 5 ag export destinations, $ millions")
    for year in (2017, 2019, END_YEAR - 1):
        top = ag_by_country.loc[(state, year)].nlargest(5) / 1e6
        print(f"  {year}: " + ", ".join(f"{c} {v:,.0f}" for c, v in top.items()))
