import os
import zipfile
from pathlib import Path

import certifi

# Use certifi's certificate bundle so HTTPS works with python.org's macOS Python
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent  # scripts live one folder below the project root
DATA_DIR = PROJECT_DIR / "data"
QCEW_CACHE = DATA_DIR / "qcew_raw"  # raw QCEW downloads, so reruns don't re-download

load_dotenv(PROJECT_DIR / ".env")
BLS_API_KEY = os.getenv("BLS_API_KEY")  # optional: raises limits to 500 queries/day, 20 yrs/query

QCEW_URL = "https://data.bls.gov/cew/data/api/{year}/{qtr}/area/{area}.csv"
QCEW_ZIP_URL = "https://data.bls.gov/cew/data/files/{year}/csv/{year}_annual_by_area.zip"
BLS_URL = "https://api.bls.gov/publicAPI/{version}/timeseries/data/"
HEADERS = {"User-Agent": "economic-research-script"}

START_YEAR, END_YEAR = 2012, 2026
AREAS = {"US000": "United States", "20000": "Kansas", "29000": "Missouri"}

# Detailed industries worth tracking alongside the sectors (private ownership)
DETAIL_INDUSTRIES = {
    "111": "Crop production",
    "112": "Animal production and aquaculture",
    "311": "Food manufacturing",
    "3116": "Animal slaughtering and processing",
    "3364": "Aerospace product and parts manufacturing",
}
QCEW_OWNERSHIP = {0: "Total, all ownerships", 1: "Federal government", 2: "State government",
                  3: "Local government", 5: "Private"}

# CES supersectors (state level); data type 01 = all employees, thousands
CES_SUPERSECTORS = {
    "00000000": "Total nonfarm",
    "05000000": "Total private",
    "10000000": "Mining and logging",
    "20000000": "Construction",
    "30000000": "Manufacturing",
    "40000000": "Trade, transportation, and utilities",
    "50000000": "Information",
    "55000000": "Financial activities",
    "60000000": "Professional and business services",
    "65000000": "Education and health services",
    "70000000": "Leisure and hospitality",
    "80000000": "Other services",
    "90000000": "Government",
}
CES_STATES = {"20": "Kansas", "29": "Missouri"}
# Metro area CBSA codes (state code is the metro's primary state)
CES_METROS = {
    ("29", "28140"): "Kansas City, MO-KS",
    ("29", "41180"): "St. Louis, MO-IL",
    ("20", "48620"): "Wichita, KS",
    ("29", "44180"): "Springfield, MO",
    ("20", "45820"): "Topeka, KS",
    ("29", "17860"): "Columbia, MO",
    ("20", "29940"): "Lawrence, KS",
    ("20", "31740"): "Manhattan, KS",
    ("29", "27900"): "Joplin, MO",
    ("29", "27620"): "Jefferson City, MO",
    ("29", "41140"): "St. Joseph, MO-KS",
    ("29", "16020"): "Cape Girardeau, MO-IL",
}


# ---------- QCEW: employment by sector, growth, and location quotients ----------

def cache_annual_zip(year):
    """The open data URLs only go back to 2014. For earlier years, download BLS's bulk
    annual zip (~130 MB) once and keep just our areas' files from it."""
    zip_path = QCEW_CACHE / f"{year}_annual_by_area.zip"
    with requests.get(QCEW_ZIP_URL.format(year=year), headers=HEADERS, timeout=600, stream=True) as resp:
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        for area in AREAS:
            member = next(n for n in z.namelist() if f"annual {area} " in n)
            df = pd.read_csv(z.open(member), dtype={"area_fips": str, "industry_code": str})
            # Bulk files name this column differently from the open data API
            df = df.rename(columns={"annual_avg_estabs_count": "annual_avg_estabs",
                                    "oty_annual_avg_estabs_count_pct_chg": "oty_annual_avg_estabs_pct_chg"})
            df.to_csv(QCEW_CACHE / f"{year}_a_{area}.csv", index=False)
    zip_path.unlink()


def get_qcew(year, qtr, area):
    """One QCEW area file (all industries and ownerships). qtr is 1-4 or 'a' for annual."""
    path = QCEW_CACHE / f"{year}_{qtr}_{area}.csv"
    if not path.exists():
        QCEW_CACHE.mkdir(parents=True, exist_ok=True)
        resp = requests.get(QCEW_URL.format(year=year, qtr=qtr, area=area), headers=HEADERS, timeout=120)
        if resp.status_code == 404 and qtr == "a" and year < 2014:
            cache_annual_zip(year)
        else:
            resp.raise_for_status()
            path.write_text(resp.text)
    return pd.read_csv(path, dtype={"area_fips": str, "industry_code": str})


def keep_rows(df):
    """Totals by ownership, private NAICS sectors, and the detailed industries."""
    is_total = (df["industry_code"] == "10") & df["own_code"].isin(QCEW_OWNERSHIP)
    # NAICS sector rows: agglvl 14 (national) / 54 (statewide)
    is_sector = (df["own_code"] == 5) & df["agglvl_code"].isin([14, 54])
    is_detail = (df["own_code"] == 5) & df["industry_code"].isin(DETAIL_INDUSTRIES)
    return df[is_total | is_sector | is_detail].copy()


def latest_quarter(year):
    """Most recent quarter BLS has published for a year, or None."""
    for qtr in (4, 3, 2, 1):
        if requests.head(QCEW_URL.format(year=year, qtr=qtr, area="US000"),
                         headers=HEADERS, timeout=60).ok:
            return qtr
    return None


qcew_frames = []
for year in range(START_YEAR, END_YEAR + 1):
    for area, area_name in AREAS.items():
        try:
            df = keep_rows(get_qcew(year, "a", area))
        except requests.HTTPError:
            continue  # annual file not published yet (current year)
        qcew_frames.append(pd.DataFrame({
            "area": area_name, "year": year, "period": "Annual average",
            "own_code": df["own_code"], "industry_code": df["industry_code"],
            "employment": df["annual_avg_emplvl"],
            "establishments": df["annual_avg_estabs"],
            "avg_annual_pay": df["avg_annual_pay"],
            "employment_growth_pct": df["oty_annual_avg_emplvl_pct_chg"],
            "location_quotient": df["lq_annual_avg_emplvl"],
            "disclosure": df["disclosure_code"],
        }))

# Current year: no annual file yet, so use the last month of the latest published quarter
qtr = latest_quarter(END_YEAR)
if qtr:
    for area, area_name in AREAS.items():
        df = keep_rows(get_qcew(END_YEAR, qtr, area))
        qcew_frames.append(pd.DataFrame({
            "area": area_name, "year": END_YEAR, "period": f"Q{qtr} (month 3)",
            "own_code": df["own_code"], "industry_code": df["industry_code"],
            "employment": df["month3_emplvl"],
            "establishments": df["qtrly_estabs"],
            "avg_annual_pay": None,
            "employment_growth_pct": df["oty_month3_emplvl_pct_chg"],
            "location_quotient": df["lq_month3_emplvl"],
            "disclosure": df["disclosure_code"],
        }))

qcew = pd.concat(qcew_frames, ignore_index=True)
qcew["ownership"] = qcew["own_code"].map(QCEW_OWNERSHIP)
# Industry titles come from BLS's own lookup table
titles = pd.read_csv("https://data.bls.gov/cew/doc/titles/industry/industry_titles.csv",
                     dtype=str, storage_options=HEADERS)
titles["industry_title"] = titles["industry_title"].str.replace(r"^(NAICS\s*)?[\d-]+\s+", "", regex=True)
qcew = qcew.merge(titles, on="industry_code", how="left")
# BLS publishes 0 with disclosure code "N" when a value is suppressed for confidentiality
qcew.loc[qcew["disclosure"] == "N", ["employment", "establishments", "avg_annual_pay",
                                     "employment_growth_pct", "location_quotient"]] = None
qcew = qcew[["area", "year", "period", "ownership", "industry_code", "industry_title", "employment",
             "employment_growth_pct", "location_quotient", "establishments", "avg_annual_pay"]]
qcew = qcew.sort_values(["area", "ownership", "industry_code", "year"]).reset_index(drop=True)


# ---------- CES: nonfarm payroll employment, state and metro, monthly ----------

ces_series = {}
for st, st_name in CES_STATES.items():
    for code, sector in CES_SUPERSECTORS.items():
        ces_series[f"SMU{st}00000{code}01"] = (st_name, "State", sector)
for (st, cbsa), metro in CES_METROS.items():
    ces_series[f"SMU{st}{cbsa}0000000001"] = (metro, "Metro", "Total nonfarm")


def get_ces(series_ids, start, end):
    """Monthly CES data. Without a key: v1 API, 25 series and 10 years per request."""
    version, per_req, years = ("v2", 50, 20) if BLS_API_KEY else ("v1", 25, 10)
    rows = []
    for i in range(0, len(series_ids), per_req):
        for y0 in range(start, end + 1, years):
            body = {"seriesid": series_ids[i:i + per_req],
                    "startyear": str(y0), "endyear": str(min(y0 + years - 1, end))}
            if BLS_API_KEY:
                body["registrationkey"] = BLS_API_KEY
            resp = requests.post(BLS_URL.format(version=version), json=body, timeout=120)
            resp.raise_for_status()
            result = resp.json()
            if result["status"] != "REQUEST_SUCCEEDED":
                raise RuntimeError(f"BLS API: {result['message']}")
            for s in result["Results"]["series"]:
                for obs in s["data"]:
                    if obs["period"].startswith("M") and obs["period"] != "M13":
                        rows.append({"series_id": s["seriesID"], "year": int(obs["year"]),
                                     "month": int(obs["period"][1:]),
                                     "employment_thousands": float(obs["value"]),
                                     "preliminary": any(f.get("code") == "P" for f in obs["footnotes"])})
    return pd.DataFrame(rows)


# Start a year early so 2012 growth can be computed
ces = get_ces(list(ces_series), START_YEAR - 1, END_YEAR)
ces[["area", "level", "sector"]] = ces["series_id"].map(ces_series).apply(pd.Series)
ces["date"] = pd.to_datetime(dict(year=ces["year"], month=ces["month"], day=1))
ces = ces.sort_values(["level", "area", "sector", "date"]).reset_index(drop=True)
# Not seasonally adjusted, so compare each month to the same month a year earlier
ces["yoy_growth_pct"] = ces.groupby("series_id")["employment_thousands"].pct_change(12) * 100

# Annual averages (BLS's own method: mean of the 12 months). The current year is
# year-to-date, compared against the same months of the prior year.
last_year, last_month = ces["year"].max(), ces.loc[ces["year"] == ces["year"].max(), "month"].max()
annual = (ces[ces["year"] < last_year]
          .groupby(["level", "area", "sector", "year"])["employment_thousands"].mean().reset_index())
annual["growth_pct"] = annual.groupby(["level", "area", "sector"])["employment_thousands"].pct_change() * 100
annual["period"] = "Annual average"
ytd = (ces[ces["year"].isin([last_year - 1, last_year]) & (ces["month"] <= last_month)]
       .groupby(["level", "area", "sector", "year"])["employment_thousands"].mean().reset_index())
ytd["growth_pct"] = ytd.groupby(["level", "area", "sector"])["employment_thousands"].pct_change() * 100
ytd = ytd[ytd["year"] == last_year].assign(period=f"Jan-{pd.Timestamp(2000, last_month, 1):%b} YTD")
ces_annual = pd.concat([annual, ytd])
ces_annual = ces_annual[ces_annual["year"] >= START_YEAR].sort_values(["level", "area", "sector", "year"])


# ---------- Save and summarize ----------

DATA_DIR.mkdir(exist_ok=True)
outputs = {
    "qcew_employment_lq_us_ks_mo.csv": qcew,
    "ces_payroll_monthly_ks_mo.csv": ces[ces["year"] >= START_YEAR].drop(columns=["year", "month"]),
    "ces_payroll_annual_ks_mo.csv": ces_annual,
}
for name, df in outputs.items():
    df.to_csv(DATA_DIR / name, index=False)
    print(f"Saved {len(df):,} rows to {DATA_DIR / name}")

print("\nNonfarm payroll employment growth, % (CES, annual average)")
total = ces_annual[ces_annual["sector"] == "Total nonfarm"]
print(total.pivot(index="area", columns="year", values="growth_pct").round(1).to_string())

latest = qcew[qcew["period"] == "Annual average"]["year"].max()
print(f"\nLocation quotients, {latest} (QCEW, private employment; >1 = more concentrated than US)")
lq = qcew[(qcew["year"] == latest) & (qcew["period"] == "Annual average")
          & (qcew["ownership"] == "Private") & (qcew["area"] != "United States")]
print(lq.pivot(index="industry_title", columns="area", values="location_quotient")
      .sort_values("Kansas", ascending=False).to_string())
