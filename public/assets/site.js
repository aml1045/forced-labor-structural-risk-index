/* FLSRI public site — shared client logic (published build; see data/scores.json meta for version)
   Wires the composite map to data/scores.json (v0_4_spu_w05_composite),
   the subnational overlay to data/admin1_risk.topojson (risk surface),
   the rankings table, the divergence cards (data/divergence.json), and the
   R/E reweighting simulator. No data is fabricated here: every value shown is
   read from the data files; missing values render as no-data, never as zero. */

(function () {
  "use strict";

  // ---- shared constants ----
  const RAMP = ["#fff7ec","#fde0c5","#fdbf8f","#f99858","#ef7128","#d24f12","#a8320a","#781d07"];
  const NODATA = "#e2dccf";

  // Composite scores top out near 0.66 (Yemen), not 1.0. Ramp domain is fixed to the
  // theoretical 0..1 scale so the legend reads honestly: real countries never reach the dark end.
  function rampColor(v, vmax) {
    if (v === null || v === undefined) return NODATA;
    const t = Math.max(0, Math.min(1, v / vmax));
    const i = Math.min(RAMP.length - 1, Math.floor(t * RAMP.length));
    return RAMP[i];
  }
  // For relative reads (subnational risk surface, 0..~0.78) the values are stretched across the observed range.
  function rampStretch(v, lo, hi) {
    if (v === null || v === undefined || isNaN(v)) return NODATA;
    const t = Math.max(0, Math.min(1, (v - lo) / (hi - lo || 1)));
    const i = Math.min(RAMP.length - 1, Math.floor(t * RAMP.length));
    return RAMP[i];
  }

  const fmt = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "—" : Number(v).toFixed(d);

  // ---- antimeridian repair ----------------------------------------------------
  // Natural Earth / world-atlas geometry clips Russia, Fiji and a handful of Pacific
  // features at the dateline, but stores each as ONE ring whose vertices sit at both
  // -180 and +180. In any cylindrical projection that single ring spans the entire map
  // width, so Leaflet fills a horizontal band straight across the globe (the "streaks").
  // We repair the geometry up front: every ring is split at each antimeridian crossing
  // into separate east/west pieces, each closed along the ±180 edge, so nothing wraps.
  // Verified against world-atlas@2 countries-50m (Russia 2 rings, Fiji 1 ring) and the
  // admin1 risk surface. Pure data transform — colours/values are untouched.
  function splitRing(ring) {
    if (!ring || ring.length < 2) return [ring];
    const segs = [];
    let cur = [ring[0]];
    for (let i = 1; i < ring.length; i++) {
      const [x0, y0] = ring[i - 1];
      const [x1, y1] = ring[i];
      if (Math.abs(x1 - x0) > 180) {
        // antimeridian crossing — interpolate the latitude at the dateline
        const b0 = x0 > 0 ? 180 : -180;
        const b1 = -b0;
        const x1u = x1 + (x1 < x0 ? 360 : -360);
        const t = (x1u - x0) !== 0 ? (b0 - x0) / (x1u - x0) : 0;
        const ylat = y0 + (y1 - y0) * t;
        cur.push([b0, ylat]);
        segs.push(cur);
        cur = [[b1, ylat], [x1, y1]];
      } else {
        cur.push([x1, y1]);
      }
    }
    segs.push(cur);
    return segs.length === 1 ? [ring] : segs.map(closeRing);
  }
  function closeRing(r) {
    if (r.length < 3) return r;
    const a = r[0], b = r[r.length - 1];
    if (a[0] !== b[0] || a[1] !== b[1]) r.push([a[0], a[1]]);
    return r;
  }
  function ringCrosses(ring) {
    for (let i = 1; i < ring.length; i++) {
      if (Math.abs(ring[i][0] - ring[i - 1][0]) > 180) return true;
    }
    return false;
  }
  // Repair a single Polygon's coordinate array (array of rings) -> array of polygons.
  function splitPolygon(rings) {
    // Only act if the outer ring crosses; otherwise return as-is (one polygon).
    if (!rings.length || !ringCrosses(rings[0])) return [rings];
    // Split outer ring; treat each resulting piece as its own polygon.
    // Holes are rare in crossing features and dropped only if they themselves cross;
    // none do in the shipped geometry, so attach non-crossing holes to every piece.
    const outerPieces = splitRing(rings[0]);
    const holes = rings.slice(1).filter(h => !ringCrosses(h));
    return outerPieces.map(p => [p, ...holes]);
  }
  function splitAntimeridian(geojson) {
    geojson.features.forEach(f => {
      const g = f.geometry;
      if (!g) return;
      if (g.type === "Polygon") {
        const polys = splitPolygon(g.coordinates);
        if (polys.length > 1) { g.type = "MultiPolygon"; g.coordinates = polys; }
      } else if (g.type === "MultiPolygon") {
        const out = [];
        g.coordinates.forEach(poly => { splitPolygon(poly).forEach(p => out.push(p)); });
        g.coordinates = out;
      }
    });
    return geojson;
  }

  // ---- data cache ----
  let _scores = null;
  function loadScores() {
    if (_scores) return Promise.resolve(_scores);
    return fetch(rel("data/scores.json")).then(r => r.json()).then(j => {
      _scores = j;
      // tier cuts ship with the data so the JS and the build can never disagree
      if (j.meta && Array.isArray(j.meta.tier_cuts) && j.meta.tier_cuts.length === 2) {
        TIER_CUT[0] = j.meta.tier_cuts[0]; TIER_CUT[1] = j.meta.tier_cuts[1];
      }
      return j;
    });
  }
  // Per-country per-domain scores (data/domains.json). Loaded lazily by the country
  // profile only; resolves to {} on failure so the profile still renders without it.
  let _domains = null;
  function loadDomains() {
    if (_domains) return Promise.resolve(_domains);
    return fetch(rel("data/domains.json")).then(r => r.json())
      .then(j => { _domains = j; return j; })
      .catch(() => { _domains = {}; return _domains; });
  }
  // resolve data/ paths relative to whether the page is at root or in /pages/
  function rel(p) {
    const inPages = location.pathname.includes("/pages/");
    return (inPages ? "../" : "") + p;
  }

  // ---- header + footer injection (single source of truth) ----
  const NAV = [
    ["Home", "index.html"],
    ["Explore", null, [
      ["Map", "explore.html"],
      ["Rankings", "rankings.html"],
      ["Country profiles", "profiles.html"],
      ["Simulation", "simulate.html"],
      ["Intervention", "intervention.html"],
    ]],
    ["Methodology", null, [
      ["How the index works", "framework.html"],
      ["Methodology & limits", "methodology.html"],
      ["Sources & limitations", "limitations.html"],
      ["Indicators & data sources", "indicators.html"],
    ]],
    ["About", null, [
      ["About FLSRI", "about.html"],
      ["For policymakers", "policymakers.html"],
      ["GitHub \u2197", "https://github.com/aml1045/forced-labor-structural-risk-index"],
    ]],
  ];
  function pageHref(file) {
    const inPages = location.pathname.includes("/pages/");
    if (file === "index.html") return inPages ? "../index.html" : "index.html";
    return inPages ? file : "pages/" + file;
  }
  function injectChrome() {
    const here = location.pathname.split("/").pop() || "index.html";
    const renderItem = (item) => {
      const [label, file, children] = item;
      if (children) {
        const active = children.some(([, f]) => f === here);
        const sub = children.map(([l, f]) => {
          if (/^https?:/.test(f)) return `<a class="subitem" href="${f}" target="_blank" rel="noopener">${l}</a>`;
          const cur = (f === here) ? ' aria-current="page"' : "";
          return `<a class="subitem" href="${pageHref(f)}"${cur}>${l}</a>`;
        }).join("");
        return `<div class="navgroup${active ? " active" : ""}"><button type="button" class="navlink navtop" aria-haspopup="true" aria-expanded="false">${label} <span class="caret">&#9662;</span></button><div class="submenu">${sub}</div></div>`;
      }
      const cur = (file === here) ? ' aria-current="page"' : "";
      return `<a class="navlink" href="${pageHref(file)}"${cur}>${label}</a>`;
    };
    const links = NAV.map(renderItem).join("");
    const header = document.querySelector("#site-header");
    if (header) {
      header.innerHTML =
        `<a class="skip-link" href="#main">Skip to content</a>` +
        `<div class="nav"><a class="brand" href="${pageHref("index.html")}">FLSRI<small>Forced Labor Structural Risk Index</small></a>` +
        `<button type="button" class="nav-burger" aria-label="Menu" aria-expanded="false" aria-controls="nav-links"></button>` +
        `<div class="navlinks" id="nav-links">${links}</div></div>`;
      // give the main landmark a stable id so the skip link (and screen-reader users) can reach it
      const mainEl = document.querySelector("main");
      if (mainEl && !mainEl.id) mainEl.id = "main";
      // mobile disclosure menu: the burger toggles the whole link list; below the
      // nav breakpoint the submenus render as static accordions (see styles.css)
      const navEl = header.querySelector(".nav");
      const burger = header.querySelector(".nav-burger");
      if (burger && navEl) {
        burger.addEventListener("click", () => {
          const open = navEl.classList.toggle("nav-open");
          burger.setAttribute("aria-expanded", open ? "true" : "false");
        });
      }
      header.querySelectorAll(".navtop").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          const g = btn.closest(".navgroup");
          const open = g.classList.toggle("open");
          btn.setAttribute("aria-expanded", open ? "true" : "false");
        });
      });
      document.addEventListener("click", (e) => {
        if (!e.target.closest(".navgroup")) {
          header.querySelectorAll(".navgroup.open").forEach((g) => {
            g.classList.remove("open");
            const b = g.querySelector(".navtop");
            if (b) b.setAttribute("aria-expanded", "false");
          });
        }
      });
    }
    const footer = document.querySelector("#site-footer");
    if (footer) footer.innerHTML = `<div class="wrap-wide"><div class="footer-grid">
      <div><p class="footer-note">The Forced Labor Structural Risk Index measures the structural conditions associated with forced labor, not its prevalence. It is a tool for identifying where risk is concentrated and why: read alongside, not in place of, on-the-ground knowledge.</p>
      <p class="footer-note">Country geometry &copy; <a href="https://github.com/topojson/world-atlas">world-atlas</a> / Natural Earth (public domain). Sub-national risk surface derived from IPUMS-International census microdata and GDIS disaster locations. Composite scores read directly from the published build output (<code>data/scores.json</code>).</p></div>
      <div><p><strong>Forced Labor Structural Risk Index</strong></p>
      <p class="footer-note" id="footer-version">195 countries in scope, 184 scored. June 2026</p>
      </div>
    </div></div>`;
    setupGloss();
  }

  // ---- glossary popovers ----
  // Click any element carrying data-gloss="<key>" for a short definition, its theoretical
  // foundation, and the data source behind it. Wired to the per-domain circles and phase
  // headings (profiles) and to domain/concept terms on the framework page; reusable anywhere
  // by adding the attribute. Keys match the domain slugs in data/domains.json.
  const GLOSSARY = {
    "economic-precarity": { kind: "Recruitment domain", label: "Economic precarity",
      def: "Poverty, informality, and income insecurity that push people to accept dangerous or coercive work.",
      basis: "Material vulnerability is the supply-side condition for exploitative recruitment: the exposed, &ldquo;suitable target&rdquo; in a routine-activity reading of harm.",
      src: "World Bank WDI (poverty, inequality, labor productivity); ILOSTAT informal employment." },
    "debt-financialized-dependency": { kind: "Recruitment domain", label: "Debt &amp; financialized dependency",
      def: "Recruitment-fee and migration debt, and the financial dependence that turns a job into bondage.",
      basis: "Operationalizes debt bondage, a named form of forced labour under ILO Convention No.&nbsp;29.",
      src: "World Bank Global Findex (borrowing, informal credit, account exclusion)." },
    "constrained-mobility": { kind: "Recruitment domain", label: "Constrained mobility",
      def: "Restrictions (including sponsorship regimes) that tie a worker&rsquo;s legal status or movement to an employer.",
      basis: "Captures the kafala / sponsorship mechanism through which exit is legally foreclosed.",
      src: "Henley Passport Index; V-Dem freedom of movement; WB Women, Business &amp; the Law (mobility); UNHCR." },
    "ascriptive-exclusion": { kind: "Recruitment domain", label: "Ascriptive exclusion",
      def: "Exclusion by caste, ethnicity, or minority status that channels groups into exploitable work.",
      basis: "Grounds risk in ascriptive (birth-assigned) discrimination as a structural driver of forced labour.",
      src: "ETH-Zurich Ethnic Power Relations, excluded-population share." },
    "gender-structuring": { kind: "Recruitment domain", label: "Gendered labor",
      def: "Gendered segmentation of labor that concentrates risk in feminized and hidden sectors.",
      basis: "Reflects the feminization of precarious, unprotected, and under-counted work.",
      src: "UNDP Gender Inequality Index; World Bank gender LFP gap; ILOSTAT sex-by-sector employment." },
    "age-childhood-structuring": { kind: "Recruitment domain", label: "Age &amp; childhood structuring",
      def: "The presence of child labor and the structures that route minors into work.",
      basis: "Anchored in child labour: the forced-labour-proximate signal least entangled with governance, and the one used to prove incremental validity.",
      src: "World Bank WDI mirroring the ILO&ndash;UNICEF SDG series (child labour, child marriage, out-of-school)." },
    "legal-non-recognition": { kind: "Recruitment domain", label: "Legal non-recognition",
      def: "Statelessness and absent legal status that strip workers of protection and recourse.",
      basis: "Legal personhood is a precondition for labor protection; its absence removes any avenue of recourse.",
      src: "UNHCR statelessness; World Bank birth-registration completeness (SDG&nbsp;16.9.1)." },
    "structural-disruption": { kind: "Recruitment domain", label: "Structural disruption",
      def: "Conflict, displacement, and disaster shocks that abruptly enlarge the vulnerable population.",
      basis: "Acute crises push people into exploitation-exposed conditions faster than protections can adapt.",
      src: "UCDP conflict events; EM-DAT disasters; ND-GAIN climate vulnerability; UNHCR displacement." },
    "economic-structure-demand": { kind: "Exploitation domain", label: "Economic structure &amp; demand",
      def: "The sectoral demand for cheap, coercible labor, the strongest-sourced exploitation domain.",
      basis: "Demand-side reading: certain sectors (agriculture, informal work) structurally pull for unfree labor.",
      src: "World Bank WDI agrarian / sector employment; ILOSTAT informal employment; UNCTAD export concentration." },
    "foreclosed-exit-structural": { kind: "Exploitation domain", label: "Foreclosed exit (structural)",
      def: "Monopsony and exit-cost structures that make leaving a job impossible.",
      basis: "Labor-monopsony and freedom-of-association theory. Flagged low-confidence: its core generating signal is unsourceable at country scale and is carried by proxies.",
      src: "ILOSTAT collective-bargaining coverage, union density, and labour-inspection capacity (proxy stand-ins)." },
    "state-production-of-unfreedom": { kind: "Exploitation domain", label: "State production of unfreedom",
      def: "State-imposed and state-tolerated coercion.",
      basis: "Captures state-imposed forced labour, a distinct ILO category (e.g. compulsory or prison labour).",
      src: "V-Dem forced-labor indicator (v2xcl_slave); shared governance backbone." },
    "domain-a-transnational-concealment": { kind: "Monetization lens", label: "Transnational concealment",
      def: "Cross-border financial machinery that lets the proceeds of exploitation be hidden.",
      basis: "A Disruptor, not a driver: where proceeds are concealed marks a point of intervention, not where risk is worst, so it is held out of the headline score.",
      src: "Basel AML Index; Tax Justice Network Financial Secrecy Index; FATF mutual evaluations." },
    "domain-b-cash-informal-retention": { kind: "Monetization lens", label: "Cash &amp; informal retention",
      def: "Cash-reliant and informal economies in which proceeds are retained outside the formal system.",
      basis: "Disruptor lens on financial opacity; it scores high for wealthy economies too, so it is kept out of the risk composite.",
      src: "World Bank Informal Economy Database; Global Findex financial exclusion." },
    "phase-R": { kind: "Phase (averages into the score)", label: "Recruitment (R)",
      def: "Vulnerability to recruitment: who is exposed, and why. The equal-weight average of the eight Recruitment domains.",
      basis: "One half of the structural claim (an exposed, suitable population) combined with E by geometric mean.",
      src: "Computed from the Recruitment domains." },
    "phase-E": { kind: "Phase (averages into the score)", label: "Exploitation (E)",
      def: "Conditions for unchecked exploitation: whether exploiting that population can run without consequence. The average of the three Exploitation domains.",
      basis: "The other half of the claim (the absence of a capable guardian) combined with R by geometric mean.",
      src: "Computed from the Exploitation domains." },
    "monetization-lens": { kind: "Disruptor lens", label: "Monetization",
      def: "How the proceeds of exploitation are concealed and retained: financial opacity and cash dependence.",
      basis: "Mapped as a point of intervention (where the cycle could be broken), not a driver of risk, so it is deliberately excluded from the published composite.",
      src: "Basel AML Index; Tax Justice Network FSI; World Bank Informal Economy Database; Global Findex." },
    "composite": { kind: "How the score is built", label: "Composite score",
      def: "The published 0&ndash;1 structural-risk score: the geometric mean of the Recruitment (R) and Exploitation (E) phases.",
      basis: "A geometric mean penalizes imbalance: a country scores high only when both an exposed population and an unchecked environment are present; neither half alone produces forced labor.",
      src: "Computed from R and E." },
    "governance-modulator": { kind: "How the score is built", label: "Governance modulation",
      def: "Governance enters once, as a protective modulator: strong rule of law attenuates a domain&rsquo;s risk, weak rule of law leaves it largely intact.",
      basis: "Weak governance is a well-established structural driver of forced labour; rule of law explains roughly two-thirds of the score, disclosed as a finding rather than engineered away.",
      src: "World Bank WGI rule of law; V-Dem rule of law (v2x_rule)." },
  };
  // Foundational academic / legal literature per term — web-verified canonical works (author,
  // year, title, venue confirmed). Rendered as "Key literature" in the popover.
  const GLOSS_LIT = {
    "economic-precarity": ["Bales (1999), <i>Disposable People: New Slavery in the Global Economy</i>, Univ. of California Press", "Basu &amp; Van (1998), &lsquo;The Economics of Child Labor&rsquo;, <i>American Economic Review</i> 88(3)"],
    "debt-financialized-dependency": ["ILO Forced Labour Convention No. 29 (1930), recognises debt bondage as forced labour", "Genicot (2002), &lsquo;Bonded Labor and Serfdom&rsquo;, <i>J. Development Economics</i> 67(1)"],
    "constrained-mobility": ["Longva (1997), <i>Walls Built on Sand</i>, Westview Press", "Gardner (2010), <i>City of Strangers</i>, Cornell Univ. Press"],
    "ascriptive-exclusion": ["Tilly (1998), <i>Durable Inequality</i>, Univ. of California Press", "ILO (2007), <i>Equality at Work</i>, Geneva"],
    "gender-structuring": ["Elson &amp; Pearson (1981), &lsquo;Nimble Fingers Make Cheap Workers&rsquo;, <i>Feminist Review</i> 7", "Standing (1999), &lsquo;Global Feminization Through Flexible Labor&rsquo;, <i>World Development</i> 27(3)"],
    "age-childhood-structuring": ["Basu &amp; Van (1998), &lsquo;The Economics of Child Labor&rsquo;, <i>American Economic Review</i> 88(3)", "ILO Worst Forms of Child Labour Convention No. 182 (1999)"],
    "legal-non-recognition": ["Arendt (1951), <i>The Origins of Totalitarianism</i>, the &lsquo;right to have rights&rsquo;", "UN Convention relating to the Status of Stateless Persons (1954)"],
    "structural-disruption": ["Shelley (2010), <i>Human Trafficking: A Global Perspective</i>, Cambridge Univ. Press", "ILO / Walk Free / IOM (2017), <i>Global Estimates of Modern Slavery</i>"],
    "economic-structure-demand": ["LeBaron &amp; Phillips (2019), &lsquo;States and the Political Economy of Unfree Labour&rsquo;, <i>New Political Economy</i> 24(1)", "Phillips &amp; Mieres (2015), &lsquo;The Governance of Forced Labour in the Global Economy&rsquo;, <i>Globalizations</i> 12(2)"],
    "foreclosed-exit-structural": ["Manning (2003), <i>Monopsony in Motion</i>, Princeton Univ. Press", "ILO Freedom of Association Convention No. 87 (1948)"],
    "state-production-of-unfreedom": ["ILO Forced Labour Convention No. 29 (1930), Art. 2, state-compelled labour", "ILO / Walk Free (2017), <i>Global Estimates of Modern Slavery</i>, state-imposed category"],
    "domain-a-transnational-concealment": ["UN Convention against Transnational Organized Crime (Palermo, 2000)", "Sharman (2011), <i>The Money Laundry</i>, Cornell Univ. Press"],
    "domain-b-cash-informal-retention": ["Schneider &amp; Enste (2000), &lsquo;Shadow Economies&rsquo;, <i>J. Economic Literature</i> 38(1)", "FATF (2015), <i>Money Laundering Through the Physical Transportation of Cash</i>"],
    "phase-R": ["Cohen &amp; Felson (1979), &lsquo;Social Change and Crime Rate Trends: A Routine Activity Approach&rsquo;, <i>American Sociological Review</i> 44(4)"],
    "phase-E": ["Cohen &amp; Felson (1979), &lsquo;Social Change and Crime Rate Trends: A Routine Activity Approach&rsquo;, <i>American Sociological Review</i> 44(4)"],
    "monetization-lens": ["FATF (2011), <i>Money Laundering Risks Arising from Trafficking in Human Beings</i>", "Shelley (2010), <i>Human Trafficking: A Global Perspective</i>, Cambridge Univ. Press"],
    "composite": ["OECD / EC-JRC (2008), <i>Handbook on Constructing Composite Indicators</i>, OECD Publishing", "UNDP (2010), <i>Human Development Report 2010</i>, geometric-mean aggregation"],
    "governance-modulator": ["Datta &amp; Bales (2014), &lsquo;Slavery in Europe, Part 2&rsquo;, <i>Human Rights Quarterly</i> 36(2)", "ILO (2014), <i>Profits and Poverty: The Economics of Forced Labour</i>"],
  };
  let _glossEl = null;
  function closeGloss() { if (_glossEl) { _glossEl.remove(); _glossEl = null; } }
  function openGloss(key, anchor) {
    const g = GLOSSARY[key];
    if (!g || !anchor) return;
    closeGloss();
    const el = document.createElement("div");
    el.className = "gloss-pop";
    el.setAttribute("role", "dialog");
    el.innerHTML =
      `<button class="gloss-x" type="button" aria-label="Close">&times;</button>` +
      `<p class="gloss-kind">${g.kind}</p>` +
      `<h4 class="gloss-term">${g.label}</h4>` +
      `<p class="gloss-def">${g.def}</p>` +
      (g.basis ? `<p class="gloss-row"><span class="gloss-lab">Foundation</span>${g.basis}</p>` : "") +
      (g.src ? `<p class="gloss-row"><span class="gloss-lab">Source</span>${g.src}</p>` : "") +
      ((GLOSS_LIT[key] && GLOSS_LIT[key].length)
        ? `<p class="gloss-row"><span class="gloss-lab">Key literature</span>${GLOSS_LIT[key].map(c => `<span class="gloss-cite">${c}</span>`).join("")}</p>`
        : "");
    document.body.appendChild(el);
    _glossEl = el;
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, h = el.offsetHeight, m = 8;
    let left = Math.max(m, Math.min(r.left, window.innerWidth - w - m));
    let top = r.bottom + 6;
    if (top + h + m > window.innerHeight) top = r.top - h - 6;   // flip above the anchor
    top = Math.max(m, Math.min(top, window.innerHeight - h - m)); // always within the viewport
    el.style.left = left + "px";
    el.style.top = top + "px";
    el.querySelector(".gloss-x").addEventListener("click", closeGloss);
  }
  let _glossWired = false;
  function setupGloss() {
    if (_glossWired) return;
    _glossWired = true;
    // make every static [data-gloss] term keyboard-focusable (the framework inline links were
    // mouse-only); dynamically-rendered terms set tabindex/role in their own markup.
    document.querySelectorAll("[data-gloss]").forEach(el => {
      if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "0");
      if (!el.hasAttribute("role")) el.setAttribute("role", "button");
    });
    document.addEventListener("click", (e) => {
      const t = e.target.closest("[data-gloss]");
      if (t) { e.preventDefault(); openGloss(t.getAttribute("data-gloss"), t); return; }
      if (_glossEl && !e.target.closest(".gloss-pop")) closeGloss();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeGloss(); return; }
      const a = document.activeElement;
      if ((e.key === "Enter" || e.key === " ") && a && a.matches && a.matches("[data-gloss]")) {
        e.preventDefault();
        openGloss(a.getAttribute("data-gloss"), a);
      }
    });
    window.addEventListener("resize", closeGloss);
    window.addEventListener("scroll", closeGloss, true);
  }

  // ---- Leaflet helpers ----
  function makeTip(container) {
    const tip = document.createElement("div");
    tip.className = "map-tip";
    container.appendChild(tip);
    return {
      show(html, x, y) { tip.innerHTML = html; tip.style.opacity = "1"; this.move(x, y); },
      move(x, y) {
        const r = container.getBoundingClientRect();
        let lx = x - r.left + 14, ly = y - r.top + 14;
        if (lx + 270 > r.width) lx = x - r.left - 270;
        tip.style.left = lx + "px"; tip.style.top = ly + "px";
      },
      hide() { tip.style.opacity = "0"; },
    };
  }

  // Auto-pan options so a freshly opened popup is nudged clear of the floating overlay cards
  // (the top-left title and the right-side layer switch). On stacked/mobile layouts those cards
  // don't float over the map, so the padding collapses to a small inset.
  function popupPan(map) {
    const s = (map && map.getSize) ? map.getSize() : { x: 1000, y: 600 };
    const big = s.x > 860;
    return {
      autoPanPaddingTopLeft: big ? [Math.min(470, Math.round(s.x * 0.5)), Math.min(150, Math.round(s.y * 0.42))] : [16, 16],
      autoPanPaddingBottomRight: big ? [Math.min(230, Math.round(s.x * 0.4)), Math.min(96, Math.round(s.y * 0.3))] : [16, 16],
      keepInView: true,
    };
  }

  // Quick-look popup: a small country card anchored on the map, so a click previews the score,
  // rank, tier and R/E before anyone leaves the page. The "View full profile" link does the nav.
  function openCountryPeek(rec, latlng, map, meta, onProfile) {
    if (typeof L === "undefined" || !rec) return;
    const t = tierOf(rec.composite);
    const tierLbl = { high: "Higher", mid: "Middle", low: "Lower" }[t] || "";
    let lead = "";
    if (rec.R != null && rec.E != null) {
      const gap = rec.E - rec.R;
      lead = gap > 0.06 ? "Its risk is driven more by conditions that could let labor exploitation run unchecked than by how exposed people are to being recruited into it."
           : gap < -0.06 ? "Its risk is driven more by how exposed people are to being recruited into forced labor than by conditions that let exploitation run unchecked."
           : "Exposure to recruitment and the conditions for exploitation weigh about equally here.";
    }
    const bar = (lbl, v) => `<div class="peek-bar"><span class="pb-l">${lbl}</span><span class="pb-track"><span class="pb-fill" style="width:${Math.min(100, (v || 0) * 100).toFixed(0)}%"></span></span><span class="pb-v">${fmt(v)}</span></div>`;
    const pb = bandText(rec);
    const html =
      `<div class="peek">` +
      `<div class="peek-head"><b>${rec.name}</b> <span class="peek-iso">${rec.iso3}</span>${confChip(rec)}</div>` +
      `<div class="peek-score">${fmt(rec.composite)} ${tierChip(rec) || `<span class="tier tier-${t}">${tierLbl} tier</span>`}</div>` +
      `<div class="peek-meta">structural-risk score &middot; rank ${rec.rank}${pb ? ` (${pb} plausible)` : ""} of ${meta.n_scored}</div>` +
      `<div class="peek-bars">${bar("R", rec.R)}${bar("E", rec.E)}</div>` +
      (lead ? `<p class="peek-lead">${lead}</p>` : "") +
      `<a class="peek-link" href="#">View full profile &rarr;</a>` +
      `</div>`;
    const pop = L.popup(Object.assign({ className: "peek-popup", maxWidth: 252, minWidth: 212, closeButton: true }, popupPan(map)))
      .setLatLng(latlng).setContent(html).openOn(map);
    const el = pop.getElement && pop.getElement();
    const link = el && el.querySelector(".peek-link");
    if (link) link.addEventListener("click", ev => { ev.preventDefault(); map.closePopup(pop); if (onProfile) onProfile(rec.iso3); });
  }

  // Atlas-name (lowercased) -> the score-table name (lowercased). against
  // world-atlas@2 countries-110m.json and data/scores.json. Names absent from the 110m atlas
  // (small island states, e.g. Cabo Verde, Maldives, Singapore, Malta, Mauritius) have no
  // polygon at this resolution and correctly render as no-data; they are not faked.
  const ATLAS_ALIAS = {
    "united states of america": "united states",
    "dem. rep. congo": "congo, the democratic republic of the",
    "central african rep.": "central african republic",
    "dominican rep.": "dominican republic",
    "s. sudan": "south sudan",
    "eq. guinea": "equatorial guinea",
    "bosnia and herz.": "bosnia and herzegovina",
    "macedonia": "north macedonia",
    "palestine": "palestine, state of",
    "russia": "russian federation",
    "brunei": "brunei darussalam",
    "turkey": "türkiye",
    "w. sahara": "western sahara",
    "solomon is.": "solomon islands",
    "antigua and barb.": "antigua and barbuda",
    "são tomé and principe": "sao tome and principe",
  };

  // ================= COMPOSITE MAP =================
  // Country polygons from world-atlas (CDN), shaded by v0_4_spu_w05_composite.
  function compositeMap(elId, opts = {}) {
    const el = document.getElementById(elId);
    if (!el || typeof L === "undefined") return;
    const map = L.map(elId, {
      scrollWheelZoom: false, doubleClickZoom: true,
      minZoom: 1, maxZoom: 6, attributionControl: true,
      maxBounds: [[-60, -180], [85, 180]], maxBoundsViscosity: 1.0,
    }).setView([18, 12], 1.6);
    // CARTO light, label-free basemap — one clean muted base, no multilingual clutter.
    // noWrap keeps a single world (no repeated continents across the antimeridian).
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; OpenStreetMap, &copy; CARTO', subdomains: "abcd", maxZoom: 19, noWrap: true,
    }).addTo(map);
    const tip = makeTip(el);

    Promise.all([
      loadScores(),
      fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json").then(r => r.json()),
    ]).then(([scores, atlas]) => {
      const meta = scores.meta;
      const byName = {};
      scores.countries.forEach(c => { byName[c.name.toLowerCase()] = c; });
      const geo = splitAntimeridian(topojson.feature(atlas, atlas.objects.countries));

      function recFor(feat) {
        const nm = (feat.properties && feat.properties.name || "").toLowerCase();
        if (byName[nm]) return byName[nm];
        const a = ATLAS_ALIAS[nm];
        return a ? byName[a] : null;
      }

      const vmax = 1.0; // honest fixed domain: 1.0 is the theoretical worst
      const layer = L.geoJSON(geo, {
        style: f => {
          const rec = recFor(f);
          const v = rec && rec.scored ? rec.composite : null;
          return { color: "#888", weight: 0.4, fillColor: rampColor(v, vmax), fillOpacity: v === null ? 0.0 : 0.88 };
        },
        onEachFeature: (f, lyr) => {
          const rec = recFor(f);
          lyr.on("mousemove", e => {
            const nm = f.properties.name;
            let html;
            if (rec && rec.scored) {
              const b = bandText(rec);
              html = `<b>${rec.name}</b><br>Structural risk score <span class="v">${fmt(rec.composite)}</span><br>Rank ${rec.rank}${b ? ` <span class="muted">(band ${b})</span>` : ""} of ${meta.n_scored}` +
                (rec.low_confidence ? `<br><span class="muted">Lower-confidence score (${(rec.n_domains || 11) - rec.domains_not_scored} of ${rec.n_domains || 11} domains)</span>` : "");
            } else {
              html = `<b>${nm}</b><br><span class="muted">Not scored: data too thin for a fair comparison</span>`;
            }
            tip.show(html, e.originalEvent.clientX, e.originalEvent.clientY);
            lyr.setStyle({ weight: 1.2, color: "#222" });
          });
          lyr.on("mouseout", () => { tip.hide(); layer.resetStyle(lyr); });
          lyr.on("click", () => {
            if (rec && rec.scored && opts.onClick) opts.onClick(rec);
          });
        },
      }).addTo(map);

      if (opts.onReady) opts.onReady({ scores, meta });
      // settle sizing in case the container got its height from CSS after the map initialised
      setTimeout(() => map.invalidateSize(), 60);
      window.addEventListener("resize", () => map.invalidateSize());
    }).catch(err => {
      el.innerHTML = `<div class="slot-empty"><h3>Map could not load</h3><p>The base geometry is fetched from a public map service. ${err}</p></div>`;
    });
  }

  // ================= SUBNATIONAL MAP =================
  // admin-1 risk surface from data/admin1_risk.topojson (prop "risk").
  function subnationalMap(elId) {
    const el = document.getElementById(elId);
    if (!el || typeof L === "undefined") return;
    const map = L.map(elId, {
      scrollWheelZoom: false, doubleClickZoom: true,
      minZoom: 1, maxZoom: 7,
      maxBounds: [[-60, -180], [85, 180]], maxBoundsViscosity: 1.0,
    }).setView([10, 30], 2);
    // Dark-matter (label-free) basemap makes the lit-up risk surface read as a coherent layer rather
    // than a scatter of pale patches on white — the subnational story is the headline, so it must pop.
    // noWrap keeps a single world (no repeated continents across the antimeridian).
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; OpenStreetMap, &copy; CARTO', subdomains: "abcd", maxZoom: 19, noWrap: true,
    }).addTo(map);
    const tip = makeTip(el);

    fetch(rel("data/admin1_risk.topojson")).then(r => r.json()).then(topo => {
      const obj = topo.objects[Object.keys(topo.objects)[0]];
      const geo = splitAntimeridian(topojson.feature(topo, obj));
      // observed risk range for the stretch (subnational surface tops ~0.78)
      let lo = 1, hi = 0, nScored = 0;
      geo.features.forEach(f => { const v = f.properties.risk; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); nScored++; } });
      const layer = L.geoJSON(geo, {
        style: f => {
          const v = f.properties.risk;
          if (v == null) {
            // covered-but-no-reliable-sample regions: a faint outline so the surface reads as
            // a continuous mapped layer, but they stay visibly distinct from scored regions.
            return { color: "#6b6760", weight: 0.3, fillColor: "#3a3833", fillOpacity: 0.35 };
          }
          return { color: "#1a1814", weight: 0.3, fillColor: rampStretch(v, lo, hi), fillOpacity: 0.92 };
        },
        onEachFeature: (f, lyr) => {
          const p = f.properties;
          lyr.on("mousemove", e => {
            let html = `<b>${p.name || p.cntry}</b><br><span class="muted">${p.cntry}</span>`;
            if (p.risk != null) html += `<br>Local risk surface <span class="v">${fmt(p.risk)}</span>`;
            else html += `<br><span class="muted">Mapped here, but the local census sample is too thin for a reliable estimate</span>`;
            tip.show(html, e.originalEvent.clientX, e.originalEvent.clientY);
            lyr.setStyle({ weight: 1.1, color: "#fff" });
            lyr.bringToFront();
          });
          lyr.on("mouseout", () => { tip.hide(); layer.resetStyle(lyr); });
        },
      }).addTo(map);
      // Lock the initial frame on the data-bearing latitudes (the surface clusters across the
      // tropics) so the reader lands on a map full of regions, not an empty ocean.
      map.setView([10, 25], 2);
      // A small caption baked onto the map confirms the layer drew its full population.
      const ctrl = L.control({ position: "bottomleft" });
      ctrl.onAdd = function () {
        const d = L.DomUtil.create("div", "map-inset-note");
        d.innerHTML = `${nScored.toLocaleString()} regions on the risk surface`;
        return d;
      };
      ctrl.addTo(map);
    }).catch(err => {
      el.innerHTML = `<div class="slot-empty"><h3>Subnational layer could not load</h3><p>${err}</p></div>`;
    });
  }

  // ================= PER-DOMAIN RISK CIRCLES =================
  // Phase order + display copy for the per-domain breakdown. Recruitment + Exploitation
  // domains aggregate (equal-weight) into R and E; Monetization is a LENS, never in the
  // published score (locked rule 7), so it is shown separately and labelled as such.
  const PHASE_ORDER = ["Recruitment", "Exploitation", "Monetization"];
  const PHASE_COPY = {
    Recruitment: { head: "Recruitment: who is made vulnerable", note: "averages into R", gloss: "phase-R" },
    Exploitation: { head: "Exploitation: where it runs unchecked", note: "averages into E", gloss: "phase-E" },
    Monetization: { head: "Monetization: how proceeds are hidden", lens: "lens, not in the score", gloss: "monetization-lens" },
  };
  // Disc size encodes risk too (28–56px) so high-risk domains read larger as well as darker;
  // shade is the YlOrRd composite ramp. Not-scored domains are hatched grey discs (never 0).
  function domDisc(d) {
    if (!d || d.scored !== true || d.score === null || d.score === undefined) {
      return `<div class="dom-disc-wrap"><div class="dom-disc dom-na" style="width:34px;height:34px"
        title="Not scored: too little data clears the coverage floor; never read as zero">
        <span class="dom-num">n/a</span></div></div>`;
    }
    const v = Math.max(0, Math.min(1, d.score));
    const size = (28 + v * 28).toFixed(0);             // 28..56 px
    const fill = rampColor(v, 1.0);                     // YlOrRd, anchored to 0..1
    const dark = v >= 0.5;                              // pick legible number colour
    return `<div class="dom-disc-wrap"><div class="dom-disc"
      style="width:${size}px;height:${size}px;background:${fill}"
      title="${d.label}: ${fmt(d.score)}${d.low_conf ? " (low confidence)" : ""}${(d.modeled_input && Array.isArray(d.interval)) ? ` — includes 1 modeled estimate (80% interval ${fmt(d.interval[0], 3)}–${fmt(d.interval[1], 3)})` : ""}">
      <span class="dom-num" style="color:${dark ? "#fff" : "#1a1814"}">${fmt(d.score)}</span></div></div>`;
  }
  function domCell(d) {
    const lc = (d && d.scored && d.low_conf) ? `<span class="dom-lc">low confidence</span>` : "";
    const ns = (d && d.scored !== true) ? `<span class="dom-ns">not scored</span>` : "";
    const mod = (d && d.modeled_input) ? `<span class="dom-mod">modeled estimate</span>` : "";
    const modNote = (d && d.modeled_input && Array.isArray(d.interval))
      ? `<div class="dom-mod-note">includes ${d.modeled_n || 1} modeled estimate of ${d.n_signals || ""} signals · 80% interval ${fmt(d.interval[0], 3)}–${fmt(d.interval[1], 3)}</div>` : "";
    const has = d && d.key && GLOSSARY[d.key];
    const attrs = has ? ` data-gloss="${d.key}" role="button" tabindex="0" aria-label="${d.label}: what this domain measures and where its data come from"` : "";
    const cue = has ? `<span class="gloss-i" aria-hidden="true">i</span>` : "";
    return `<div class="dom-cell${has ? " has-gloss" : ""}"${attrs}>${domDisc(d)}
      <div class="dom-label">${d ? d.label : ""}${cue}${lc}${ns}${mod}</div>${modNote}</div>`;
  }
  function renderDomainCircles(iso, domAll, mountEl) {
    if (!mountEl) return;
    const rec = domAll && domAll[iso];
    if (!rec) { mountEl.innerHTML = ""; return; }
    // group by phase
    const byPhase = { Recruitment: [], Exploitation: [], Monetization: [] };
    Object.keys(rec).forEach(slug => {
      const d = rec[slug];
      d.key = slug;
      if (byPhase[d.phase]) byPhase[d.phase].push(d);
    });
    const legend = `
      <div class="dom-legend">
        <span class="lg-item">lower risk
          <span class="ramp" aria-hidden="true">${RAMP.map(c => `<span style="background:${c}"></span>`).join("")}</span>
          higher risk</span>
        <span class="lg-item"><span class="lg-dot na"></span> not scored (never zero)</span>
        <span class="lg-item">larger / darker = higher structural risk</span>
      </div>`;
    let html = `<h3>Risk across each domain</h3>
      <p class="muted" style="margin-top:0"><small>Each circle is one structural domain; its size and shade show how strongly that condition is present, on the same scale as the score. Recruitment domains average into R and Exploitation domains into E; Monetization is shown as a lens and is not part of the published score. Domains flagged low-confidence run on thin or partial data; not-scored domains are left grey rather than counted as zero.</small></p>
      <div class="cp-domains">`;
    PHASE_ORDER.forEach(phase => {
      const cells = byPhase[phase];
      if (!cells.length) return;
      // stable display order: highest risk first, not-scored last
      cells.sort((a, b) => {
        const av = (a.scored && a.score != null) ? a.score : -1;
        const bv = (b.scored && b.score != null) ? b.score : -1;
        return bv - av;
      });
      const cp = PHASE_COPY[phase];
      const noteHtml = cp.lens
        ? `<span class="phase-note phase-lens">${cp.lens}</span>`
        : `<span class="phase-note">${cp.note}</span>`;
      const headAttrs = cp.gloss ? ` class="has-gloss" data-gloss="${cp.gloss}" role="button" tabindex="0"` : "";
      const headCue = cp.gloss ? `<span class="gloss-i" aria-hidden="true">i</span>` : "";
      html += `<div class="dom-phase">
        <div class="cp-phase-head"><h4${headAttrs}>${cp.head}${headCue}</h4>${noteHtml}</div>
        <div class="dom-grid">${cells.map(domCell).join("")}</div>
      </div>`;
    });
    html += `</div>${legend}`;
    mountEl.innerHTML = html;
  }

  // ================= DOMAIN RADAR (profile) =================
  // A spider/radar chart of the per-domain scores so a reader sees a country's structural
  // "shape" — which domains are strongest — at a glance. The filled polygon spans the 11
  // Recruitment + Exploitation domains that drive the score; the 2 Monetization domains are
  // drawn as distinct points (a lens, not in the composite). Axis labels open the glossary.
  function renderRadar(rec, domAll, mountEl) {
    if (!mountEl) return;
    const recDoms = domAll && domAll[rec.iso3];
    if (!recDoms) { mountEl.innerHTML = ""; return; }
    const order = [];
    PHASE_ORDER.forEach(ph => Object.keys(recDoms).forEach(slug => {
      if (recDoms[slug].phase === ph) order.push(Object.assign({ slug }, recDoms[slug]));
    }));
    const N = order.length;
    if (!N) { mountEl.innerHTML = ""; return; }
    const W = 600, H = 470, cx = 300, cy = 235, R = 150;
    const ang = i => (-90 + i * 360 / N) * Math.PI / 180;
    const pt = (i, rad) => [cx + Math.cos(ang(i)) * rad, cy + Math.sin(ang(i)) * rad];
    const f1 = n => n.toFixed(1);
    const val = d => (d.scored === true && d.score != null) ? Math.max(0, Math.min(1, d.score)) : 0;
    let g = "";
    // concentric grid rings (0.25 .. 1.0)
    [0.25, 0.5, 0.75, 1].forEach(rr => {
      g += `<polygon class="radar-ring" fill="none" stroke="#e2dccd" points="${order.map((_, i) => pt(i, rr * R).map(f1).join(",")).join(" ")}"></polygon>`;
    });
    // spokes + axis labels (long labels wrap to two balanced lines; labels open the glossary)
    order.forEach((d, i) => {
      const isLens = d.phase === "Monetization";
      const [ex, ey] = pt(i, R);
      g += `<line class="radar-spoke${isLens ? " radar-spoke-lens" : ""}" stroke="#e2dccd" x1="${cx}" y1="${cy}" x2="${f1(ex)}" y2="${f1(ey)}"></line>`;
      const [lx, ly] = pt(i, R + 13);
      const cosA = Math.cos(ang(i));
      const anchor = Math.abs(cosA) < 0.34 ? "middle" : (cosA > 0 ? "start" : "end");
      const words = d.label.split(" ");
      let lines = [d.label];
      if (d.label.length > 15 && words.length > 1) {
        let b = 1, bd = 1e9;
        for (let k = 1; k < words.length; k++) {
          const diff = Math.abs(words.slice(0, k).join(" ").length - words.slice(k).join(" ").length);
          if (diff < bd) { bd = diff; b = k; }
        }
        lines = [words.slice(0, b).join(" "), words.slice(b).join(" ")];
      }
      const LH = 10, first = -((lines.length - 1) / 2) * LH;
      const tsp = lines.map((ln, k) => `<tspan x="${f1(lx)}" dy="${k === 0 ? first : LH}">${ln}</tspan>`).join("");
      const has = GLOSSARY[d.slug];
      const attrs = has ? ` data-gloss="${d.slug}" role="button" tabindex="0"` : "";
      g += `<text class="radar-axis${isLens ? " radar-axis-lens" : ""}${has ? " has-gloss" : ""}"${attrs} x="${f1(lx)}" y="${f1(ly)}" text-anchor="${anchor}" dominant-baseline="middle">${tsp}</text>`;
    });
    // filled polygon over the score domains (Recruitment + Exploitation only)
    const scoreIdx = order.map((d, i) => d.phase !== "Monetization" ? i : -1).filter(i => i >= 0);
    const polyPts = scoreIdx.map(i => pt(i, val(order[i]) * R).map(f1).join(",")).join(" ");
    const fill = rampColor(rec.composite || 0.4, 1.0);
    g += `<polygon class="radar-area" points="${polyPts}" style="fill:${fill};fill-opacity:0.32;stroke:${fill};stroke-width:1.5"></polygon>`;
    // vertices (all domains): scored dot at value; not-scored hollow at centre; lens distinct
    order.forEach((d, i) => {
      const scored = d.scored === true && d.score != null;
      const isLens = d.phase === "Monetization";
      const [vx, vy] = pt(i, val(d) * R);
      const cls = !scored ? "radar-dot radar-dot-na" : (isLens ? "radar-dot radar-dot-lens" : "radar-dot");
      const paint = !scored ? `fill="#f4efe5" stroke="#a89f8d" stroke-width="1"`
        : (isLens ? `fill="#f4efe5" stroke="#1a1814" stroke-width="1.2"` : `fill="#1a1814"`);
      const px = scored ? f1(vx) : cx, py = scored ? f1(vy) : cy;
      const tip = scored
        ? `${d.label}: ${fmt(d.score)}${d.low_conf ? " (low confidence)" : ""}${isLens ? " (lens, not in score)" : ""}`
        : `${d.label}: not scored, no data, never zero`;
      g += `<circle class="${cls}" ${paint} cx="${px}" cy="${py}" r="3.1"><title>${tip}</title></circle>`;
    });
    // scale ticks on the top spoke
    g += `<text class="radar-scale" x="${cx + 3}" y="${f1(cy - 0.5 * R)}">0.5</text><text class="radar-scale" x="${cx + 3}" y="${f1(cy - R + 3)}">1.0</text>`;
    const naN = order.filter(d => !(d.scored === true && d.score != null)).length;
    mountEl.innerHTML =
      `<h3>Domain shape: what is strongest here</h3>
       <p class="muted" style="margin-top:0"><small>Each spoke is one structural domain; the further a point reaches from the centre, the more strongly that condition is present (0 at the centre; 1.0, the theoretical worst, at the rim). The longest spokes are this country's strongest structural-risk domains. The shaded shape spans the Recruitment + Exploitation domains that drive the score; the two italic Monetization spokes are separate points (a lens, not in the score). Click a domain name for its definition.${naN ? " Domains with no data sit at the centre, marked: not measured, never zero." : ""}</small></p>
       <div class="radar-wrap"><svg viewBox="0 0 ${W} ${H}" class="radar-svg" role="img" aria-label="Radar chart of ${rec.name} structural-risk domains">${g}</svg></div>
       <p class="muted" style="text-align:center;margin-top:2px"><small><a href="${pageHref("simulate.html")}#${rec.iso3}">Drag these domains and watch the score move, in the simulation &rarr;</a></small></p>`;
  }

  // ================= COUNTRY PANEL =================
  // Renders the full per-country profile: composite, rank, tier, the two REAL components
  // (R, E), how they combine (the real geometric-mean operator), and rank neighbours for context.
  function renderCountryPanel(rec, meta, mountId, all) {
    const mount = document.getElementById(mountId);
    if (!mount) return;
    if (!rec) { mount.innerHTML = ""; return; }
    const bar = (label, v, note) => `
      <div class="bar-row">
        <div>${label}${note ? `<br><small style="color:var(--ink-faint)">${note}</small>` : ""}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100, (v || 0) / 1.0 * 100).toFixed(1)}%"></div></div>
        <div class="bar-val">${fmt(v)}</div>
      </div>`;
    const tier = tierOf(rec.composite);
    const tierLbl = { high: "Higher", mid: "Middle", low: "Lower" }[tier] || "";
    // honest read of which half carries the score
    let lean = "";
    if (rec.R != null && rec.E != null) {
      const gap = rec.E - rec.R;
      if (gap > 0.06) lean = " Here the score is carried more by the <strong>conditions that let exploitation run unchecked</strong> than by vulnerability to recruitment.";
      else if (gap < -0.06) lean = " Here the score is carried more by <strong>vulnerability to recruitment</strong> than by the conditions for unchecked exploitation.";
      else lean = " Here the two halves are <strong>roughly balanced</strong>.";
    }
    // rank neighbours
    let neigh = "";
    if (all) {
      const scored = all.filter(c => c.scored).sort((a, b) => a.rank - b.rank);
      const i = scored.findIndex(c => c.iso3 === rec.iso3);
      const up = i > 0 ? scored[i - 1] : null, dn = i < scored.length - 1 ? scored[i + 1] : null;
      const sameTier = (up && tierOf(up.composite) === tier) || (dn && tierOf(dn.composite) === tier);
      neigh = `<p class="muted"><small>Ranked just below ${up ? `<a href="#${up.iso3}">${up.name}</a> (${fmt(up.composite)})` : "&mdash;"} and just above ${dn ? `<a href="#${dn.iso3}">${dn.name}</a> (${fmt(dn.composite)})` : "&mdash;"}.${sameTier && bandText(rec) ? " Rank bands overlap within a tier — differences of a few places are not meaningful." : ""}</small></p>`;
    }
    // uncertainty whisker: where this rank can plausibly sit (absent on legacy data)
    const stab = rec.tier_stability != null
      ? ` In 10,000 perturbed re-scorings, ${rec.name} stayed in the ${tierLbl} tier ${Math.round(rec.tier_stability * 100)}% of the time.` : "";
    const bandBlock = bandText(rec) ? `
        <div class="cp-band">
          ${bandBar(rec, meta)}
          <div class="cp-band-axis"><span>rank 1 (highest risk)</span><span>rank ${meta.n_scored}</span></div>
          <p class="muted" style="margin:2px 0 0"><small>The shaded span is the 90% plausible band for this rank (${bandText(rec)}) when the inputs and the R&ndash;E weight are perturbed within measurement uncertainty.${stab}</small></p>
        </div>` : "";
    // lower-confidence note naming the unscored domains
    const confNote = rec.low_confidence && rec.not_scored_domains && rec.not_scored_domains.length
      ? `<p class="muted"><small><strong>Lower-confidence score.</strong> ${rec.name}&rsquo;s composite rests on ${(rec.n_domains || 11) - rec.domains_not_scored} of ${rec.n_domains || 11} domains &mdash; not scored (no data, never zero): ${rec.not_scored_domains.map(s => s.replace(/-/g, " ")).join("; ")}. Its rank band is computed with wider noise accordingly.</small></p>` : "";
    const gm = Math.sqrt((rec.R || 0) * (rec.E || 0));
    mount.innerHTML = `
      <div class="cp-head">
        <h2>${rec.name} <span style="font-size:0.78rem;color:var(--ink-faint);letter-spacing:0.04em">${rec.iso3}</span></h2>
        <span class="cp-score">${fmt(rec.composite)}</span>
        <span class="cp-rank">${tierChip(rec)} &middot; structural risk score &middot; ${rankLine(rec, meta)}${confChip(rec)}</span>
      </div>
      <div class="cp-body">
        ${bandBlock}
        ${confNote}
        <p class="muted" style="margin-top:0">A score closer to 1.0 means more of the conditions that enable forced labor are present together. The highest real-country score is near ${fmt(meta.composite_max)}; 1.0 is a theoretical worst, not an observed one. Mid-table ranks are tightly packed; read the tier, not the exact position.${lean}</p>
        <h3>The two components that combine into this score</h3>
        <div class="cp-bars">
          ${bar("Vulnerability to recruitment (R)", rec.R, "who is exposed: poverty, displacement, weak legal protection")}
          ${bar("Conditions for unchecked exploitation (E)", rec.E, "weak enforcement, high-risk sectors, blocked exit")}
        </div>
        <div class="note tight" style="margin-top:2px">
          <small><strong>How they combine.</strong> The published score is the <em>geometric mean</em> of the two: &radic;(R &times; E) = &radic;(${fmt(rec.R)} &times; ${fmt(rec.E)}) = <strong>${fmt(gm)}</strong>. Both halves are separately necessary, so a country low on either is pulled down, but a single sparse half does not annihilate the score. How proceeds are hidden (the third part of the frame) feeds the intervention map, not this published risk score.</small>
        </div>
        <div id="cp-radar" data-iso="${rec.iso3}"></div>
        <div id="cp-domain-circles" data-iso="${rec.iso3}"></div>
        ${neigh}
        <p class="muted"><small>Confidence varies by country with data coverage; thinly-covered countries are not scored at all rather than assigned a misleading number. See where this concentrates and which levers are most structurally relevant on the <a href="${pageHref("intervention.html")}#${rec.iso3}">Intervention</a> page, or open the <a href="${pageHref("simulate.html")}#${rec.iso3}">simulation</a> to watch the score move as you change the structural assumptions.</small></p>
      </div>`;
    // Fill the per-domain circles asynchronously (data/domains.json). The mount lives
    // inside the panel just written; it is re-queried by id under the mount to stay scoped.
    const radarEl = mount.querySelector("#cp-radar");
    const circlesEl = mount.querySelector("#cp-domain-circles");
    if (circlesEl || radarEl) {
      loadDomains().then(dom => {
        // guard against a later selection having replaced the panel before resolution
        if (radarEl && radarEl.isConnected) renderRadar(rec, dom, radarEl);
        if (circlesEl && circlesEl.isConnected) renderDomainCircles(rec.iso3, dom, circlesEl);
      });
    }
  }

  // ================= COUNTRY PICKER =================
  // Supports deep-linking via location.hash (#ISO3) and keeps the hash in sync on change.
  function buildPicker(selectId, panelId) {
    return loadScores().then(scores => {
      const sel = document.getElementById(selectId);
      if (!sel) return scores;
      const scored = scores.countries.filter(c => c.scored).sort((a, b) => a.name.localeCompare(b.name));
      sel.innerHTML = `<option value="">Select a country…</option>` +
        scored.map(c => `<option value="${c.iso3}">${c.name}</option>`).join("");
      const show = iso => {
        const rec = scores.countries.find(c => c.iso3 === iso);
        if (rec) { sel.value = iso; renderCountryPanel(rec, scores.meta, panelId, scores.countries); }
      };
      sel.addEventListener("change", () => {
        if (sel.value) { history.replaceState(null, "", "#" + sel.value); show(sel.value); }
      });
      window.addEventListener("hashchange", () => {
        const iso = (location.hash || "").replace("#", "").toUpperCase();
        if (iso) show(iso);
      });
      const initial = (location.hash || "").replace("#", "").toUpperCase();
      if (initial && scores.countries.some(c => c.iso3 === initial && c.scored)) {
        show(initial);
      } else {
        // no deep-link: open the top-ranked country so the centrepiece isn't blank on load
        const top = scores.countries.filter(c => c.scored).sort((a, b) => a.rank - b.rank)[0];
        if (top) show(top.iso3);
      }
      return scores;
    });
  }

  // ================= RANKINGS / COVERAGE WIDGETS =================
  function fillMeta(scores) {
    document.querySelectorAll("[data-meta]").forEach(node => {
      const k = node.getAttribute("data-meta");
      const m = scores.meta;
      const map = {
        n_scored: m.n_scored, n_universe: m.n_universe,
        composite_max: fmt(m.composite_max), composite_min: fmt(m.composite_min),
      };
      if (k in map) node.textContent = map[k];
    });
  }
  function fillTopBottom(scores) {
    const scored = scores.countries.filter(c => c.scored).sort((a, b) => b.composite - a.composite);
    const top = scored.slice(0, 10), bottom = scored.slice(-10).reverse();
    const row = c => {
      const b = bandText(c);
      return `<tr><td class="num">${c.rank}${b ? `<span class="rank-band">${b}</span>` : ""}</td><td>${c.name}${confChip(c)}</td><td class="num">${fmt(c.composite)}</td></tr>`;
    };
    const t = document.getElementById("top-list"); if (t) t.innerHTML = top.map(row).join("");
    const b = document.getElementById("bottom-list"); if (b) b.innerHTML = bottom.map(row).join("");
  }

  // ================= FULL RANKINGS TABLE =================
  // Searchable + sortable table of every scored country, plus the unscored set shown honestly.
  // Tier cut thresholds (0.281 / 0.402) are FROZEN at the registration-stage terciles
  // (docs/validation/validation_results_v2_v03.json, uncertainty.tier_cut_thresholds) and are
  // deliberately not re-derived per build — banding thresholds are fixed at registration (docs/METHODS.md §7).
  // Tiers are reported, not precise mid-table ranks, per that pass.
  const TIER_CUT = [0.281, 0.402];
  function tierOf(v) {
    if (v == null) return null;
    if (v >= TIER_CUT[1]) return "high";
    if (v >= TIER_CUT[0]) return "mid";
    return "low";
  }
  const TIER_LABEL = { high: "Higher", mid: "Middle", low: "Lower" };

  // ---- uncertainty helpers (Monte-Carlo rank bands shipped in data/scores.json) ----
  // Every helper null-guards: with an older scores.json (no band fields) the display
  // degrades to exactly the pre-band rendering.
  function bandText(c) {
    return (c && c.rank_p5 != null && c.rank_p95 != null) ? `${c.rank_p5}–${c.rank_p95}` : null;
  }
  function rankLine(c, meta) {
    const b = bandText(c);
    return `rank ${c.rank}${b ? ` <span class="rank-band-inline">(90% band ${b})</span>` : ""} of ${meta.n_scored} scored`;
  }
  function confChip(c) {
    if (!c || !c.low_confidence) return "";
    const total = c.n_domains || 11;
    const have = total - (c.domains_not_scored || 0);
    return ` <span class="conf-low" title="Composite rests on ${have} of ${total} domains; treated as lower-confidence (wider rank band)">low confidence</span>`;
  }
  function tierChip(c) {
    const t = tierOf(c.composite);
    if (!t) return "";
    const borderline = c.tier_stability != null && c.tier_stability < 0.70;
    const title = borderline
      ? ` title="Borderline tier: stayed in this tier in only ${Math.round(c.tier_stability * 100)}% of 10,000 perturbed re-scorings"`
      : "";
    return `<span class="tier tier-${t}${borderline ? " tier-borderline" : ""}"${title}>${TIER_LABEL[t]}</span>`;
  }
  function bandBar(c, meta) {
    if (!bandText(c) || !meta || !meta.n_scored) return "";
    const n = meta.n_scored;
    const pos = r => (n > 1 ? ((r - 1) / (n - 1)) * 100 : 0);
    const left = pos(c.rank_p5);
    const width = Math.max(1.5, pos(c.rank_p95) - left);
    return `<span class="band-bar" title="90% plausible rank band ${c.rank_p5}–${c.rank_p95}; published rank ${c.rank}">` +
      `<span class="band-fill" style="left:${left.toFixed(1)}%;width:${width.toFixed(1)}%"></span>` +
      `<span class="band-tick" style="left:${pos(c.rank).toFixed(1)}%"></span></span>`;
  }

  function buildRankings(opts = {}) {
    const searchId = opts.searchId || "rank-search";
    const bodyId = opts.bodyId || "rank-body";
    const countId = opts.countId || "rank-count";
    return loadScores().then(scores => {
      const body = document.getElementById(bodyId);
      if (!body) return scores;
      const scored = scores.countries.filter(c => c.scored).sort((a, b) => a.rank - b.rank);
      const unscored = scores.countries.filter(c => !c.scored).sort((a, b) => a.name.localeCompare(b.name));
      let sortKey = "rank", sortDir = 1, query = "";

      function render() {
        const q = query.trim().toLowerCase();
        let rows = scored.filter(c => !q || c.name.toLowerCase().includes(q) || c.iso3.toLowerCase().includes(q));
        rows.sort((a, b) => {
          let av, bv;
          if (sortKey === "name") { av = a.name; bv = b.name; return av.localeCompare(bv) * sortDir; }
          av = a[sortKey]; bv = b[sortKey];
          return (av - bv) * sortDir;
        });
        // tier section headers only in the default (rank-ascending, unfiltered) view,
        // where tiers are contiguous; under any other sort they would interleave.
        const showTierHeads = !q && sortKey === "rank" && sortDir === 1;
        const tierRange = t => t === "high" ? `composite &ge; ${TIER_CUT[1]}`
          : t === "mid" ? `composite ${TIER_CUT[0]}&ndash;${TIER_CUT[1]}`
          : `composite &lt; ${TIER_CUT[0]}`;
        let html = "";
        let lastTier = null;
        rows.forEach(c => {
          const t = tierOf(c.composite);
          if (showTierHeads && t !== lastTier) {
            lastTier = t;
            html += `<tr class="tier-head"><td colspan="7">${TIER_LABEL[t]} tier &middot; ${tierRange(t)}</td></tr>`;
          }
          const b = bandText(c);
          html += `
          <tr>
            <td>${tierChip(c)}</td>
            <td class="name-cell"><a class="rank-link" href="profiles.html#${c.iso3}">${c.name}</a> <span class="iso">${c.iso3}</span>${confChip(c)}</td>
            <td class="num rank-cell">${c.rank}${b ? `<span class="rank-band">${b}</span>` : ""}</td>
            <td class="num">${fmt(c.composite)}</td>
            <td class="num soft">${fmt(c.R)}</td>
            <td class="num soft">${fmt(c.E)}</td>
            <td class="mini">${bandBar(c, scores.meta) || `<span class="mini-bar"><span style="width:${(c.composite * 100).toFixed(1)}%;background:${rampColor(c.composite, 0.7)}"></span></span>`}</td>
          </tr>`;
        });
        // append unscored block only when not searching, or when the query matches one
        if (!q || unscored.some(c => c.name.toLowerCase().includes(q))) {
          const uShown = unscored.filter(c => !q || c.name.toLowerCase().includes(q));
          if (uShown.length) {
            html += `<tr class="sep-row"><td colspan="7">Not scored: data too thin for a fair comparison (${unscored.length} countries)</td></tr>`;
            html += uShown.map(c => `
              <tr class="unscored-row">
                <td><span class="tier tier-none">Not scored</span></td>
                <td class="name-cell">${c.name} <span class="iso">${c.iso3}</span></td>
                <td class="num">&mdash;</td>
                <td class="num">&mdash;</td><td class="num soft">&mdash;</td><td class="num soft">&mdash;</td><td></td>
              </tr>`).join("");
          }
        }
        body.innerHTML = html;
        const cnt = document.getElementById(countId);
        if (cnt) cnt.textContent = q ? `${rows.length} of ${scored.length} scored countries` : `${scored.length} scored countries`;
      }

      const search = document.getElementById(searchId);
      if (search) search.addEventListener("input", e => { query = e.target.value; render(); });
      document.querySelectorAll("[data-sort]").forEach(th => {
        th.addEventListener("click", () => {
          const k = th.getAttribute("data-sort");
          if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = (k === "name" || k === "rank") ? 1 : -1; }
          document.querySelectorAll("[data-sort]").forEach(x => x.removeAttribute("data-active"));
          th.setAttribute("data-active", sortDir > 0 ? "asc" : "desc");
          render();
        });
      });
      render();
      return scores;
    });
  }

  // ================= SIMULATION ENGINE =================
  // A REAL recompute of the published model from the shipped data. Every country's score is
  // recomputed from its real R and E (data/scores.json) under the operator/weights the reader picks;
  // ranks are recomputed across the whole field; the map, the readout, and the leaderboard re-render
  // live. Nothing is invented: the DEFAULT is the exact published formula — an equal-weight
  // geometric mean of R and E — which reproduces data/scores.json to within rounding (verified).
  //
  // What the reader can vary:
  //   1. The R-vs-E weight (a single slider; 0.5/0.5 is the published equal weight).
  //   2. The combining operator: weighted GEOMETRIC mean (published, soft-conjunctive) vs weighted
  //      ARITHMETIC mean (fully compensatory) — the two ends the scoring rules describe.
  //   3. A governance what-if toggle, grounded in the published discriminant result on the
  //      displayed build (composite-vs-governance R^2 = 0.628; docs/validation/): it shrinks the
  //      across-country SPREAD of the composite by sqrt(1 - R^2) ~ 0.61, leaving the
  //      forced-labor-specific residual. This is a labelled reweighting of the shipped composite,
  //      not a re-run of the upstream pipeline, and the UI says so.
  const SIM = {
    GOV_R2: 0.628,
    state: { country: null, wE: 0.5, operator: "geometric", govStrip: false, domainOverrides: {} },
    scores: null, domains: null, _reshade: null,
  };

  // Effective R/E for the focus country given any per-domain overrides. R is the mean of the
  // country's scored Recruitment domains and E the mean of its Exploitation domains — verified
  // to reproduce the shipped R/E exactly, so moving a domain is a faithful recompute, not a proxy.
  function simFocusRE() {
    const d = SIM.domains && SIM.domains[SIM.state.country];
    if (!d) return null;
    const ov = SIM.state.domainOverrides || {};
    const phaseMean = ph => {
      let s = 0, n = 0;
      Object.keys(d).forEach(k => {
        const dd = d[k];
        if (dd.phase === ph && dd.scored === true && dd.score != null) {
          s += (ov[k] != null ? ov[k] : dd.score); n++;
        }
      });
      return n ? s / n : null;
    };
    return { R: phaseMean("Recruitment"), E: phaseMean("Exploitation") };
  }

  function simCombine(R, E, st) {
    if (R == null || E == null) return null;
    const wE = st.wE, wR = 1 - wE;
    if (st.operator === "arithmetic") return wR * R + wE * E;
    return Math.pow(Math.max(1e-6, R), wR) * Math.pow(Math.max(1e-6, E), wE); // weighted geometric mean
  }

  // Recompute the whole field -> [{iso3,name,R,E,base,sim,rank}] sorted by sim desc, with new ranks.
  function simRecomputeField(scores, st, focusRE) {
    let rows = scores.countries.filter(c => c.scored).map(c => {
      let R = c.R, E = c.E;
      if (focusRE && c.iso3 === st.country && focusRE.R != null && focusRE.E != null) { R = focusRE.R; E = focusRE.E; }
      return { iso3: c.iso3, name: c.name, R, E, base: c.composite, sim: simCombine(R, E, st) };
    });
    if (st.govStrip) {
      // Remove the governance-attributable share of the across-country variance: residualize about
      // the field mean and rescale the residual by sqrt(1 - R^2). This is the textbook
      // "variance not explained by X" transform, applied to the published composite.
      const vals = rows.map(r => r.sim).filter(v => v != null);
      const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
      const keep = Math.sqrt(1 - SIM.GOV_R2);
      rows.forEach(r => { if (r.sim != null) r.sim = Math.max(0, mean + (r.sim - mean) * keep); });
    }
    rows.sort((a, b) => b.sim - a.sim);
    rows.forEach((r, i) => { r.rank = i + 1; });
    return rows;
  }

  function simRender() {
    const scores = SIM.scores, st = SIM.state;
    if (!scores) return;
    const focusRE = simFocusRE();
    const field = simRecomputeField(scores, st, focusRE);
    const byIso = {}; field.forEach(r => { byIso[r.iso3] = r; });
    const $ = id => document.getElementById(id);

    const me = byIso[st.country];
    if (me) {
      const pub = scores.countries.find(c => c.iso3 === me.iso3);
      const baseRank = pub.rank;
      const pubBand = bandText(pub);
      const set = (id, v) => { if ($(id)) $(id).textContent = v; };
      set("sim-name", me.name); set("sim-iso", me.iso3);
      set("sim-base", fmt(me.base)); set("sim-sim", fmt(me.sim));
      set("sim-R", fmt(me.R)); set("sim-E", fmt(me.E));
      set("sim-R-top", fmt(me.R)); set("sim-E-top", fmt(me.E));
      // published side carries its 90% band; the simulated side deliberately does NOT
      // (the bands quantify measurement uncertainty of the published inputs only)
      set("sim-rank", `${me.rank}`);
      set("sim-rank-base", `${baseRank}${pubBand ? ` (band ${pubBand})` : ""}`);
      const d = me.sim - me.base, de = $("sim-delta");
      if (de) {
        const cls = d > 0.005 ? "up" : d < -0.005 ? "down" : "flat";
        de.className = "sim-arrow " + cls;
        de.textContent = `${d > 0.005 ? "▲" : d < -0.005 ? "▼" : "·"} ${Math.abs(d).toFixed(3)} vs published`;
      }
      const te = $("sim-tier");
      if (te) { const t = tierOf(me.sim); te.className = "tier tier-" + t; te.textContent = { high: "Higher", mid: "Middle", low: "Lower" }[t] + " tier"; }
      // component bars
      const setBar = (id, v) => { const el = $(id); if (el) el.style.width = Math.min(100, (v || 0) * 100).toFixed(1) + "%"; };
      setBar("sim-bar-R", me.R); setBar("sim-bar-E", me.E); setBar("sim-bar-sim", me.sim);
      // full-field position strip: where the focus country lands among ALL scored countries,
      // published vs. now, on a fixed 0..0.8 axis so the marks stay stable while you drag.
      const strip = $("sim-strip");
      if (strip) {
        const px = v => Math.max(0, Math.min(100, (v / 0.8) * 100)).toFixed(1);
        const ticks = field.map(r => `<span class="strip-tick" style="left:${px(r.sim)}%"></span>`).join("");
        strip.innerHTML =
          `<div class="strip-track">${ticks}` +
          `<span class="strip-mk strip-base" style="left:${px(me.base)}%"></span>` +
          `<span class="strip-mk strip-sim" style="left:${px(me.sim)}%"></span></div>` +
          `<p class="strip-cap"><span>lower risk</span><span class="strip-now"><b>${me.name}</b> &middot; published ${fmt(me.base)} (rank ${baseRank}${pubBand ? `, band ${pubBand}` : ""}) &rarr; now <b>${fmt(me.sim)}</b> (simulated rank ${me.rank} of ${field.length})</span><span>higher</span></p>`;
      }
    }
    // formula echo
    const fe = $("sim-formula");
    if (fe) {
      const wE = st.wE.toFixed(2), wR = (1 - st.wE).toFixed(2);
      const op = st.operator === "geometric"
        ? `R<sup>${wR}</sup> &middot; E<sup>${wE}</sup> &nbsp;<span class="muted">(weighted geometric mean, the published operator)</span>`
        : `${wR}&middot;R + ${wE}&middot;E &nbsp;<span class="muted">(weighted arithmetic mean, fully compensatory)</span>`;
      fe.innerHTML = `score = ${op}${st.govStrip ? `<br>then shrink the across-country spread by &radic;(1&minus;${SIM.GOV_R2}) &asymp; ${Math.sqrt(1 - SIM.GOV_R2).toFixed(2)} to leave the non-governance residual` : ""}`;
    }
    // weight label
    const wl = $("sim-weight-label");
    if (wl) wl.innerHTML = `Weight: vulnerability&nbsp;(R) <strong>${Math.round((1 - st.wE) * 100)}%</strong> &middot; exploitation&nbsp;(E) <strong>${Math.round(st.wE * 100)}%</strong>`;
    // leaderboard top 12
    const lb = $("sim-board");
    if (lb) {
      const rowHtml = r => {
        const baseR = scores.countries.find(c => c.iso3 === r.iso3).rank;
        const dr = baseR - r.rank;
        const drTxt = dr === 0 ? `<span class="flat">&ndash;</span>` : dr > 0 ? `<span class="up">&uarr;${dr}</span>` : `<span class="down">&darr;${-dr}</span>`;
        const hi = r.iso3 === st.country ? ' class="sim-me"' : "";
        return `<tr${hi}><td class="num rank-cell">${r.rank}</td><td class="name-cell">${r.name} <span class="iso">${r.iso3}</span></td><td class="num">${fmt(r.sim)}</td><td class="num soft">${fmt(r.base)}</td><td class="num">${drTxt}</td></tr>`;
      };
      let html = field.slice(0, 12).map(rowHtml).join("");
      const meRow = byIso[st.country];
      if (meRow && meRow.rank > 12) html += `<tr class="sim-gap"><td colspan="5">&middot;&middot;&middot;</td></tr>` + rowHtml(meRow);
      lb.innerHTML = html;
    }
    if (SIM._reshade) SIM._reshade(byIso);
  }

  // Per-country domain what-if: a compact slider for each scored Recruitment/Exploitation domain
  // of the focus country. Moving one writes an override; the focus country's R/E are recomputed as
  // its domain means and the whole field re-ranks. Monetization domains are excluded (not in score).
  function simBuildDomainSliders() {
    const mount = document.getElementById("sim-domains");
    if (!mount) return;
    const d = SIM.domains && SIM.domains[SIM.state.country];
    if (!d) { mount.innerHTML = ""; return; }
    const groups = [["Recruitment", "Recruitment domains &rarr; R"], ["Exploitation", "Exploitation domains &rarr; E"]];
    let html = "";
    groups.forEach(([ph, head]) => {
      const keys = Object.keys(d).filter(k => d[k].phase === ph)
        .sort((a, b) => ((d[b].score != null ? d[b].score : -1) - (d[a].score != null ? d[a].score : -1)));
      if (!keys.length) return;
      html += `<div class="sim-dom-group"><p class="sim-dom-head-lbl">${head}</p>`;
      keys.forEach(k => {
        const dd = d[k];
        if (dd.scored !== true || dd.score == null) {
          html += `<div class="sim-dom sim-dom-na"><div class="sim-dom-top"><span>${dd.label}</span><span class="sim-dom-val">not scored</span></div></div>`;
          return;
        }
        const v = Math.max(0, Math.min(1, dd.score));
        html += `<div class="sim-dom"><div class="sim-dom-top"><span>${dd.label}</span><span class="sim-dom-val" id="domv-${k}">${fmt(v)}</span></div>` +
          `<input type="range" min="0" max="1" step="0.01" value="${v}" data-dom="${k}" data-base="${v}" aria-label="${dd.label} score for ${SIM.state.country}"></div>`;
      });
      html += `</div>`;
    });
    mount.innerHTML = html;
    mount.querySelectorAll("input[data-dom]").forEach(inp => {
      inp.addEventListener("input", () => {
        const k = inp.getAttribute("data-dom"), v = Number(inp.value), base = Number(inp.getAttribute("data-base"));
        if (Math.abs(v - base) < 0.005) delete SIM.state.domainOverrides[k];
        else SIM.state.domainOverrides[k] = v;
        const lab = document.getElementById("domv-" + k);
        if (lab) lab.innerHTML = Math.abs(v - base) < 0.005 ? fmt(v) : `${fmt(v)} <span class="sim-dom-was">(was ${fmt(base)})</span>`;
        simRender();
        simRadar(document.getElementById("sim-radar"));
      });
    });
  }

  // Draggable what-if radar for the simulation page. Shows the focus country's domains using
  // current overrides; dragging a Recruitment/Exploitation point along its spoke sets that domain's
  // override and recomputes the whole field live, in sync with the sliders. Monetization points are
  // static (the lens is not in the score). Built fresh on each country change / slider input.
  function simRadar(mountEl) {
    if (!mountEl) return;
    const recDoms = SIM.domains && SIM.domains[SIM.state.country];
    if (!recDoms) { mountEl.innerHTML = ""; return; }
    const order = [];
    PHASE_ORDER.forEach(ph => Object.keys(recDoms).forEach(slug => {
      if (recDoms[slug].phase === ph) order.push(Object.assign({ slug }, recDoms[slug]));
    }));
    const N = order.length;
    if (!N) { mountEl.innerHTML = ""; return; }
    const W = 600, H = 470, cx = 300, cy = 235, R = 150;
    const ang = i => (-90 + i * 360 / N) * Math.PI / 180;
    const pt = (i, rad) => [cx + Math.cos(ang(i)) * rad, cy + Math.sin(ang(i)) * rad];
    const f1 = n => n.toFixed(1);
    const ov = SIM.state.domainOverrides;
    const isScored = d => d.scored === true && d.score != null;
    const isLens = d => d.phase === "Monetization";
    const valOf = d => { if (!isScored(d)) return null; const v = ov[d.slug] != null ? ov[d.slug] : d.score; return Math.max(0, Math.min(1, v)); };
    const scoreIdx = order.map((d, i) => !isLens(d) ? i : -1).filter(i => i >= 0);
    const polyPoints = () => scoreIdx.map(i => { const v = valOf(order[i]); return pt(i, (v == null ? 0 : v) * R).map(f1).join(","); }).join(" ");
    const curColor = () => { const fr = simFocusRE(); const c = (fr && fr.R != null && fr.E != null) ? simCombine(fr.R, fr.E, SIM.state) : 0.4; return rampColor(c, 1.0); };

    let g = "";
    [0.25, 0.5, 0.75, 1].forEach(rr => { g += `<polygon class="radar-ring" fill="none" stroke="#e2dccd" points="${order.map((_, i) => pt(i, rr * R).map(f1).join(",")).join(" ")}"></polygon>`; });
    order.forEach((d, i) => {
      const lens = isLens(d);
      const [ex, ey] = pt(i, R);
      g += `<line class="radar-spoke${lens ? " radar-spoke-lens" : ""}" stroke="#e2dccd" x1="${cx}" y1="${cy}" x2="${f1(ex)}" y2="${f1(ey)}"></line>`;
      const [lx, ly] = pt(i, R + 13);
      const cosA = Math.cos(ang(i));
      const anchor = Math.abs(cosA) < 0.34 ? "middle" : (cosA > 0 ? "start" : "end");
      const words = d.label.split(" ");
      let lines = [d.label];
      if (d.label.length > 15 && words.length > 1) {
        let b = 1, bd = 1e9;
        for (let k = 1; k < words.length; k++) { const diff = Math.abs(words.slice(0, k).join(" ").length - words.slice(k).join(" ").length); if (diff < bd) { bd = diff; b = k; } }
        lines = [words.slice(0, b).join(" "), words.slice(b).join(" ")];
      }
      const LH = 10, first = -((lines.length - 1) / 2) * LH;
      const tsp = lines.map((ln, k) => `<tspan x="${f1(lx)}" dy="${k === 0 ? first : LH}">${ln}</tspan>`).join("");
      const has = GLOSSARY[d.slug];
      g += `<text class="radar-axis${lens ? " radar-axis-lens" : ""}${has ? " has-gloss" : ""}"${has ? ` data-gloss="${d.slug}" role="button" tabindex="0"` : ""} x="${f1(lx)}" y="${f1(ly)}" text-anchor="${anchor}" dominant-baseline="middle">${tsp}</text>`;
    });
    const col0 = curColor();
    g += `<polygon class="radar-area sim-radar-area" points="${polyPoints()}" style="fill:${col0};fill-opacity:0.32;stroke:${col0};stroke-width:1.5"></polygon>`;
    order.forEach((d, i) => {
      const v = valOf(d);
      const [vx, vy] = pt(i, (v == null ? 0 : v) * R);
      if (!isScored(d)) g += `<circle class="radar-dot radar-dot-na" fill="#f4efe5" stroke="#a89f8d" stroke-width="1" cx="${cx}" cy="${cy}" r="3.1"><title>${d.label}: not scored</title></circle>`;
      else if (isLens(d)) g += `<circle class="radar-dot radar-dot-lens" fill="#f4efe5" stroke="#1a1814" stroke-width="1.2" cx="${f1(vx)}" cy="${f1(vy)}" r="3.1"><title>${d.label}: ${fmt(v)} (lens, not in score)</title></circle>`;
      else g += `<circle class="sim-rh" data-i="${i}" data-slug="${d.slug}" cx="${f1(vx)}" cy="${f1(vy)}" r="6.5" fill="#1a1814" stroke="#fff" stroke-width="1.5"><title>${d.label}: ${fmt(v)} (drag to change)</title></circle>`;
    });
    g += `<text class="radar-scale" x="${cx + 3}" y="${f1(cy - 0.5 * R)}">0.5</text><text class="radar-scale" x="${cx + 3}" y="${f1(cy - R + 3)}">1.0</text>`;
    mountEl.innerHTML = `<div class="radar-wrap sim-radar-wrap"><svg viewBox="0 0 ${W} ${H}" class="radar-svg" role="img" aria-label="Draggable radar of the focus country's domains">${g}</svg></div><p class="muted sim-radar-cap"><small>Drag any solid point to change that domain. R, E, the score, the rank and the map move with you. The two italic points are the Monetization lens (not in the score).</small></p>`;

    // ---- dragging (pointer events: mouse + touch) ----
    const svg = mountEl.querySelector("svg");
    const area = svg.querySelector(".sim-radar-area");
    let dragging = null, raf = 0;
    const toVal = (i, clientX, clientY) => {
      const r = svg.getBoundingClientRect();
      const sx = (clientX - r.left) * (W / r.width), sy = (clientY - r.top) * (H / r.height);
      const a = ang(i);
      let rad = (sx - cx) * Math.cos(a) + (sy - cy) * Math.sin(a);
      return Math.max(0, Math.min(R, rad)) / R;
    };
    const onMove = ev => {
      if (!dragging) return;
      ev.preventDefault();
      const v = toVal(dragging.i, ev.clientX, ev.clientY);
      ov[dragging.slug] = v;
      const [x, y] = pt(dragging.i, v * R);
      dragging.dot.setAttribute("cx", f1(x)); dragging.dot.setAttribute("cy", f1(y));
      area.setAttribute("points", polyPoints());
      const c = curColor(); area.style.fill = c; area.style.stroke = c;
      const sl = document.querySelector(`#sim-domains input[data-dom="${dragging.slug}"]`);
      if (sl) { sl.value = v; const lab = document.getElementById("domv-" + dragging.slug); const base = Number(sl.getAttribute("data-base")); if (lab) lab.innerHTML = Math.abs(v - base) < 0.005 ? fmt(v) : `${fmt(v)} <span class="sim-dom-was">(was ${fmt(base)})</span>`; }
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; simRender(); });
    };
    const onUp = () => {
      if (!dragging) return;
      window.removeEventListener("pointermove", onMove); window.removeEventListener("pointerup", onUp);
      const slug = dragging.slug; dragging = null;
      const d = recDoms[slug]; if (d && Math.abs((ov[slug] != null ? ov[slug] : d.score) - d.score) < 0.005) delete ov[slug];
      simRender();
    };
    svg.querySelectorAll(".sim-rh").forEach(dot => {
      dot.addEventListener("pointerdown", ev => {
        ev.preventDefault();
        dragging = { i: Number(dot.getAttribute("data-i")), slug: dot.getAttribute("data-slug"), dot };
        window.addEventListener("pointermove", onMove); window.addEventListener("pointerup", onUp);
      });
    });
  }

  function simBindControls(scores) {
    const st = SIM.state; SIM.scores = scores;
    const $ = id => document.getElementById(id);
    const sel = $("sim-country");
    if (sel) {
      const list = scores.countries.filter(c => c.scored).sort((a, b) => a.name.localeCompare(b.name));
      sel.innerHTML = list.map(c => `<option value="${c.iso3}">${c.name}</option>`).join("");
      const init = (location.hash || "").replace("#", "").toUpperCase();
      st.country = (init && scores.countries.some(c => c.iso3 === init && c.scored)) ? init : list[0].iso3;
      sel.value = st.country;
      sel.addEventListener("change", () => { st.country = sel.value; st.domainOverrides = {}; history.replaceState(null, "", "#" + sel.value); simBuildDomainSliders(); simRadar(document.getElementById("sim-radar")); simRender(); });
    }
    const ws = $("sim-weight");
    if (ws) { ws.value = 50; ws.addEventListener("input", () => { st.wE = Number(ws.value) / 100; simRender(); }); }
    document.querySelectorAll("[data-sim-op]").forEach(btn => btn.addEventListener("click", () => {
      st.operator = btn.getAttribute("data-sim-op");
      document.querySelectorAll("[data-sim-op]").forEach(b => b.setAttribute("aria-pressed", b === btn ? "true" : "false"));
      simRender();
    }));
    const gov = $("sim-gov");
    if (gov) gov.addEventListener("change", () => { st.govStrip = gov.checked; simRender(); });
    const rst = $("sim-reset");
    if (rst) rst.addEventListener("click", () => {
      st.wE = 0.5; st.operator = "geometric"; st.govStrip = false; st.domainOverrides = {};
      if (ws) ws.value = 50; if (gov) gov.checked = false;
      document.querySelectorAll("[data-sim-op]").forEach(b => b.setAttribute("aria-pressed", b.getAttribute("data-sim-op") === "geometric" ? "true" : "false"));
      simBuildDomainSliders();
      simRadar(document.getElementById("sim-radar"));
      simRender();
    });
  }

  // Simulation-aware map: same geometry/aliasing as compositeMap, plus a reshade(byIso) hook.
  function simMap(elId, onReady) {
    const el = document.getElementById(elId);
    if (!el || typeof L === "undefined") { if (onReady) onReady({}); return; }
    const map = L.map(elId, {
      scrollWheelZoom: false, doubleClickZoom: true,
      minZoom: 1, maxZoom: 6,
      maxBounds: [[-60, -180], [85, 180]], maxBoundsViscosity: 1.0,
    }).setView([18, 12], 1.6);
    // CARTO light, label-free basemap — match the composite map; no multilingual clutter.
    // noWrap keeps a single world (no repeated continents across the antimeridian).
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; OpenStreetMap, &copy; CARTO', subdomains: "abcd", maxZoom: 19, noWrap: true,
    }).addTo(map);
    const tip = makeTip(el);
    Promise.all([
      loadScores(),
      fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json").then(r => r.json()),
    ]).then(([scores, atlas]) => {
      const byName = {}; scores.countries.forEach(c => { byName[c.name.toLowerCase()] = c; });
      const geo = splitAntimeridian(topojson.feature(atlas, atlas.objects.countries));
      const recFor = f => {
        const nm = (f.properties && f.properties.name || "").toLowerCase();
        return byName[nm] || (ATLAS_ALIAS[nm] ? byName[ATLAS_ALIAS[nm]] : null);
      };
      let simByIso = {};
      const layer = L.geoJSON(geo, {
        style: f => {
          const rec = recFor(f);
          const v = rec && rec.scored ? (simByIso[rec.iso3] ? simByIso[rec.iso3].sim : rec.composite) : null;
          return { color: "#888", weight: 0.4, fillColor: rampColor(v, 1.0), fillOpacity: v == null ? 0.0 : 0.88 };
        },
        onEachFeature: (f, lyr) => {
          const rec = recFor(f);
          lyr.on("mousemove", e => {
            let html;
            if (rec && rec.scored) {
              const s = simByIso[rec.iso3];
              html = `<b>${rec.name}</b><br>Simulated <span class="v">${fmt(s ? s.sim : rec.composite)}</span> &middot; published <span class="v">${fmt(rec.composite)}</span><br>Rank ${s ? s.rank : rec.rank}`;
            } else html = `<b>${f.properties.name}</b><br><span class="muted">Not scored</span>`;
            tip.show(html, e.originalEvent.clientX, e.originalEvent.clientY);
            lyr.setStyle({ weight: 1.2, color: "#222" });
          });
          lyr.on("mouseout", () => { tip.hide(); layer.resetStyle(lyr); });
        },
      }).addTo(map);
      SIM._reshade = byIso => { simByIso = byIso || {}; layer.setStyle(layer.options.style); };
      if (onReady) onReady({ scores });
    }).catch(err => { el.innerHTML = `<div class="slot-empty"><h3>Map could not load</h3><p>${err}</p></div>`; if (onReady) onReady({}); });
  }

  // Public entry: wire the whole simulation page (map + controls + readout + leaderboard).
  function initSimulation(mapElId) {
    Promise.all([loadScores(), loadDomains()]).then(([scores, domains]) => {
      SIM.domains = domains;
      simMap(mapElId, () => { simBindControls(scores); simBuildDomainSliders(); simRadar(document.getElementById("sim-radar")); simRender(); });
    });
  }
  // Backward-compatible alias: an older/simpler simulate.html may call buildSimulator(); route it
  // to the same engine so nothing breaks. If a map element exists it is wired too.
  function buildSimulator() {
    return Promise.all([loadScores(), loadDomains()]).then(([scores, domains]) => {
      SIM.domains = domains;
      if (document.getElementById("sim-map")) { simMap("sim-map", () => { simBindControls(scores); simBuildDomainSliders(); simRadar(document.getElementById("sim-radar")); simRender(); }); }
      else { simBindControls(scores); simBuildDomainSliders(); simRadar(document.getElementById("sim-radar")); simRender(); }
      return scores;
    });
  }

  // ================= POINTS OF INTERVENTION (per-country read) =================
  // Honest framing: this reads a country's REAL R and E (data/scores.json) and reports WHERE
  // structural risk concentrates — which side (vulnerability vs. unchecked exploitation) carries
  // the score, and therefore which family of levers is most structurally relevant. It does NOT
  // predict the effect of any intervention; it locates candidate intervention points.
  function buildInterventionReadout(selectId, mountId) {
    // loads scores (R/E/composite) AND domains.json, so the per-country card can show the
    // two Monetization-lens scores (concealment, cash retention) as a disruptor read.
    return Promise.all([loadScores(), loadDomains()]).then(([scores, domains]) => {
      const sel = document.getElementById(selectId);
      const mount = document.getElementById(mountId);
      if (!sel || !mount) return scores;
      const scored = scores.countries.filter(c => c.scored).sort((a, b) => a.name.localeCompare(b.name));
      sel.innerHTML = `<option value="">Select a country…</option>` +
        scored.map(c => `<option value="${c.iso3}">${c.name}</option>`).join("");

      // ---- Monetization (Disruptor) lens: per-country read + global highlight ----
      const MON_A = "domain-a-transnational-concealment", MON_B = "domain-b-cash-informal-retention";
      const monScore = (iso, slug) => {
        const d = domains[iso] && domains[iso][slug];
        return (d && d.scored && d.score != null) ? d.score : null;
      };
      const pct = v => Math.min(100, Math.max(0, (v || 0) * 100)).toFixed(0);
      function disruptorBlock(rec) {
        const cz = monScore(rec.iso3, MON_A), cr = monScore(rec.iso3, MON_B);
        if (cz == null && cr == null) return "";
        const bar = (lbl, v) => v == null
          ? `<div class="iv-dz-row iv-dz-na"><span class="iv-dz-lbl">${lbl}</span><span class="iv-dz-track"></span><span class="iv-dz-val">not scored</span></div>`
          : `<div class="iv-dz-row"><span class="iv-dz-lbl">${lbl}</span><span class="iv-dz-track"><span class="iv-dz-fill" style="width:${pct(v)}%"></span></span><span class="iv-dz-val">${fmt(v)}</span></div>`;
        const hi = 0.66, lo = 0.45;
        let read;
        if (cz != null && cr != null) {
          if (cz >= hi && cr >= hi) read = "Both financial levers are strong here: proceeds can be moved across borders and kept off the books, so beneficial-ownership transparency, follow-the-money enforcement, and financial inclusion all have the most to act on.";
          else if (cz - cr > 0.12) read = "The cross-border concealment lever is the stronger of the two here; beneficial-ownership transparency and follow-the-money enforcement are where disruption has the most to act on.";
          else if (cr - cz > 0.12) read = "The cash and informal-retention lever is the stronger of the two here; financial inclusion and informal-sector formalization are where disruption has the most to act on.";
          else if (cz < lo && cr < lo) read = "Neither financial lever stands out: by these measures, proceeds are comparatively harder to monetize through cross-border concealment or cash retention.";
          else read = "Both financial levers register at a moderate level here; concealment and cash retention each offer a partial point of disruption.";
        } else if (cz != null) {
          read = `Only the cross-border concealment lens is scored for ${rec.name}. ` + (cz >= hi ? "It reads high: proceeds are comparatively easy to move and hide, so beneficial-ownership transparency and follow-the-money enforcement have the most to act on." : "It reads moderate by this measure.");
        } else {
          read = `Only the cash and informal-retention lens is scored for ${rec.name}. ` + (cr >= hi ? "It reads high: proceeds can readily stay off the books, so financial inclusion and informal-sector formalization have the most to act on." : "It reads moderate by this measure.");
        }
        return `
            <div class="iv-disruptor-read">
              <span class="role role-def">Disruptor lens</span>
              <h4>Where the financial levers are strongest</h4>
              <div class="iv-dz">
                ${bar("Transnational concealment", cz)}
                ${bar("Cash &amp; informal retention", cr)}
              </div>
              <p style="margin:8px 0 0">${read}</p>
              <p class="muted" style="margin:6px 0 0"><small>Monetization-lens scores (0&ndash;1): where proceeds are easiest to hide and retain. They mark disruption points and are deliberately excluded from the published risk score. See the <a href="#iv-monetization">Disruptor levers</a> below.</small></p>
            </div>`;
      }

      // global highlight: where the two financial levers are strongest together
      const topMount = document.getElementById("iv-disruptor-top");
      if (topMount) {
        const rows = scores.countries
          .map(c => ({ iso: c.iso3, name: c.name, cz: monScore(c.iso3, MON_A), cr: monScore(c.iso3, MON_B) }))
          .filter(r => r.cz != null && r.cr != null)
          .map(r => Object.assign(r, { mean: (r.cz + r.cr) / 2 }))
          .sort((a, b) => b.mean - a.mean);
        const top = rows.slice(0, 6);
        topMount.innerHTML = `
          <h4>Where these levers bite hardest</h4>
          <p class="muted" style="margin:0 0 8px"><small>The countries where the two financial levers are strongest together (the mean of the two lens scores), among the ${rows.length} scored on both. A high reading means proceeds are comparatively easy to move, hide, and retain, so the disruptor levers above have the most to act on. Read as a separate lens; it does not enter the risk score.</small></p>
          <ol class="iv-dz-rank">
            ${top.map(r => `<li><a href="${pageHref("profiles.html")}#${r.iso}">${r.name}</a><span class="iv-dz-cmp">concealment ${fmt(r.cz)} &middot; cash ${fmt(r.cr)}</span><span class="iv-dz-mean">${fmt(r.mean)}</span></li>`).join("")}
          </ol>`;
      }

      function render(rec) {
        if (!rec) { mount.innerHTML = ""; return; }
        const tier = tierOf(rec.composite);
        const tierLbl = { high: "Higher", mid: "Middle", low: "Lower" }[tier];
        // which side carries the score -> which lever family is most structurally relevant
        const gap = (rec.E || 0) - (rec.R || 0);
        let where, focus, focusClass;
        if (gap > 0.06) {
          where = `In ${rec.name}, the score is carried more by the <strong>conditions that let exploitation run unchecked (E&nbsp;=&nbsp;${fmt(rec.E)})</strong> than by vulnerability to recruitment (R&nbsp;=&nbsp;${fmt(rec.R)}).`;
          focus = "Enforcement, sectoral oversight, and exit-route levers (the Exploitation phase) are the most structurally relevant places to look first.";
          focusClass = "role-driver";
        } else if (gap < -0.06) {
          where = `In ${rec.name}, the score is carried more by <strong>vulnerability to recruitment (R&nbsp;=&nbsp;${fmt(rec.R)})</strong> than by the conditions for unchecked exploitation (E&nbsp;=&nbsp;${fmt(rec.E)}).`;
          focus = "Protection, status, and shock-buffer levers (the Recruitment phase) are the most structurally relevant places to look first.";
          focusClass = "role-driver";
        } else {
          where = `In ${rec.name}, the two sides are <strong>roughly balanced</strong> (R&nbsp;=&nbsp;${fmt(rec.R)}, E&nbsp;=&nbsp;${fmt(rec.E)}).`;
          focus = "Both the recruitment-side and exploitation-side levers are structurally relevant; neither dominates.";
          focusClass = "role-driver";
        }
        const tierColor = { high: "#f7e0d2;color:#8c2d19", mid: "#fbeed8;color:#8a6400", low: "#e7eee4;color:#4a6440" }[tier];
        mount.innerHTML = `
          <div class="iv-card">
            <div class="iv-lead">
              <span class="iv-priority" style="background:${tierColor}">${tierLbl}-tier structural risk</span>
              <h3 style="margin-top:8px">Where this concentrates in ${rec.name}</h3>
              <p style="margin:0">${where}</p>
            </div>
            <div class="iv-where">
              <div class="cell">
                <span class="lbl">Recruitment side (R)</span>
                <b>${fmt(rec.R)}</b>
                <span class="sub">who is made vulnerable: the levers below under <em>Recruitment</em></span>
              </div>
              <div class="cell">
                <span class="lbl">Exploitation side (E)</span>
                <b>${fmt(rec.E)}</b>
                <span class="sub">whether exploitation runs unchecked: the levers below under <em>Exploitation</em></span>
              </div>
            </div>
            <div class="iv-lead" style="border-top:1px solid var(--line)">
              <p style="margin:0 0 6px"><strong>Most structurally relevant levers:</strong> ${focus}</p>
              <p class="muted" style="margin:0"><small>This locates where the structure is most sensitive. It is not a prediction that acting there will reduce real-world forced labor, only that this is where the index says the conditions concentrate. Read it alongside the country&rsquo;s <a href="${pageHref("profiles.html")}#${rec.iso3}">full profile</a>.</small></p>
            </div>
            ${disruptorBlock(rec)}
          </div>`;
      }

      const show = iso => {
        const rec = scores.countries.find(c => c.iso3 === iso);
        if (rec && rec.scored) { sel.value = iso; render(rec); }
      };
      sel.addEventListener("change", () => { if (sel.value) { history.replaceState(null, "", "#" + sel.value); show(sel.value); } });
      window.addEventListener("hashchange", () => {
        const iso = (location.hash || "").replace("#", "").toUpperCase();
        if (iso) show(iso);
      });
      const initial = (location.hash || "").replace("#", "").toUpperCase();
      if (initial) show(initial);
      return scores;
    });
  }

  // ================= EXPLORE — full-bleed "map IS the page" =================
  // A single Leaflet map on the CARTO light_nolabels basemap that switches between five
  // analytical layers, matching the project's reference overlay
  // (experiments/geospatial/flsri-geospatial-overlay.html). The page scrolls (scrollWheelZoom
  // is off; zoom is via the +/- control); a single non-repeating world (noWrap). Every layer
  // is built from real data files in data/; nothing is fabricated, and no-data is grey/absent,
  // never zero. Layers:
  //   composite   — country choropleth from data/scores.json over world-atlas geometry
  //   subnational — admin-1 risk surface from data/admin1_risk.topojson (prop "risk")
  //   corridors   — labor-export pressure (remittance dependence) from data/overlay.json
  //   lisa        — admin-1 LISA clusters (HH/LL/HL/LH) from data/lisa_admin1.json joined to
  //                 data/admin1_risk.topojson by properties.id, on a LIGHT base
  const RAMP_HEX = RAMP; // alias for legend builders
  const LISA_COL = { "High-High": "#b2182b", "Low-Low": "#2166ac", "High-Low": "#f4a582", "Low-High": "#92c5de" };

  function exploreMap(elId, opts = {}) {
    const el = document.getElementById(elId);
    if (!el || typeof L === "undefined") return;
    const $ = id => (opts[id] && document.getElementById(opts[id])) || null;
    const legendEl = $("legendEl"), legendNoteEl = $("legendNoteEl"),
          countEl = $("countEl"), hoverEl = $("hoverEl"), auxEl = $("auxEl"),
          pageEl = (opts.pageEl && document.getElementById(opts.pageEl)) || el.closest(".map-page");
    const layersEl = (opts.layersEl && document.getElementById(opts.layersEl)) || null;

    const map = L.map(elId, {
      scrollWheelZoom: false, doubleClickZoom: true, zoomControl: true,
      minZoom: 1, maxZoom: 8, worldCopyJump: false,
      maxBounds: [[-62, -180], [85, 180]], maxBoundsViscosity: 1.0,
    }).setView([20, 14], 2);
    // CARTO light, label-free basemap — the reference's clean muted base. noWrap = single world.
    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; OpenStreetMap, &copy; CARTO', subdomains: "abcd", maxZoom: 19, noWrap: true,
    }).addTo(map);

    // fade the top-left title card while a popup is open, so it never collides with one
    // (auto-pan can't always clear it at min zoom because the map bounds are locked).
    map.on("popupopen", () => { if (pageEl) pageEl.classList.add("popup-open"); });
    map.on("popupclose", () => { if (pageEl) pageEl.classList.remove("popup-open"); });

    // floating hover readout (follows cursor)
    function hover(html, e) {
      if (!hoverEl) return;
      if (!html) { hoverEl.hidden = true; return; }
      hoverEl.hidden = false; hoverEl.innerHTML = html;
      const r = el.getBoundingClientRect();
      let x = e.originalEvent.clientX - r.left + 16, y = e.originalEvent.clientY - r.top + 16;
      if (x + 250 > r.width) x = e.originalEvent.clientX - r.left - 250;
      if (y + 120 > r.height) y = e.originalEvent.clientY - r.top - 120;
      hoverEl.style.left = x + "px"; hoverEl.style.top = y + "px";
    }
    const setCount = t => { if (countEl) countEl.innerHTML = t || ""; };
    const setAux = t => { if (auxEl) auxEl.innerHTML = t || ""; };
    const rampLegend = (label, max) => {
      if (!legendEl) return;
      legendEl.innerHTML =
        `<span><span class="ramp" aria-hidden="true">${RAMP_HEX.map(c => `<span style="background:${c}"></span>`).join("")}</span></span>` +
        `<span>${label}</span>` +
        `<span style="margin-left:auto"><span class="swatch" style="background:${NODATA}"></span>No data</span>`;
    };
    // simple click popup for the secondary layers (subnational, corridors, clusters)
    function simplePopup(latlng, html) {
      L.popup(Object.assign({ className: "peek-popup peek-min", maxWidth: 244, minWidth: 168, closeButton: true }, popupPan(map)))
        .setLatLng(latlng).setContent(html).openOn(map);
    }

    // ---- layer registry: each builds (lazily) a Leaflet layer + sets legend/count text ----
    const built = {};      // name -> Leaflet layer
    let current = null;    // name of active layer
    let _atlas = null, _scores = null, _overlay = null, _lisa = null;

    function getAtlas() {
      return _atlas ? Promise.resolve(_atlas) :
        fetch("https://cdn.jsdelivr.net/npm/world-atlas@2/countries-50m.json").then(r => r.json()).then(a => (_atlas = a));
    }
    // overlay.json carries bare NaN tokens (comp for unscored countries), which Python writes
    // but browser JSON.parse rejects. Load as text and neutralise bare NaN/Infinity -> null so
    // the hotspots + corridors layers can read it without touching the upstream data file.
    function fetchJsonLoose(path) {
      return fetch(rel(path)).then(r => r.text()).then(t =>
        JSON.parse(t.replace(/\b-?(?:NaN|Infinity)\b/g, "null")));
    }
    function getOverlay() {
      return _overlay ? Promise.resolve(_overlay) :
        fetchJsonLoose("data/overlay.json").then(d => (_overlay = d));
    }
    function getLisaAdmin1() {
      return _lisa ? Promise.resolve(_lisa) :
        fetch(rel("data/lisa_admin1.json")).then(r => r.json()).then(d => (_lisa = d));
    }

    // --- COMPOSITE ---
    function buildComposite() {
      return Promise.all([loadScores(), getAtlas()]).then(([scores, atlas]) => {
        _scores = scores;
        const byName = {}; scores.countries.forEach(c => { byName[c.name.toLowerCase()] = c; });
        const recFor = f => {
          const nm = (f.properties && f.properties.name || "").toLowerCase();
          return byName[nm] || (ATLAS_ALIAS[nm] ? byName[ATLAS_ALIAS[nm]] : null);
        };
        const geo = splitAntimeridian(topojson.feature(atlas, atlas.objects.countries));
        const lyr = L.geoJSON(geo, {
          style: f => {
            const rec = recFor(f);
            const v = rec && rec.scored ? rec.composite : null;
            return { color: "#888", weight: 0.4, fillColor: rampColor(v, 1.0), fillOpacity: v == null ? 0.0 : 0.88 };
          },
          onEachFeature: (f, l) => {
            const rec = recFor(f);
            l.on("mousemove", e => {
              let html;
              if (rec && rec.scored) {
                const b = bandText(rec);
                html = `<b>${rec.name}</b><br>Structural risk <span class="v">${fmt(rec.composite)}</span><br>Rank ${rec.rank}${b ? ` <span class="muted">(band ${b})</span>` : ""} of ${scores.meta.n_scored}` +
                  (rec.low_confidence ? `<br><span class="muted">Lower-confidence score (${(rec.n_domains || 11) - rec.domains_not_scored} of ${rec.n_domains || 11} domains)</span>` : "");
              } else html = `<b>${f.properties.name}</b><br><span class="muted">Not scored: the data are too thin for a fair comparison</span>`;
              hover(html, e); l.setStyle({ weight: 1.2, color: "#222" });
            });
            l.on("mouseout", () => { hover(null); lyr.resetStyle(l); });
            l.on("click", e => { if (rec && rec.scored) { L.DomEvent.stopPropagation(e); openCountryPeek(rec, e.latlng, map, scores.meta, opts.onCountryClick); } });
          },
        });
        lyr._onShow = () => {
          rampLegend("Less &rarr; more of the conditions that enable forced labor", 1.0);
          if (legendNoteEl) legendNoteEl.innerHTML = "1.0 is a theoretical worst; the highest real score is near " + fmt(_scores.meta.composite_max) + ". Click a country for a quick look.";
          setCount(_scores.meta.n_scored + " of " + _scores.meta.n_universe + " countries scored");
          setAux("The published national score. Click a country for a quick look.");
        };
        return lyr;
      });
    }

    // --- SUBNATIONAL ---
    function buildSubnational() {
      return Promise.all([
        fetch(rel("data/admin1_risk.topojson")).then(r => r.json()),
        getAtlas(),
      ]).then(([topo, atlas]) => {
        const obj = topo.objects[Object.keys(topo.objects)[0]];
        const geo = splitAntimeridian(topojson.feature(topo, obj));
        let lo = 1, hi = 0, n = 0;
        const covered = new Set();
        geo.features.forEach(f => { const v = f.properties.risk, cc = (f.properties.cntry || "").toLowerCase(); if (cc) covered.add(cc); if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); n++; } });
        const lyr = L.layerGroup();
        // admin-1 risk surface (original colouring; no-data regions pale, no grey world fill)
        const surf = L.geoJSON(geo, {
          style: f => {
            const v = f.properties.risk;
            if (v == null) return { color: "#bbb", weight: 0.2, fillColor: "#d9d4c8", fillOpacity: 0.35 };
            return { color: "#fff", weight: 0.25, fillColor: rampStretch(v, lo, hi), fillOpacity: 0.9 };
          },
          onEachFeature: (f, l) => {
            const p = f.properties;
            l.on("mousemove", e => {
              let html = `<b>${p.name || p.cntry}</b><br><span class="muted">${p.cntry}</span>`;
              html += p.risk != null ? `<br>Local risk surface <span class="v">${fmt(p.risk)}</span>` : `<br><span class="muted">Mapped here, but the local census sample is too thin for a reliable estimate</span>`;
              hover(html, e); l.setStyle({ weight: 1.0, color: "#222" }); l.bringToFront();
            });
            l.on("mouseout", () => { hover(null); surf.resetStyle(l); });
            l.on("click", e => {
              L.DomEvent.stopPropagation(e);
              const body = p.risk != null
                ? `<div class="pk-row">Local risk surface <span class="v">${fmt(p.risk)}</span></div>`
                : `<div class="pk-note">Mapped here, but the local census sample is too thin for a reliable estimate.</div>`;
              simplePopup(e.latlng, `<div class="pk-t">${p.name || p.cntry}</div><div class="pk-sub">${p.cntry || ""}</div>${body}`);
            });
          },
        });
        // no-data countries: same light base as before (no fill), but HOVER reveals "no data"
        // so a blank country reads as not-measured rather than low-risk. Each country once.
        const world = splitAntimeridian(topojson.feature(atlas, atlas.objects.countries));
        const seen = new Set();
        const ndFeatures = world.features.filter(f => {
          const nm = (f.properties.name || "").toLowerCase();
          if (!nm || seen.has(nm)) return false;
          seen.add(nm);
          return !(covered.has(nm) || (ATLAS_ALIAS[nm] && covered.has(ATLAS_ALIAS[nm])));
        });
        const ndLayer = L.geoJSON({ type: "FeatureCollection", features: ndFeatures }, {
          style: () => ({ color: "#cfcabd", weight: 0.2, fillColor: "#ddd7c9", fillOpacity: 0 }),
          onEachFeature: (f, l) => {
            l.on("mousemove", e => {
              hover(`<b>${f.properties.name}</b><br><span class="muted">No data: not represented in this layer (not low-risk)</span>`, e);
              l.setStyle({ fillOpacity: 0.4, weight: 0.7, color: "#999" }); l.bringToFront();
            });
            l.on("mouseout", () => { hover(null); ndLayer.resetStyle(l); });
            l.on("click", e => {
              L.DomEvent.stopPropagation(e);
              simplePopup(e.latlng, `<div class="pk-t">${f.properties.name}</div><div class="pk-note">No subnational data here. This country is not represented in the admin-1 surface, which is not the same as low risk.</div>`);
            });
          },
        });
        lyr.addLayer(ndLayer);
        lyr.addLayer(surf);
        lyr._onShow = () => {
          rampLegend("Lower &rarr; higher local risk (range-stretched)", hi);
          if (legendNoteEl) legendNoteEl.innerHTML = "Admin-1 surface where IPUMS census data allow (" + covered.size + " countries). Hover a country with no surface to confirm &ldquo;no data&rdquo;: not represented, not low-risk.";
          setCount(n.toLocaleString() + " regions &middot; " + covered.size + " countries covered");
          setAux("Risk inside countries, where census data allow.");
        };
        return lyr;
      });
    }

    // --- LABOR-EXPORT PRESSURE (remittance dependence; origin economies) ---
    // The overlay carries an "inbound" migrant-share proxy too, but it did not capture real
    // destination demand (it flagged origin countries), so only the meaningful origin-side
    // signal is shown: how strongly an economy relies on labor sent abroad (remittances %GDP).
    function buildMonetization() {
      // Monetization lens overlay: the two Product-2 domains (transnational
      // concealment, cash & informal retention). Computed by the pipeline,
      // deliberately EXCLUDED from the composite — shown here as its own layer
      // so the financial architecture is visible next to the risk map.
      return Promise.all([getOverlay(), loadDomains()]).then(([ov, domains]) => {
        const lyr = L.layerGroup();
        const MA = "domain-a-transnational-concealment", MB = "domain-b-cash-informal-retention";
        const ramp = v => v >= 0.66 ? "#a8320a" : v >= 0.45 ? "#c98a00" : "#3a7d44";
        const rows = [];
        (ov.corridor || []).forEach(c => {
          if (!c.cent) return;
          const d = domains[c.iso3]; if (!d) return;
          const a = d[MA] && d[MA].scored ? d[MA].score : null;
          const b = d[MB] && d[MB].scored ? d[MB].score : null;
          if (a == null && b == null) return;
          const lens = a != null && b != null ? (a + b) / 2 : (a != null ? a : b);
          rows.push({ c, a, b, lens });
        });
        rows.sort((x, y) => y.lens - x.lens);
        const label = new Set(rows.slice(0, 12).map(r => r.c.iso3));
        const fmt2 = v => v == null ? "not scored" : fmt(v);
        rows.forEach(({ c, a, b, lens }) => {
          const rad = Math.max(4, Math.min(16, 3 + lens * 14));
          const m = L.circleMarker([c.cent[0], c.cent[1]], {
            radius: rad, fillColor: ramp(lens), color: "#fff", weight: 1, fillOpacity: 0.8,
          });
          const tip = `<b>${c.name}</b><br>Monetization lens: <span class="v">${fmt(lens)}</span><br>Concealment ${fmt2(a)} &middot; Retention ${fmt2(b)}<br><span class="muted">Not part of the composite score</span>`;
          m.on("mousemove", e => { hover(tip, e); m.setStyle({ weight: 2, fillOpacity: 0.95 }); m.bringToFront(); });
          m.on("mouseout", () => { hover(null); m.setStyle({ weight: 1, fillOpacity: 0.8 }); });
          m.on("click", e => {
            L.DomEvent.stopPropagation(e);
            simplePopup(e.latlng, `<div class="pk-t">${c.name}</div><div class="pk-sub">Monetization lens (not in the score)</div><div class="pk-row">Transnational concealment <span class="v">${fmt2(a)}</span></div><div class="pk-row">Cash &amp; informal retention <span class="v">${fmt2(b)}</span></div><div class="pk-row"><span class="muted">Where proceeds could be hidden and kept — the disruption read, deliberately excluded from the published risk score.</span></div>`);
          });
          if (label.has(c.iso3)) m.bindTooltip(c.name, { permanent: true, direction: "right", offset: [rad - 2, 0], className: "corridor-label" });
          lyr.addLayer(m);
        });
        lyr._onShow = () => {
          if (legendEl) legendEl.innerHTML =
            `<span><span class="swatch" style="background:#3a7d44"></span>lower lens score</span>` +
            `<span><span class="swatch" style="background:#c98a00"></span>moderate</span>` +
            `<span><span class="swatch" style="background:#a8320a"></span>higher &mdash; proceeds easiest to conceal &amp; retain</span>`;
          if (legendNoteEl) legendNoteEl.innerHTML = "The financial architecture lens: how readily the proceeds of exploitation could be moved across borders (concealment) and kept off the books (retention). Computed by the same pipeline, <b>deliberately excluded from the composite score</b> to avoid double-counting governance; shown here so the money side is visible beside the risk side.";
          setCount(rows.length + " countries with a lens score");
          setAux("The disruption read: where follow-the-money interventions have the most to act on.");
        };
        return lyr;
      });
    }

    function buildCorridors() {
      return getOverlay().then(ov => {
        const lyr = L.layerGroup();
        // structural-risk line: many corridor economies are not in the scored composite, so
        // explain the blank rather than printing a bare dash.
        const riskLine = comp => comp != null
          ? `Structural risk: <span class="v">${fmt(comp)}</span>`
          : `<span class="muted">Not scored in the composite: this country is missing data the national score needs.</span>`;
        // ORIGIN side: remittance INFLOW dependence (%GDP, normalized) — labor-exporting economies
        const inRows = ov.corridor
          .filter(c => c.cent && c.out != null && c.out >= 0.15)
          .sort((a, b) => (b.out || 0) - (a.out || 0));
        const inLabel = new Set(inRows.slice(0, 12).map(c => c.iso3));
        inRows.forEach(c => {
          const rad = Math.max(6, Math.min(18, 6 + c.out * 12));
          const m = L.circleMarker([c.cent[0], c.cent[1]], {
            radius: rad, fillColor: "#2166ac", color: "#fff", weight: 1, fillOpacity: 0.8,
          });
          const tip = `<b>${c.name}</b><br>Origin: labor-export pressure<br>Remittances received: <span class="v">${c.out}</span> (%GDP, normalized)<br>${riskLine(c.comp)}`;
          m.on("mousemove", e => { hover(tip, e); m.setRadius(rad + 2); m.setStyle({ weight: 2, fillOpacity: 0.95 }); m.bringToFront(); });
          m.on("mouseout", () => { hover(null); m.setRadius(rad); m.setStyle({ weight: 1, fillOpacity: 0.8 }); });
          m.on("click", e => {
            L.DomEvent.stopPropagation(e);
            simplePopup(e.latlng, `<div class="pk-t">${c.name}</div><div class="pk-sub">Origin: labor-export pressure</div><div class="pk-row">Remittances received <span class="v">${c.out}</span> <span class="muted">(%GDP, normalized)</span></div><div class="pk-row">${riskLine(c.comp)}</div>`);
          });
          if (inLabel.has(c.iso3)) m.bindTooltip(c.name, { permanent: true, direction: "right", offset: [rad - 2, 0], className: "corridor-label corridor-src" });
          lyr.addLayer(m);
        });
        // DEMAND side: remittance OUTFLOWS (%GDP) — migrant-destination economies (Gulf, etc.)
        const outRows = ov.corridor
          .filter(c => c.cent && c.rout != null && c.rout >= 1.5)
          .sort((a, b) => (b.rout || 0) - (a.rout || 0));
        const outLabel = new Set(outRows.slice(0, 12).map(c => c.iso3));
        outRows.forEach(c => {
          const rad = Math.max(5, Math.min(16, 4.5 + Math.sqrt(c.rout) * 2.4));
          const m = L.circleMarker([c.cent[0], c.cent[1]], {
            radius: rad, fillColor: "#c0392b", color: "#fff", weight: 1, fillOpacity: 0.78,
          });
          const tip = `<b>${c.name}</b><br>Destination: labor demand<br>Remittances sent abroad: <span class="v">${c.rout}%</span> of GDP<br>${riskLine(c.comp)}`;
          m.on("mousemove", e => { hover(tip, e); m.setRadius(rad + 2); m.setStyle({ weight: 2, fillOpacity: 0.95 }); m.bringToFront(); });
          m.on("mouseout", () => { hover(null); m.setRadius(rad); m.setStyle({ weight: 1, fillOpacity: 0.78 }); });
          m.on("click", e => {
            L.DomEvent.stopPropagation(e);
            simplePopup(e.latlng, `<div class="pk-t">${c.name}</div><div class="pk-sub">Destination: labor demand</div><div class="pk-row">Remittances sent abroad <span class="v">${c.rout}%</span> <span class="muted">of GDP</span></div><div class="pk-row">${riskLine(c.comp)}</div>`);
          });
          if (outLabel.has(c.iso3)) m.bindTooltip(c.name, { permanent: true, direction: "left", offset: [-(rad - 2), 0], className: "corridor-label corridor-dem" });
          lyr.addLayer(m);
        });
        lyr._onShow = () => {
          if (legendEl) legendEl.innerHTML =
            `<span><span class="swatch" style="background:#2166ac"></span>Origin: labor-export pressure (remittances received, %GDP)</span>` +
            `<span><span class="swatch" style="background:#c0392b"></span>Destination: labor demand (remittances sent abroad, %GDP)</span>`;
          if (legendNoteEl) legendNoteEl.innerHTML = "Blue = origin economies reliant on labor sent abroad (remittance <b>inflows</b>). Red = destination economies whose residents send the most money home (remittance <b>outflows</b>), the demand side, where migrant labor concentrates (the Gulf, Luxembourg, Switzerland). A proxy for migration corridors, not a flow map; neither side is part of the composite score.";
          setCount(inRows.length + " origin &middot; " + outRows.length + " destination economies");
          setAux("Both sides of the labor corridor: where workers leave, and where demand pulls them.");
        };
        return lyr;
      });
    }

    // --- LISA clusters (SUBNATIONAL admin-1) ---
    // Local Moran's I clusters on the admin-1 risk surface. Geometry from admin1_risk.topojson
    // (object "admin1"); join by feature properties.id to lisa_admin1.json. Colour each region by
    // its cluster class; the legend lists only the classes actually present (so the rare High-Low /
    // Low-High outliers appear and empty boxes do not).
    function buildLisa() {
      return Promise.all([
        fetch(rel("data/admin1_risk.topojson")).then(r => r.json()),
        getLisaAdmin1(),
      ]).then(([topo, clusters]) => {
        const obj = topo.objects.admin1 || topo.objects[Object.keys(topo.objects)[0]];
        const geo = splitAntimeridian(topojson.feature(topo, obj));
        const FILL = {
          "High-High": "#b2182b", "Low-Low": "#2166ac",
          "High-Low": "#f4a582", "Low-High": "#92c5de",
          "Not-Significant": "#e7e3d9", "Isolate (no neighbours)": "#e7e3d9",
        };
        const FRIENDLY = {
          "High-High": "High-High (high-risk belt)", "Low-Low": "Low-Low (cold belt)",
          "High-Low": "High-Low (hidden hotspot)", "Low-High": "Low-High (relief pocket)",
          "Not-Significant": "Not significant", "Isolate (no neighbours)": "Isolate (no neighbours)",
        };
        const clusterLayers = {};  // cluster class -> [leaflet layers], for the "locate" action
        let _hiTimer = null, _hiReset = null;
        const present = {};        // cluster class -> count, only those that occur on the map
        geo.features.forEach(f => {
          const rec = clusters[f.properties.id];
          f.properties._lisa = rec || null;
          if (rec) present[rec.cluster] = (present[rec.cluster] || 0) + 1;
        });
        const lyr = L.geoJSON(geo, {
          style: f => {
            const rec = f.properties._lisa;
            if (!rec) return { color: "#cfcabd", weight: 0.15, fillColor: "#e7e3d9", fillOpacity: 0.35 };
            const faint = rec.cluster === "Not-Significant" || rec.cluster.indexOf("Isolate") === 0 || rec.cluster === "Low-Low";
            return { color: "#fff", weight: 0.25, fillColor: FILL[rec.cluster] || "#e7e3d9", fillOpacity: faint ? 0.45 : 0.88 };
          },
          onEachFeature: (f, l) => {
            const p = f.properties, rec = p._lisa;
            if (rec) (clusterLayers[rec.cluster] = clusterLayers[rec.cluster] || []).push(l);
            const lbl = rec ? (rec.cluster.indexOf("Isolate") === 0 ? "Isolate (no neighbours)" : rec.cluster) : null;
            l.on("mousemove", e => {
              if (rec) {
                hover(`<b>${rec.name || p.name}</b><br><span class="muted">${rec.iso3 || p.cntry || ""}</span><br>Cluster: <span class="v">${lbl}</span>`, e);
              } else {
                hover(`<b>${p.name || p.cntry}</b><br><span class="muted">${p.cntry || ""}</span><br><span class="muted">Not in cluster analysis</span>`, e);
              }
              l.setStyle({ weight: 1.1, color: "#222" }); l.bringToFront();
            });
            l.on("mouseout", () => { hover(null); lyr.resetStyle(l); });
            l.on("click", e => {
              L.DomEvent.stopPropagation(e);
              const body = rec
                ? `<div class="pk-row">Cluster <span class="v">${FRIENDLY[rec.cluster] || lbl}</span></div>`
                : `<div class="pk-note">Not part of the cluster analysis.</div>`;
              simplePopup(e.latlng, `<div class="pk-t">${(rec && rec.name) || p.name || p.cntry}</div><div class="pk-sub">${(rec && rec.iso3) || p.cntry || ""}</div>${body}`);
            });
          },
        });
        // briefly highlight every region of one cluster class, and frame them, so the rare
        // High-Low / Low-High outliers can actually be found on the map.
        function locateCluster(cls) {
          const layers = clusterLayers[cls] || [];
          if (!layers.length) return;
          if (_hiTimer) { clearTimeout(_hiTimer); if (_hiReset) _hiReset(); _hiTimer = null; _hiReset = null; }
          const b = L.latLngBounds([]);
          layers.forEach(l => { try { b.extend(l.getBounds()); } catch (_) {} });
          if (b.isValid()) map.fitBounds(b, { padding: [60, 60], maxZoom: 6, animate: true });
          layers.forEach(l => { l.setStyle({ weight: 3.4, color: "#000", fillOpacity: 1, opacity: 1, dashArray: null }); l.bringToFront(); });
          _hiReset = () => layers.forEach(l => { try { lyr.resetStyle(l); } catch (_) {} });
          _hiTimer = setTimeout(() => { if (_hiReset) _hiReset(); _hiReset = null; _hiTimer = null; }, 3200);
        }
        lyr._onShow = () => {
          // legend rows: only the cluster classes present, interesting outliers included.
          const order = ["High-High", "Low-Low", "High-Low", "Low-High", "Not-Significant", "Isolate (no neighbours)"];
          const swClass = {
            "High-High": "lisa-hh", "Low-Low": "lisa-ll", "High-Low": "lisa-hl",
            "Low-High": "lisa-lh", "Not-Significant": "lisa-ns", "Isolate (no neighbours)": "lisa-ns",
          };
          const LOCATABLE = { "High-High": 1, "Low-Low": 1, "High-Low": 1, "Low-High": 1 };
          if (legendEl) {
            legendEl.innerHTML = order
              .filter(c => present[c])
              .map(c => {
                const can = LOCATABLE[c];
                return `<span class="lg-row${can ? " lg-locate" : ""}"${can ? ` data-cluster="${c}" title="Click to locate these regions on the map"` : ""}><span class="swatch-lisa ${swClass[c]}"></span>${FRIENDLY[c]} (${present[c]})</span>`;
              })
              .join("");
            legendEl.querySelectorAll(".lg-locate").forEach(elr =>
              elr.addEventListener("click", () => locateCluster(elr.getAttribute("data-cluster"))));
          }
          if (legendNoteEl) legendNoteEl.innerHTML = "<b>Global Moran's I = 0.81</b> (p &asymp; 0.001): risk is strongly spatially clustered; this map is the local breakdown of that. Local Moran's I, FDR-corrected. High-Low and Low-High are the spatial outliers: a region unlike its neighbours. <b>Click a cluster type above to locate it.</b>";
          setCount((present["High-High"] || 0) + " High-High &middot; " + (present["Low-Low"] || 0) + " Low-Low &middot; " +
                   ((present["High-Low"] || 0) + (present["Low-High"] || 0)) + " outlier regions");
          setAux("Spatial clusters of subnational risk, and the regions that break from their neighbours.");
        };
        return lyr;
      });
    }

    const BUILDERS = {
      composite: buildComposite, subnational: buildSubnational,
      corridors: buildCorridors, monetization: buildMonetization, lisa: buildLisa,
    };

    function show(name) {
      map.closePopup();
      if (current && built[current]) map.removeLayer(built[current]);
      const swatch = () => { if (pageEl) pageEl.classList.remove("on-dark"); }; // always light base
      swatch();
      const place = lyr => {
        built[name] = lyr; current = name;
        map.addLayer(lyr);
        if (lyr._onShow) lyr._onShow();
        if (layersEl) layersEl.querySelectorAll("button[data-layer]").forEach(b =>
          b.setAttribute("aria-pressed", b.getAttribute("data-layer") === name ? "true" : "false"));
      };
      if (built[name]) { place(built[name]); return; }
      // loading state
      setAux("Loading&hellip;");
      BUILDERS[name]().then(place).catch(err => {
        setAux("");
        if (legendNoteEl) legendNoteEl.innerHTML = "This layer&rsquo;s data could not load (" + err + "). The composite remains available.";
        console.error("explore layer " + name, err);
      });
    }

    if (layersEl) layersEl.querySelectorAll("button[data-layer]").forEach(b =>
      b.addEventListener("click", () => show(b.getAttribute("data-layer"))));

    // honest invalidation after layout settles (overlays float over the map)
    setTimeout(() => map.invalidateSize(), 60);
    window.addEventListener("resize", () => map.invalidateSize());

    show("composite");
  }

  // ---- public API ----
  window.FLSRI = {
    injectChrome, loadScores, loadDomains, compositeMap, subnationalMap, exploreMap,
    renderCountryPanel, renderDomainCircles, buildPicker, fillMeta, fillTopBottom, buildRankings,
    initSimulation, buildSimulator, buildInterventionReadout, tierOf, fmt,
  };

  document.addEventListener("DOMContentLoaded", injectChrome);
})();
