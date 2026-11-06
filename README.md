# Kansas and Missouri Economic Research

Python scripts that pull public economic and agricultural data for Kansas and Missouri, 2012 to the latest 2026 releases. They support a case study on how the 2018–19 US–China trade war affected Kansas and Missouri agriculture.

## Scripts

| Script | Source | What it pulls |
| --- | --- | --- |
| `Scripts/top_commodities.py` | USDA NASS | Top 5 commodities by value of production |
| `Scripts/commodity_history.py` | USDA NASS | Production value and quantity for major commodities, incl. sorghum |
| `Scripts/USChina.py` | USDA NASS | Soybean production and price chart, trade war shaded |
| `Scripts/state_gdp.py` | BEA | Real GDP growth, GDP per capita, GDP by industry |
| `Scripts/bls_employment.py` | BLS QCEW, CES | Employment by sector, location quotients, state and metro payrolls |
| `Scripts/census_exports.py` | Census Bureau | Agricultural exports by product and destination country |
| `Scripts/farm_income.py` | USDA ERS | Farm income, cash receipts, government payments incl. MFP |
| `Scripts/exchange_rates.py` | FRED | Annual and quarterly exchange rates and dollar indexes |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your API keys
python Scripts/state_gdp.py
```

Outputs are saved to `data/` (CSV) and `charts/` (PNG).
