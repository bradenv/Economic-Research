import os
from pathlib import Path

import certifi

# Use certifi's certificate bundle so HTTPS works with python.org's macOS Python
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred

PROJECT_DIR = Path(__file__).resolve().parent.parent  # scripts live one folder below the project root
DATA_DIR = PROJECT_DIR / "data"

load_dotenv(PROJECT_DIR / ".env")
FRED_API_KEY = os.getenv("FRED_API_KEY")
if not FRED_API_KEY:
    raise RuntimeError("Missing FRED_API_KEY in .env")

fred = Fred(api_key=FRED_API_KEY)

START_YEAR = 2012

# Currencies of KS/MO's main ag customers and competitors (Brazil for soybeans).
# All are foreign currency per US dollar, so a rise = stronger dollar.
SERIES = {
    "DEXCHUS": ("China", "Yuan per USD"),
    "DEXMXUS": ("Mexico", "Pesos per USD"),
    "DEXCAUS": ("Canada", "Canadian dollars per USD"),
    "DEXJPUS": ("Japan", "Yen per USD"),
    "DEXKOUS": ("South Korea", "Won per USD"),
    "DEXTAUS": ("Taiwan", "Taiwan dollars per USD"),
    "DEXBZUS": ("Brazil", "Reais per USD"),
    "DEXUSEU": ("Euro area", "Euros per USD"),  # FRED quotes USD per euro; inverted below
    "DTWEXBGS": ("Broad dollar index", "Index, Jan 2006 = 100"),
    "RTWEXBGS": ("Real broad dollar index", "Index, Jan 2006 = 100 (inflation-adjusted)"),
}
# Pandas period codes: year-end and quarter-end
FREQUENCIES = {"YE": "Annual", "QE": "Quarterly"}

frames = []
for series_id, (name, units) in SERIES.items():
    # Average the daily/monthly data ourselves: FRED's own averaging leaves the
    # current, unfinished year blank
    s = fred.get_series(series_id, observation_start=f"{START_YEAR - 1}-01-01").dropna()
    if series_id == "DEXUSEU":
        s = 1 / s
    # Monthly series are dated on the 1st, but the value covers the whole month
    last_covered = s.index.max()
    if s.index.to_series().diff().median() > pd.Timedelta(days=20):
        last_covered += pd.offsets.MonthEnd(0)
    for freq, freq_name in FREQUENCIES.items():
        avg = s.resample(freq).mean()
        df = avg.rename("value").rename_axis("date").reset_index()
        df["pct_change_yoy"] = df["value"].pct_change(1 if freq == "YE" else 4) * 100
        # Flag periods that aren't over yet, or that the data doesn't fully cover yet
        df["partial_period"] = df["date"] > last_covered
        df = df.assign(series_id=series_id, currency=name, units=units, frequency=freq_name)
        frames.append(df)

rates = pd.concat(frames, ignore_index=True)
rates["year"] = rates["date"].dt.year
rates["quarter"] = rates["date"].dt.quarter.where(rates["frequency"] == "Quarterly")
rates["date"] = rates["date"].dt.to_period("Q").dt.start_time.where(
    rates["frequency"] == "Quarterly", rates["date"].dt.to_period("Y").dt.start_time)
rates = rates[rates["year"] >= START_YEAR]

rates = rates[["currency", "series_id", "units", "frequency", "year", "quarter", "date",
               "value", "pct_change_yoy", "partial_period"]]
rates = rates.sort_values(["currency", "frequency", "date"]).reset_index(drop=True)

DATA_DIR.mkdir(exist_ok=True)
out_path = DATA_DIR / "fred_exchange_rates.csv"
rates.to_csv(out_path, index=False)
print(f"Saved {len(rates):,} rows to {out_path}")

annual = rates[rates["frequency"] == "Annual"]
print("\nAnnual average exchange rates (FRED; foreign currency per USD)")
print(annual.pivot(index="year", columns="currency", values="value").round(2).to_string())
