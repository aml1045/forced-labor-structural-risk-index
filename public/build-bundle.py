#!/usr/bin/env python3
"""Build a single self-contained bundle.html from the multi-page public-site.

Strategy (keeps the reskinned design + all wiring intact):
  - Inline assets/styles.css and assets/site.js.
  - Inline the SMALL data files (scores.json, divergence.json, subnational.json)
    as JSON blobs and shim fetch() so the bundle opens over file:// with no server.
  - The large admin1_risk.topojson (3.6 MB) stays fetched from the relative path
    when served, with a graceful no-data fallback when opened standalone — so the
    bundle never bloats and never fabricates a layer it cannot load.
  - Each page's <main> becomes a hash-routed <section data-route="...">; the shared
    header/footer and per-page inline <script> blocks are preserved verbatim, run
    on route activation. Cross-page links are rewritten to in-document hashes.
Run: python3 build-bundle.py  ->  writes bundle.html next to index.html
"""
import re, json, pathlib

ROOT = pathlib.Path(__file__).parent
CSS = (ROOT / "assets/styles.css").read_text()
JS = (ROOT / "assets/site.js").read_text()

PAGES = [
    ("index.html",            "index.html",        "Home"),
    ("pages/framework.html",  "framework.html",    "What it shows"),
    ("pages/explore.html",    "explore.html",      "Explore the map"),
    ("pages/intervention.html","intervention.html","Intervention"),
    ("pages/simulate.html",   "simulate.html",     "Simulation"),
    ("pages/rankings.html",   "rankings.html",     "Rankings"),
    ("pages/profiles.html",   "profiles.html",     "Country profiles"),
    ("pages/policymakers.html","policymakers.html","For policymakers"),
    ("pages/methodology.html","methodology.html",  "Methodology & limits"),
    ("pages/about.html",      "about.html",        "About"),
    ("pages/limitations.html","limitations.html",  "Limitations & sources"),
    ("pages/indicators.html", "indicators.html",   "Indicators & data sources"),
]

def slug(name):
    return name.replace(".html", "")

VALID = {slug(n) for _, n, _ in PAGES}

def extract_main(html):
    m = re.search(r"<main\b[^>]*>(.*?)</main>", html, re.S)
    return m.group(1) if m else ""

def extract_inline_scripts(html):
    """Inline <script> blocks (no src) at the end of body — the per-page wiring."""
    blocks = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S):
        body = m.group(1).strip()
        if body:
            blocks.append(body)
    return blocks

def rewrite_links(html):
    # ../index.html / index.html / pages/foo.html / foo.html  -> #foo  (in-doc routes)
    def repl(m):
        href = m.group(2)
        anchor = ""
        if "#" in href:
            href, anchor = href.split("#", 1)
            anchor = "#" + anchor
        f = href.split("/")[-1]
        s = slug(f)
        if s in VALID:
            # carry the deep-link hash as a query-ish suffix the router understands
            return f'{m.group(1)}="#{s}{("/" + anchor[1:]) if anchor else ""}"'
        return m.group(0)
    return re.sub(r'(href)="([^"]+\.html(?:#[^"]*)?)"', repl, html)

sections = []
scripts_by_route = {}
for path, name, label in PAGES:
    html = (ROOT / path).read_text()
    main = rewrite_links(extract_main(html))
    route = slug(name)
    sections.append(f'<section class="route" data-route="{route}" hidden>{main}</section>')
    scripts_by_route[route] = extract_inline_scripts(html)

scores = (ROOT / "data/scores.json").read_text()
divergence = (ROOT / "data/divergence.json").read_text()
subnational = (ROOT / "data/subnational.json").read_text()
domains = (ROOT / "data/domains.json").read_text()
# vintages.json is small (~32 KB) and feeds the indicators page's Years/Stale/Mode
# columns; tolerate absence (the page's script no-ops without it)
_vintages_path = ROOT / "data/vintages.json"
vintages_entry = (f'\n  "data/vintages.json": {_vintages_path.read_text()},'
                  if _vintages_path.exists() else "")

# Per-route init: wrap each page's inline scripts in a function keyed by route.
init_funcs = []
for route, blocks in scripts_by_route.items():
    body = "\n".join(blocks)
    init_funcs.append(f'  {json.dumps(route)}: function(){{\n{body}\n  }}')
INIT = "var ROUTE_INIT = {\n" + ",\n".join(init_funcs) + "\n};"

DATA_SHIM = f"""
// ---- inlined data (opens over file:// with no server) ----
var FLSRI_DATA = {{{vintages_entry}
  "data/scores.json": {scores},
  "data/divergence.json": {divergence},
  "data/subnational.json": {subnational},
  "data/domains.json": {domains}
}};
// Shim fetch: serve inlined small data; large topojson falls back gracefully.
(function(){{
  var realFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function(url, opts){{
    var key = String(url).replace(/^\\.\\.\\//,"").replace(/^\\.\\//,"");
    if (FLSRI_DATA[key] !== undefined) {{
      return Promise.resolve({{ ok:true, json:function(){{ return Promise.resolve(FLSRI_DATA[key]); }} }});
    }}
    if (/admin1_risk\\.topojson$/.test(key)) {{
      // try a real fetch when served; otherwise reject so the map shows its no-data slot
      if (realFetch) return realFetch(url, opts).catch(function(){{ return Promise.reject(new Error("subnational layer needs the data file served alongside the bundle")); }});
      return Promise.reject(new Error("subnational layer needs the data file served alongside the bundle"));
    }}
    return realFetch ? realFetch(url, opts) : Promise.reject(new Error("no fetch"));
  }};
}})();
"""

ROUTER = """
// ---- in-document hash router ----
(function(){
  var DEFAULT = "index";
  function parse(){
    var h = (location.hash||"").replace(/^#/,"");
    if(!h) return {route:DEFAULT, deep:""};
    var parts = h.split("/");
    var route = parts[0] || DEFAULT;
    var deep = parts.slice(1).join("/");
    return {route:route, deep:deep};
  }
  var inited = {};
  // Some ids (map-composite, country-pick, country-panel) appear on more than one
  // page. getElementById returns the first in document order, so inactive sections
  // must give up their canonical ids to the active one. We park inactive ids under a
  // data-oid attribute and restore them only on the active section.
  function dedupeIds(activeRoute){
    document.querySelectorAll("section.route").forEach(function(s){
      var active = (s.getAttribute("data-route") === activeRoute);
      s.querySelectorAll("[id],[data-oid]").forEach(function(el){
        if (active) {
          var oid = el.getAttribute("data-oid");
          if (oid) { el.id = oid; el.removeAttribute("data-oid"); }
        } else if (el.id) {
          el.setAttribute("data-oid", el.id);
          el.removeAttribute("id");
        }
      });
    });
  }
  function show(){
    var p = parse();
    // Unknown route (e.g. the bare-anchor hash a deep link leaves behind after
    // replaceState + the re-emitted hashchange): never blank the page.
    if (!document.querySelector('section.route[data-route="' + p.route.replace(/"/g,'') + '"]')) {
      var anyVisible = false;
      document.querySelectorAll("section.route").forEach(function(s){ if (!s.hidden) anyVisible = true; });
      if (anyVisible) return; // keep the current view; per-page handlers consume the hash
      p = { route: DEFAULT, deep: (location.hash||"").replace(/^#/,"") }; // cold load on unknown hash
    }
    dedupeIds(p.route);
    document.querySelectorAll("section.route").forEach(function(s){
      s.hidden = (s.getAttribute("data-route") !== p.route);
    });
    // reflect deep-link (#ISO3) so per-page hashchange handlers still fire
    if (p.deep) { try { history.replaceState(null,"","#"+p.deep); } catch(e){} }
    // mark active nav
    document.querySelectorAll("#site-header .navlink").forEach(function(a){
      var t = (a.getAttribute("href")||"").replace(/^#/,"").split("/")[0];
      if (t === p.route) a.setAttribute("aria-current","page"); else a.removeAttribute("aria-current");
    });
    if (!inited[p.route] && ROUTE_INIT[p.route]) {
      inited[p.route] = true;
      try { ROUTE_INIT[p.route](); } catch(e){ console.error("route init "+p.route, e); }
    }
    window.scrollTo(0,0);
    // if a deep-link was carried, re-emit hashchange so country pickers pick it up
    if (p.deep) { try { window.dispatchEvent(new HashChangeEvent("hashchange")); } catch(e){} }
  }
  // chrome injection (FLSRI.injectChrome) builds nav with pages/foo.html hrefs; rewrite to routes
  function fixChrome(){
    document.querySelectorAll("#site-header a, #site-footer a").forEach(function(a){
      var href = a.getAttribute("href")||"";
      var m = href.match(/([a-z-]+)\\.html(#.*)?$/);
      if (m) a.setAttribute("href", "#" + (m[1]==="index"?"index":m[1]) + (m[2]?("/"+m[2].slice(1)):""));
    });
  }
  document.addEventListener("DOMContentLoaded", function(){
    if (window.FLSRI && FLSRI.injectChrome) FLSRI.injectChrome();
    fixChrome();
    show();
  });
  window.addEventListener("hashchange", function(){ show(); });
  window.__flsriRouterShow = show;
})();
"""

bundle = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Forced Labor Structural Risk Index</title>
<meta name="description" content="The Forced Labor Structural Risk Index maps the structural conditions that enable forced labor across the world's countries — a single self-contained build.">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
{CSS}
</style>
</head>
<body>
<header id="site-header" class="site-header"></header>
<main>
{chr(10).join(sections)}
</main>
<footer id="site-footer" class="site-footer"></footer>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/topojson-client@3/dist/topojson-client.min.js"></script>
<script>
{DATA_SHIM}
</script>
<script>
{JS}
</script>
<script>
{INIT}
{ROUTER}
</script>
</body>
</html>
"""

out = ROOT / "bundle.html"
out.write_text(bundle)
print(f"wrote {out} ({len(bundle):,} bytes), {len(sections)} routed sections")
"""done"""
