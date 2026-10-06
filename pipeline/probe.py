"""Temporary probe 4: bond yield sources. Prints status/sizes and a few lines only."""
import urllib.request
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"
def get(label, url, n=400, lines=0, headers=None):
    h = {"User-Agent": UA}; h.update(headers or {})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40) as r:
            body = r.read().decode("utf-8", "replace")
        print("OK  ", label, r.status, len(body))
        if lines: print("     HEAD:", " // ".join(body.splitlines()[:lines])[:n]); print("     TAIL:", " // ".join(body.splitlines()[-3:])[:n])
        else: print("    ", body[:n].replace("\n", " // "))
        return body
    except Exception as e:
        print("FAIL", label, e); return None
get("US Treasury daily curve 2026", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv", 500, 2)
for sid in ("DGS10", "DGS2", "IRLTLT01GBM156N", "IRLTLT01DEM156N", "IRLTLT01FRM156N", "IRLTLT01ITM156N", "IRLTLT01ESM156N", "IRLTLT01NLM156N"):
    get("FRED " + sid, "https://fred.stlouisfed.org/graph/fredgraph.csv?id=" + sid, 300, 2)
for sid in ("IUDMNPY", "IUDSNPY", "IUDMNZC"):
    get("BoE " + sid, "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Sep/2026&Dateto=now&SeriesCodes=%s&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N" % sid, 300, 3)
get("ECB AAA 10Y spot", "https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y?lastNObservations=5&format=csvdata", 400, 3)
get("ECB DE 10Y IRS monthly", "https://data-api.ecb.europa.eu/service/data/IRS/M.DE.L.L40.CI.0000.EUR.N.Z?lastNObservations=3&format=csvdata", 300, 3)
get("Bundesbank 10Y", "https://api.statistics.bundesbank.de/rest/data/BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A?lastNObservations=5&format=csv", 300, 3)
for c in ("germany", "united-kingdom", "france", "italy", "spain", "netherlands"):
    get("jina tradingeconomics " + c, "https://r.jina.ai/https://tradingeconomics.com/%s/government-bond-yield" % c, 500)
get("jina worldgovernmentbonds DE", "https://r.jina.ai/https://www.worldgovernmentbonds.com/country/germany/", 500)
get("yahoo ^TYX via jina", "https://r.jina.ai/https://query1.finance.yahoo.com/v8/finance/chart/%5ETYX?range=5d&interval=1d", 200)
get("yahoo BZ=F via jina", "https://r.jina.ai/https://query1.finance.yahoo.com/v8/finance/chart/BZ%3DF?range=5d&interval=1d", 200)
