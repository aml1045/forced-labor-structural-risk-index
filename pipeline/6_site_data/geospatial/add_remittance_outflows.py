#!/usr/bin/env python3
"""
Add remittance OUTFLOWS (the demand / destination side) to the labor-export overlay.

The Labor-export pressure layer already shows remittance INFLOWS as %GDP (origin /
labor-exporting economies). This adds the mirror signal: remittances PAID OUT as a
share of GDP, which surfaces the migrant-DESTINATION economies (the Gulf states,
Luxembourg, etc.) — the demand side that the inflow signal does not capture.

Source: World Bank Open Data
  BM.TRF.PWKR.CD.DT  (personal remittances, paid, current US$)  /
  NY.GDP.MKTP.CD     (GDP, current US$)                          -> outflow % of GDP
Most-recent non-empty value per country (mrnev). World Bank aggregates (WLD, OED, ...)
are dropped by the join onto the overlay's 195 real-country corridor rows.

Outputs:
  data/processed/remittance_outflows.csv   (provenance: iso3, %GDP, US$, year, source)
  patches public/data/overlay.json corridor rows with `rout` (outflow % of GDP)

RUN ORDER: this is a POST-PROCESS. It MUST run AFTER build_overlay.py ->
to_public_json.build_overlay_json has (re)generated overlay.json and that file has been
published to public/data/. It patches public/data/overlay.json IN PLACE, adding `rout`
and backfilling destination centroids. It is idempotent (safe to re-run). If overlay.json
is regenerated without re-running this script, the published file loses `rout` (the map
still loads — destination dots just disappear); re-run this script to restore them.

Requires: geopandas (centroid backfill from data/geometry/ne_admin0.geojson) and network
access (live World Bank fetch). build_overlay.py / build_overlay_json have neither
dependency, which is why this enrichment is kept separate rather than folded into the
deterministic, offline staging build.
"""
import urllib.request, json, ssl, csv, os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CTX = ssl._create_unverified_context()  # WB open data over https; local trust store lacks the issuer


def fetch(ind):
    url = f"https://api.worldbank.org/v2/country/all/indicator/{ind}?format=json&per_page=400&mrnev=1"
    with urllib.request.urlopen(url, timeout=40, context=CTX) as r:
        d = json.load(r)
    out = {}
    for x in d[1]:
        c, v = x.get("countryiso3code"), x.get("value")
        if c and v is not None:
            out[c] = (float(v), x["date"])
    return out


def main():
    paid = fetch("BM.TRF.PWKR.CD.DT")
    gdp = fetch("NY.GDP.MKTP.CD")
    rows = {}
    for c, (p, yr) in paid.items():
        if c in gdp and gdp[c][0]:
            rows[c] = {"pct": 100 * p / gdp[c][0], "usd": p, "year": yr}
    print(f"outflow %GDP computed for {len(rows)} ISO3 units")

    csvp = os.path.join(REPO, "data", "processed", "remittance_outflows.csv")
    with open(csvp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["iso3", "outflow_pct_gdp", "paid_usd", "year", "source"])
        for c in sorted(rows):
            r = rows[c]
            w.writerow([c, round(r["pct"], 3), int(r["usd"]), r["year"],
                        "World Bank BM.TRF.PWKR.CD.DT / NY.GDP.MKTP.CD"])
    print("wrote", csvp)

    ovp = os.path.join(REPO, "public", "data", "overlay.json")
    ov = json.load(open(ovp))
    n = 0
    for c in ov["corridor"]:
        iso = c.get("iso3")
        c["rout"] = round(rows[iso]["pct"], 2) if iso in rows else None
        if iso in rows:
            n += 1

    # backfill any missing country centroids from the geometry so every dot can render
    # (the original overlay build dropped centroids for several states, incl. the Gulf)
    import geopandas as gpd
    g = gpd.read_file(os.path.join(REPO, "data", "geometry", "ne_admin0.geojson"))
    g["iso3"] = g["ADM0_A3"]
    g.loc[g["iso3"] == "-99", "iso3"] = g.loc[g["iso3"] == "-99", "ISO_A3_EH"]
    cents = {}
    for _, row in g.iterrows():
        try:
            p = row.geometry.representative_point()
            cents[row["iso3"]] = [round(p.y, 3), round(p.x, 3)]
        except Exception:
            pass
    # small island / micro-states the Natural Earth join still misses (notably Bahrain)
    FALLBACK_CENT = {
        "BHR": [26.07, 50.55], "NRU": [-0.52, 166.93], "GRD": [12.12, -61.68],
        "ATG": [17.06, -61.80], "AND": [42.55, 1.60], "KNA": [17.30, -62.74],
    }
    for iso, ll in FALLBACK_CENT.items():
        cents.setdefault(iso, ll)
    fc = sum(1 for c in ov["corridor"] if not c.get("cent") and c.get("iso3") in cents)
    for c in ov["corridor"]:
        if not c.get("cent") and c.get("iso3") in cents:
            c["cent"] = cents[c["iso3"]]
    print(f"backfilled centroids for {fc} corridor rows")

    # sanitize NaN/Inf -> null so the file is strict JSON (browsers reject NaN literals).
    # Upstream (build_overlay_json) now also emits strict JSON, so this is belt-and-suspenders
    # against any non-finite value introduced by the rout/centroid patch above.
    import math
    def clean(o):
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        if isinstance(o, list):
            return [clean(x) for x in o]
        if isinstance(o, dict):
            return {k: clean(v) for k, v in o.items()}
        return o
    ov = clean(ov)

    json.dump(ov, open(ovp, "w"), separators=(",", ":"))
    print(f"patched overlay.json: {n}/{len(ov['corridor'])} corridor rows got rout")

    dest = sorted([c for c in ov["corridor"] if c.get("rout") and c["rout"] >= 1.0],
                  key=lambda c: -c["rout"])
    print(f"destinations that will render (rout >= 1.0% GDP): {len(dest)}")
    for c in dest[:14]:
        print(f'  {c["iso3"]} {c["name"]}: {c["rout"]}% GDP')


if __name__ == "__main__":
    main()
