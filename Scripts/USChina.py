import os
from pathlib import Path

import certifi

# Use certifi's certificate bundle so HTTPS works with python.org's macOS Python
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import pandas as pd
import matplotlib.pyplot as plt
import requests
from dotenv import load_dotenv
from fredapi import Fred

PROJECT_DIR = Path(__file__).resolve().parent.parent  # scripts live one folder below the project root
CHART_DIR = PROJECT_DIR / "charts"

# Load API keys from .env (kept out of the code so it's safe to share)
load_dotenv(PROJECT_DIR / ".env")
FRED_API_KEY = os.getenv("FRED_API_KEY")
NASS_API_KEY = os.getenv("NASS_API_KEY")

if not FRED_API_KEY or not NASS_API_KEY:
    raise RuntimeError("Missing API key(s): fill in FRED_API_KEY and NASS_API_KEY in .env")

fred = Fred(api_key=FRED_API_KEY)
NASS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"



STATES = ["KS", "MO"]
START_YEAR, END_YEAR = 2015, 2025  # 2026 is still a forecast as of Oct 2026

# Chart style: one color per state, used in every chart
STATE_COLORS = {"KS": "#2a78d6", "MO": "#eb6834"}
STATE_NAMES = {"KS": "Kansas", "MO": "Missouri"}
TEXT, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def get_nass(short_desc, reference_period):
    """Pull one annual NASS series for KS and MO. Returns a year x state table."""
    params = {
        "key": NASS_API_KEY,
        "source_desc": "SURVEY",
        "agg_level_desc": "STATE",
        "short_desc": short_desc,
        "reference_period_desc": reference_period,
        "year__GE": START_YEAR,
        "year__LE": END_YEAR,
        "format": "JSON",
    }
    frames = []
    for state in STATES:
        resp = requests.get(NASS_URL, params={**params, "state_alpha": state}, timeout=60)
        resp.raise_for_status()
        frames.append(pd.DataFrame(resp.json()["data"]))
    df = pd.concat(frames)
    # NASS returns numbers as text like "201,670,000"
    df["value"] = pd.to_numeric(df["Value"].str.replace(",", ""), errors="coerce")
    table = df.pivot(index="year", columns="state_alpha", values="value").sort_index()
    return table.loc[START_YEAR:END_YEAR]


def plot_by_state(ax, table, title, ylabel):
    """Draw one line per state, with the 2018-19 trade war shaded."""
    ax.axvspan(2017.5, 2019.5, color=GRID, alpha=0.6, lw=0)
    ax.text(2018.5, 1.0, "Trade war\n2018–19", transform=ax.get_xaxis_transform(),
            ha="center", va="top", fontsize=9, color=MUTED)
    for state in STATES:
        s = table[state]
        ax.plot(s.index, s.values, color=STATE_COLORS[state], lw=2,
                marker="o", ms=4, label=STATE_NAMES[state])
        ax.annotate(STATE_NAMES[state], (s.index[-1], s.values[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9, color=TEXT)
    ax.set_title(title, loc="left", fontsize=12, color=TEXT)
    ax.set_ylabel(ylabel, color=MUTED)
    ax.set_xticks(range(START_YEAR, END_YEAR + 1, 2))
    ax.set_xlim(START_YEAR - 0.5, END_YEAR + 1.5)  # room for end labels
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.tick_params(colors=MUTED, length=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)


# --- Soybeans: production and price, Kansas vs. Missouri ---
production = get_nass("SOYBEANS - PRODUCTION, MEASURED IN BU", "YEAR") / 1e6
price = get_nass("SOYBEANS - PRICE RECEIVED, MEASURED IN $ / BU", "MARKETING YEAR")

summary = pd.concat({"Production (mil. bu)": production, "Price ($/bu)": price}, axis=1)
summary.columns = [f"{STATE_NAMES[st]} {measure}" for measure, st in summary.columns]
print("\nSoybeans, Kansas and Missouri (USDA NASS)")
print(summary.round(2).to_string())

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.5))
plot_by_state(ax1, production, "Soybean production", "Million bushels")
plot_by_state(ax2, price, "Soybean price received", "Dollars per bushel")
ax1.legend(frameon=False, loc="lower left")
fig.text(0.01, 0.01, "Source: USDA NASS QuickStats. Prices are marketing-year averages.",
         fontsize=8, color=MUTED)
fig.tight_layout(rect=(0, 0.04, 1, 1))

CHART_DIR.mkdir(exist_ok=True)
fig.savefig(CHART_DIR / "soybeans_ks_mo.png", dpi=200)
plt.show()
