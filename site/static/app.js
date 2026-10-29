"use strict";

const state = {
  market: "KR",
  rows: [],
  snapshot: null,
  selected: null,
  chip: "all",
  dropThreshold: null,   // 고점 대비 하락 임계치. null이면 스냅샷 기본값
  months: null,          // 하락 기간(개월). null이면 스냅샷 기본값
  minScore: 70,
  detail: null,       // 열려 있는 상세(기간을 바꾸면 다시 그린다)
  chart: null,
};

// 정적 사이트(Vercel)로 내보낸 화면이면 true다. stockscout export가
// index.html에 심는다 (web/export.py). 그때는 API 대신 내보낸 JSON 파일을
// 읽고, 서버가 하던 검색·정렬은 브라우저가 하고, 관심종목은 방문자
// 브라우저(localStorage)에 둔다 - 공개 사이트는 읽기 전용이다.
const STATIC = typeof window !== "undefined" && window.STOCKSCOUT_STATIC === true;

const $ = (id) => document.getElementById(id);
const pct = (v) => (v === null || v === undefined) ? "—" : (v * 100).toFixed(1) + "%";
// 금액은 전부 이 함수를 거친다. 시장마다 통화가 다르다 (설계서 §18.5).
// 달러는 센트까지, 원은 정수로.
function money(v) {
  if (v === null || v === undefined) return "—";
  if (state.market === "US") {
    return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return v.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
}

// 재무제표 금액. 미국은 10-K의 관례대로 백만 달러로 쓴다 -
// $82,584,000,000.00은 읽을 수 없다. 한국은 지금처럼 원 단위 그대로다.
function amount(v) {
  if (v === null || v === undefined) return "—";
  if (state.market === "US") {
    return "$" + Math.round(v / 1e6).toLocaleString("en-US") + "M";
  }
  return money(v);
}
const signClass = (v) => (v === null || v === undefined) ? "" : (v < 0 ? "neg" : "pos");

// 슬라이더 값은 다음 방문에 복원한다. 설정 파일 기본값은 건드리지 않는다 (설계서 §11.1).
function loadPrefs() {
  try {
    // 키 이름이 바뀌었다(stockscout.dropThreshold는 5년 기준 -40%였다).
    // 옛 값을 그대로 쓰면 단기 하락 기본값(-25%)이 -40%로 덮인다.
    const saved = localStorage.getItem("stockscout.ddThreshold");
    if (saved !== null) state.dropThreshold = parseFloat(saved);
    const months = parseInt(localStorage.getItem("stockscout.ddMonths"), 10);
    if (months > 0) state.months = months;
    const minScore = localStorage.getItem("stockscout.minScore");
    if (minScore !== null) state.minScore = parseFloat(minScore);
    const market = localStorage.getItem("stockscout.market");
    if (market === "KR" || market === "US") state.market = market;
  } catch (e) { /* 시크릿 모드 등에서 막힐 수 있다 */ }
}

function savePrefs() {
  try {
    localStorage.setItem("stockscout.ddThreshold", String(state.dropThreshold));
    localStorage.setItem("stockscout.ddMonths", String(state.months));
    localStorage.setItem("stockscout.minScore", String(state.minScore));
    localStorage.setItem("stockscout.market", state.market);
  } catch (e) { /* 저장 실패는 기능을 막지 않는다 */ }
}

// 검색·정렬. 정적 모드에서만 쓴다. 규칙은 api.list_securities와 같다 -
// 하락률 오름차순(없는 종목은 뒤), 이름순, 코드순. 이름은 localeCompare가
// 아니라 코드포인트로 비교한다(파이썬 sort와 같게).
function filterAndSort(rows, q, sort) {
  const needle = q.toLowerCase();
  const out = needle
    ? rows.filter((r) => r.name.toLowerCase().includes(needle)
                      || r.ticker.toLowerCase().includes(needle))
    : rows.slice();
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  if (sort === "drop") {
    const drop = (r) => { const dd = drawdownOf(r); return dd ? dd.drop_from_high : null; };
    out.sort((a, b) => {
      const na = drop(a) === null, nb = drop(b) === null;
      if (na !== nb) return na ? 1 : -1;
      return (drop(a) || 0) - (drop(b) || 0);
    });
  } else if (sort === "name") {
    out.sort((a, b) => cmp(a.name, b.name));
  } else {
    out.sort((a, b) => cmp(a.ticker, b.ticker));
  }
  return out;
}

function loadWatchlist() {
  try {
    return new Set(JSON.parse(localStorage.getItem("stockscout.watchlist") || "[]"));
  } catch (e) { return new Set(); }
}

function saveWatchlist(set) {
  try {
    localStorage.setItem("stockscout.watchlist", JSON.stringify([...set]));
  } catch (e) { /* 저장 실패는 기능을 막지 않는다 */ }
}

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${url}`);
  }
  return res.json();
}

// --- 판정 ------------------------------------------------------------
//
// 임계치는 지표를 바꾸지 않고 등급만 나눈다. 그래서 서버 왕복 없이
// 여기서 즉시 재판정한다 (설계서 §6.1).
//
// 점수가 없는 동안 ★★/★ 등급은 매기지 않는다. 가격만으로 등급을
// 매기면 품질을 검증한 것처럼 보이는 거짓말이 된다 (설계서 §9.3).

// 선택한 하락 기간(state.months)의 최근 고점 대비 하락. 서버가 1·3·6·12개월을
// 미리 계산해 둔다(analysis/valuation.compute_drawdowns). 그 기간 값이 없으면
// (이 기능 이전 스냅샷) null이다 - 다른 기간 값으로 대신 판정하지 않는다.
function drawdownOf(row) {
  return (row.drawdown && row.drawdown[state.months]) || null;
}

function hits(row) {
  const dd = drawdownOf(row);
  return dd !== null && dd.drop_from_high <= state.dropThreshold;
}

// 서버의 grade_signal(analysis/grading.py)과 같은 규칙이다. 품질이 먼저다 -
// 점수가 기준에 못 미치면 아무리 떨어져도 등급이 없다. 기본 상태에서는
// 슬라이더 하나가 평균·고점 두 임계치를 함께 움직인다 (§11.1).
function gradeOf(row) {
  if (!state.snapshot.has_scores) return null;
  if (!row.passed_filters || row.score_total === null) return null;
  if (row.score_total < state.minScore) return null;
  // 후보 = 최근 N개월 고점 대비 임계치 이하. 하락이 멈췄으면 별 둘, 아직
  // 빠지는 중이면 별 하나 (analysis/grading.py와 같다).
  // 영역 최소점 미달(서버가 analysis/grading.weak_axes로 계산해 둔다).
  if (row.weak_axes && row.weak_axes.length) return null;
  const dd = drawdownOf(row);
  if (dd === null || dd.drop_from_high > state.dropThreshold) return null;
  return dd.stabilized ? "STRONG" : "WATCH";
}

const GRADE_BADGES = { STRONG: "★★", WATCH: "★" };

function visibleRows() {
  let rows = state.rows;
  if (state.chip === "passed") rows = rows.filter((r) => r.passed_filters);
  else if (state.chip === "excluded") rows = rows.filter((r) => !r.passed_filters);
  else if (state.chip === "watch") rows = rows.filter((r) => r.in_watchlist);
  else if (state.chip === "hit") rows = rows.filter((r) => r.passed_filters && hits(r));
  else if (state.chip === "strong") rows = rows.filter((r) => gradeOf(r) === "STRONG");
  else if (state.chip === "single") rows = rows.filter((r) => gradeOf(r) === "WATCH");
  return rows;
}

// --- 렌더 ------------------------------------------------------------

const MARKETS = [["KR", "한국"], ["US", "미국"]];

function renderMarketTabs() {
  const nav = $("market-tabs");
  nav.innerHTML = "";
  for (const [key, label] of MARKETS) {
    const b = document.createElement("button");
    b.className = "tab" + (state.market === key ? " on" : "");
    b.textContent = label;
    b.onclick = async () => {
      if (state.market === key) return;
      state.market = key;
      savePrefs();
      await loadMarket();
    };
    nav.appendChild(b);
  }
}

function renderSnapshotInfo() {
  const s = state.snapshot;
  const parts = [`기준일 ${s.as_of ?? "?"}`, `${s.total}종목`,
                 `통과 ${s.passed}`, `제외 ${s.excluded}`];
  if (s.note) parts.push(s.note);
  $("snapshot-info").textContent = parts.join(" · ");
}

function renderScoreBlock() {
  const hasScores = state.snapshot.has_scores;
  $("score-threshold").disabled = !hasScores;
  $("score-threshold").value = String(state.minScore);
  $("score-slider-block").classList.toggle("disabled-block", !hasScores);
  $("score-value").textContent = hasScores ? `${state.minScore}점` : "—";
  $("score-hint").textContent = hasScores
    ? ""
    : "재무 데이터가 없어 우량주 점수를 계산하지 못했습니다. "
      + (state.market === "US"
        ? "SEC_USER_AGENT에 연락처를 넣고 collect · normalize · analyze 를 실행하면 켜집니다."
        : "DART 인증키를 넣고 collect · normalize · analyze 를 실행하면 켜집니다.");
}

function renderChips() {
  // 점수가 없는 동안은 등급 칩 대신 가격만 본 "임계치 이하"를 보인다.
  // 그때 ★를 보이면 품질을 확인한 것처럼 읽힌다.
  const gradeDefs = state.snapshot.has_scores
    ? [["strong", "★★"], ["single", "★"]]
    : [["hit", "임계치 이하"]];
  const defs = [
    ["all", "전체"],
    ...gradeDefs,
    ["passed", "통과"],
    ["excluded", "제외"],
    ["watch", "관심"],
  ];
  $("chips").innerHTML = "";
  for (const [key, label] of defs) {
    const b = document.createElement("button");
    b.className = "chip" + (state.chip === key ? " on" : "");
    b.textContent = label;
    b.onclick = () => { state.chip = key; renderChips(); renderList(); };
    $("chips").appendChild(b);
  }
}

function renderHitCount() {
  const passing = state.rows.filter((r) => r.passed_filters);
  if (state.snapshot.has_scores) {
    const strong = passing.filter((r) => gradeOf(r) === "STRONG").length;
    const single = passing.filter((r) => gradeOf(r) === "WATCH").length;
    $("hit-count").textContent =
      `${state.months}개월 기준 -> ★★ 멈춤 ${strong} · ★ 하락중 ${single} / 통과 ${passing.length}종목`;
    return;
  }
  const n = passing.filter(hits).length;
  $("hit-count").textContent =
    `${state.months}개월 기준 -> ${n}종목 해당 / 통과 ${passing.length}종목`;
}

function renderList() {
  const ul = $("stock-list");
  ul.innerHTML = "";
  for (const row of visibleRows()) {
    const li = document.createElement("li");
    li.className = (row.ticker === state.selected ? "selected " : "")
      + (row.passed_filters ? "" : "excluded");
    li.onclick = () => selectTicker(row.ticker);

    const star = document.createElement("span");
    star.className = "star";
    star.textContent = row.in_watchlist ? "★" : "☆";
    star.title = "관심종목";
    star.onclick = (e) => { e.stopPropagation(); toggleWatch(row); };

    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = row.name;
    const grade = gradeOf(row);
    if (grade) {
      const badge = document.createElement("span");
      badge.className = "grade " + grade.toLowerCase();
      badge.textContent = GRADE_BADGES[grade];
      nm.append(" ", badge);
    }

    const dp = document.createElement("span");
    const dd = drawdownOf(row);
    const drop = dd ? dd.drop_from_high : null;
    dp.className = "dp " + signClass(drop);
    dp.textContent = pct(drop);

    li.append(star, nm, dp);
    ul.appendChild(li);
  }
  if (!ul.children.length) {
    const li = document.createElement("li");
    li.textContent = "해당하는 종목이 없습니다.";
    li.style.color = "var(--muted)";
    li.style.cursor = "default";
    ul.appendChild(li);
  }
}

async function toggleWatch(row) {
  if (STATIC) {
    const watched = loadWatchlist();
    if (row.in_watchlist) watched.delete(row.ticker); else watched.add(row.ticker);
    saveWatchlist(watched);
  } else {
    const method = row.in_watchlist ? "DELETE" : "POST";
    await fetch(`/api/watchlist/${encodeURIComponent(row.ticker)}`, { method });
  }
  row.in_watchlist = !row.in_watchlist;
  renderList();
}

// --- 우측 상세 패널 --------------------------------------------------

const DART_GUIDE = `<div class="empty-state">
  <b>재무 데이터가 없습니다</b>
  DART 공시에서 재무제표를 수집하면 우량주 점수와 재무 추이가 여기에
  표시됩니다. 지금은 주가 기준 하락률만 계산됩니다.
  <pre>1. https://opendart.fss.or.kr 에서 인증키 발급
2. config/.env 의 DART_API_KEY= 뒤에 붙여넣기
3. stockscout collect --market KR
   stockscout normalize --market KR
   stockscout analyze --market KR</pre>
</div>`;

const EDGAR_GUIDE = `<div class="empty-state">
  <b>재무 데이터가 아직 없습니다</b>
  config/.env 의 SEC_USER_AGENT 에 연락처(이메일)를 넣고 실행하세요.
  <pre>stockscout collect --market US
stockscout normalize --market US
stockscout analyze --market US</pre>
</div>`;

async function selectTicker(ticker) {
  state.selected = ticker;
  renderList();
  const panel = $("detail-panel");
  panel.innerHTML = `<p class="placeholder">불러오는 중…</p>`;
  try {
    const t = encodeURIComponent(ticker);
    const [detail, prices] = await Promise.all(STATIC
      ? [getJSON(`/data/securities/${t}.json`), getJSON(`/data/securities/${t}.prices.json`)]
      : [getJSON(`/api/securities/${t}`), getJSON(`/api/securities/${t}/prices`)]);
    if (STATIC) detail.in_watchlist = loadWatchlist().has(ticker);
    renderDetail(detail, prices);
  } catch (err) {
    panel.innerHTML = `<p class="placeholder">${err.message}</p>`;
  }
}

function renderDetail(d, prices) {
  state.detail = { d, prices };
  const dd = drawdownOf(d);
  const panel = $("detail-panel");
  panel.innerHTML = "";

  // 헤더
  const header = document.createElement("div");
  header.className = "detail-header";
  header.innerHTML = `
    <h2>${escapeHtml(d.name)}</h2>
    <span class="code">${escapeHtml(d.ticker)} · ${escapeHtml(d.exchange ?? "")}</span>
    <span class="badge ${d.passed_filters ? "pass" : "fail"}">
      ${d.passed_filters ? "통과" : "제외"}
    </span>
    <span style="flex:1"></span>
    <span class="star" id="detail-star">${d.in_watchlist ? "★" : "☆"}</span>`;
  panel.appendChild(header);
  header.querySelector("#detail-star").onclick = async () => {
    const row = state.rows.find((r) => r.ticker === d.ticker);
    if (row) { await toggleWatch(row); d.in_watchlist = row.in_watchlist; }
    header.querySelector("#detail-star").textContent = d.in_watchlist ? "★" : "☆";
  };

  // 차트
  const box = document.createElement("div");
  box.id = "chart-box";
  box.innerHTML = `<canvas id="price-chart"></canvas>`;
  panel.appendChild(box);

  // KPI
  const kpis = document.createElement("div");
  kpis.className = "kpis";
  kpis.innerHTML = [
    kpi(money(d.price_current), "현재가"),
    kpi(pct(dd && dd.drop_from_high), `최근 ${state.months}개월 고점 대비`,
        signClass(dd && dd.drop_from_high)),
    dd && dd.market_drop !== null && dd.market_drop !== undefined
      ? kpi(pct(dd.market_drop), `같은 기간 ${BENCHMARK_LABELS[d.benchmark] ?? "시장"}`,
            signClass(dd.market_drop))
      : kpi("—", "같은 기간 시장", "", true),
    dd
      ? kpi(dd.stabilized ? "멈춤" : "하락 중",
            `저점 후 ${dd.days_since_low}거래일`, dd.stabilized ? "pos" : "neg")
      : kpi("—", "하락 상태", "", true),
    d.score_total === null
      ? kpi("—", "우량주 점수", "", true)
      : kpi(d.score_total.toFixed(0), "우량주 점수"),
  ].join("");
  panel.appendChild(kpis);

  // 최근 실적 변화. 5년 평균 점수가 최근 악화를 가릴 수 있다.
  if (d.interim) {
    const it = d.interim;
    const rec = document.createElement("p");
    rec.className = "interim" + (it.deteriorated ? " warn" : "");
    const g = (v) => (v === null || v === undefined) ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(0) + "%";
    rec.textContent =
      `최근 실적 (${it.period_end.slice(0, 4)}년 ${it.months}개월 누적, 전년 동기 대비): `
      + `매출 ${g(it.revenue_growth)} · 영업이익 ${g(it.operating_income_growth)}`
      + ` · 영업이익률 ${pct(it.operating_margin_prior)} → ${pct(it.operating_margin)}`
      + (it.deteriorated ? " · 주의: 최근 실적 급감" : "");
    panel.appendChild(rec);
  }

  // 최근 공시. 하락 원인과 회복 계기는 사람이 판단한다 - 그 재료를 보인다.
  if (d.filings && d.filings.length) {
    panel.appendChild(sectionTitle("최근 공시"));
    const ul = document.createElement("ul");
    ul.className = "filings";
    for (const f of d.filings) {
      const li = document.createElement("li");
      const tag = FILING_LABELS[f.category];
      li.innerHTML = `<span class="fdate">${escapeHtml(f.filed)}</span>`
        + (tag ? `<span class="ftag ${f.category.toLowerCase()}">${tag}</span>` : "")
        + `<a href="${escapeHtml(f.url)}" target="_blank" rel="noopener">${escapeHtml(f.title)}</a>`;
      ul.appendChild(li);
    }
    panel.appendChild(ul);
  }

  // 가치평가. 많이 떨어졌어도 여전히 비쌀 수 있다. 점수에는 넣지 않는다(§8.2).
  const val = document.createElement("p");
  val.className = "valuation";
  const ratio = (v) => (v === null || v === undefined) ? "—" : v.toFixed(1) + "배";
  val.textContent = [
    `시가총액 ${amount(d.market_cap)}`,
    `PER ${ratio(d.per)}`,
    `PBR ${ratio(d.pbr)}`,
    `FCF 수익률 ${pct(d.fcf_yield)}`,
    dd && dd.period_return !== null && dd.period_return !== undefined
      ? `${state.months}개월 수익률 ${pct(dd.period_return)}` : null,
  ].filter(Boolean).join(" · ");
  panel.appendChild(val);

  // 점수 분해 (설계서 §11.2). 제외된 종목은 채점하지 않았으므로 막대가 없다.
  if (d.score_detail) {
    panel.appendChild(sectionTitle("점수 분해"));
    const bars = document.createElement("div");
    bars.className = "score-bars";
    bars.innerHTML = scoreBars(d.score_detail);
    panel.appendChild(bars);
  }

  // 판정 근거
  panel.appendChild(sectionTitle("판정 근거"));
  const rat = document.createElement("div");
  rat.className = "rationale";
  rat.innerHTML = d.rationale.map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  panel.appendChild(rat);

  // 재무 추이
  panel.appendChild(sectionTitle("재무 추이"));
  if (!d.financials.length) {
    const box2 = document.createElement("div");
    box2.innerHTML = state.market === "US" ? EDGAR_GUIDE : DART_GUIDE;
    panel.appendChild(box2);
  } else {
    panel.appendChild(financialsTable(d.financials));
  }

  drawChart(prices);
}

const FILING_LABELS = { RISK: "위험", DILUTION: "자금조달", EARNINGS: "실적", LEGAL: "소송",
                        CATALYST: "수주·주주환원" };

const BENCHMARK_LABELS = { "^KS11": "코스피", "^KQ11": "코스닥", "^GSPC": "S&P 500" };

function kpi(value, label, cls = "", empty = false) {
  return `<div class="kpi${empty ? " empty" : ""}">
    <b class="${cls}">${value}</b><span>${label}</span></div>`;
}

function sectionTitle(text) {
  const el = document.createElement("div");
  el.className = "section-title";
  el.textContent = text;
  return el;
}

const AXIS_LABELS = {
  profitability: "수익성",
  growth: "성장성",
  stability: "안정성",
  cashflow: "현금흐름",
};

const METRIC_LABELS = {
  roe_avg3: "ROE 3년 평균",
  op_margin_avg3: "영업이익률 3년 평균",
  op_margin_trend: "영업이익률 추세",
  revenue_cagr3: "매출 CAGR 3년",
  op_income_cagr3: "영업이익 CAGR 3년",
  growth_consistency: "성장 일관성",
  debt_ratio: "부채비율",
  current_ratio: "유동비율",
  interest_coverage: "이자보상배율",
  ocf_positive_years: "OCF 흑자 연속",
  ocf_to_op_income: "OCF / 영업이익",
  fcf_positive_years: "FCF 흑자 연속",
};

// 0점이지만 사업이 나빠서가 아닐 수 있는 지표마다 이유를 단다.
// 문구가 없으면 "0.0 / 10"만 보여 부실로 읽힌다.
const METRIC_STATUS_LABELS = {
  MISSING: "데이터 없음",
  BASE_NEGATIVE: "3년 전 기저가 적자라 계산 불가 (0점)",
  END_NONPOSITIVE: "흑자에서 적자로 전환 (0점)",
  DENOMINATOR_NONPOSITIVE: "3년 누계 영업이익이 0 이하 (0점)",
};

// 4축 막대. <details>를 쓰는 이유: §11.2의 "막대 클릭 시 지표별 상세
// 펼침"을 브라우저가 공짜로 준다. 배점은 설정에서 오므로 max_points로
// 환산한다 - 숫자를 박으면 사용자가 YAML을 고쳐도 막대가 안 따라온다.
function scoreBars(detailJson) {
  let card;
  try {
    card = typeof detailJson === "string" ? JSON.parse(detailJson) : detailJson;
  } catch (e) {
    return "";
  }
  if (!card || !card.axes) return "";

  return card.axes.map((axis) => {
    const label = AXIS_LABELS[axis.axis] ?? axis.axis;
    const max = axis.max_points ?? 0;
    const unmeasurable = axis.status === "AXIS_UNMEASURABLE";
    const scaled = unmeasurable || axis.denominator <= 0
      ? 0
      : (axis.earned / axis.denominator) * max;
    const width = max > 0 ? (scaled / max) * 100 : 0;
    const note = unmeasurable
      ? `<span class="axis-note">지표를 절반도 못 구해 측정 불가</span>`
      : "";

    const rows = (axis.metrics ?? []).map((m) => {
      const name = METRIC_LABELS[m.metric] ?? m.metric;
      const status = METRIC_STATUS_LABELS[m.status];
      const value = status
        ? `<i>${escapeHtml(status)}</i>`
        : `${m.points.toFixed(1)} / ${m.max_points.toFixed(0)}`;
      return `<div class="metric-row"><span>${escapeHtml(name)}</span>${value}</div>`;
    }).join("");

    return `<details class="axis${unmeasurable ? " unmeasurable" : ""}">
      <summary>
        <span class="axis-name">${escapeHtml(label)}</span>
        <span class="axis-track"><span class="axis-fill" style="width:${width.toFixed(1)}%"></span></span>
        <span class="axis-score">${scaled.toFixed(1)} / ${max.toFixed(0)}</span>
        ${note}
      </summary>
      ${rows}
    </details>`;
  }).join("");
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function financialsTable(rows) {
  const table = document.createElement("table");
  table.className = "fin";
  const years = rows.map((r) => r.fiscal_year);
  const line = (label, pick) =>
    `<tr><td>${label}</td>` + rows.map((r) => `<td>${pick(r)}</td>`).join("") + "</tr>";

  table.innerHTML =
    `<tr><th>항목</th>${years.map((y) => `<th>${y}</th>`).join("")}</tr>` +
    line("매출", (r) => amount(r.revenue)) +
    line("영업이익", (r) => amount(r.operating_income)) +
    line("영업이익률", (r) => pct(r.operating_margin)) +
    line("ROE", (r) => pct(r.roe)) +
    line("부채비율", (r) => r.debt_ratio === null ? "—" : r.debt_ratio.toFixed(1) + "%") +
    line("영업현금흐름", (r) => amount(r.operating_cash_flow));
  return table;
}

function drawChart(p) {
  if (state.chart) state.chart.destroy();

  // 기준선은 서버가 준 값을 그대로 쓴다. 여기서 다시 평균을 내면
  // 창 정의나 반올림이 갈려 차트와 판정 근거가 어긋난다.
  const dd = state.detail ? drawdownOf(state.detail.d) : null;
  const refs = [
    [`${state.months}개월 고점`, dd ? dd.high : null, "#d69e2e"],
    ["5년 고점", p.high_5y, "#e53e3e"],
    ["5년 평균", p.avg_price_5y, "#805ad5"],
    ["3년 평균", p.avg_price_3y, "#38a169"],
    ["1년 평균", p.avg_price_1y, "#dd6b20"],
  ].filter(([, v]) => v !== null && v !== undefined);

  const labels = p.series.map((x) => x.date);
  const datasets = [{
    label: "종가",
    data: p.series.map((x) => x.close),
    borderColor: "#2b6cb0",
    borderWidth: 1.4,
    pointRadius: 0,
    tension: 0,
  }];
  for (const [label, value, color] of refs) {
    datasets.push({
      label: `${label} ${money(value)}`,
      data: labels.map(() => value),
      borderColor: color,
      borderWidth: 1,
      borderDash: [5, 4],
      pointRadius: 0,
    });
  }

  state.chart = new Chart($("price-chart"), {
    type: "line",
    data: { labels, datasets },
    options: {
      // 애니메이션을 끈다. 1,200점짜리 선 6개를 애니메이션할 이유가 없고,
      // 화면 없는 브라우저(검증 스크립트)에서는 그리다 멈춰 선이 중간에서 끊겼다.
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
        y: { ticks: { font: { size: 10 }, callback: (v) => money(v) } },
      },
    },
  });
}

// --- 초기화 ----------------------------------------------------------

// 하락 기간 슬라이더의 선택지. 서버가 계산해 둔 기간(drawdown_choices)이다.
function periodChoices() {
  return (state.snapshot && state.snapshot.drawdown_choices) || [1, 3, 6, 12];
}

// 슬라이더 위치·표시를 state에 맞춘다. 스냅샷을 읽은 뒤(기본값이 정해진 뒤) 부른다.
function syncSliders() {
  const choices = periodChoices();
  if (!choices.includes(state.months)) state.months = choices[0];
  $("period").max = String(choices.length - 1);
  $("period").value = String(choices.indexOf(state.months));
  $("period-value").textContent = `${state.months}개월`;
  const pctValue = Math.round(-state.dropThreshold * 100);
  $("drop-threshold").value = String(pctValue);
  $("drop-value").textContent = `-${pctValue}%`;
}

function wireControls() {
  const slider = $("drop-threshold");

  slider.oninput = () => {
    state.dropThreshold = -Number(slider.value) / 100;
    $("drop-value").textContent = `-${slider.value}%`;
    savePrefs();
    renderHitCount();
    renderList();   // 등급 뱃지가 임계치를 따라 바뀐다
  };

  // 서버 왕복 없이 즉시 재판정한다 (설계서 §6.1).
  $("score-threshold").oninput = () => {
    state.minScore = Number($("score-threshold").value);
    $("score-value").textContent = `${state.minScore}점`;
    savePrefs();
    renderHitCount();
    renderList();
  };

  $("period").oninput = () => {
    state.months = periodChoices()[Number($("period").value)];
    $("period-value").textContent = `${state.months}개월`;
    savePrefs();
    state.rows = filterAndSort(state.rows, $("search").value.trim(), $("sort").value);
    renderHitCount();
    renderList();
    if (state.detail && state.selected === state.detail.d.ticker) {
      renderDetail(state.detail.d, state.detail.prices);
    }
  };

  $("search").oninput = async () => { await reload(); };
  $("sort").onchange = async () => { await reload(); };
}

async function reload() {
  if (!state.snapshot) return;   // 이 시장은 아직 분석하지 않았다
  const q = $("search").value.trim();
  const sort = $("sort").value;
  if (STATIC) {
    const watched = loadWatchlist();
    const rows = await getJSON(`/data/${state.market}/securities.json`);
    for (const r of rows) r.in_watchlist = watched.has(r.ticker);
    state.rows = filterAndSort(rows, q, sort);
  } else {
    const params = new URLSearchParams({ market: state.market, sort });
    if (q) params.set("q", q);
    // 서버는 5년 하락률로 정렬해 보낸다. 비교 기간을 바꿨으면 다시 정렬한다.
    state.rows = filterAndSort(await getJSON(`/api/securities?${params}`), "", sort);
  }
  renderHitCount();
  renderList();
}

// 시장을 바꿀 때마다 부른다. 스냅샷이 없으면 페이지를 지우지 않는다 -
// 탭은 남아 있어야 다른 시장으로 돌아갈 수 있다.
async function loadMarket() {
  renderMarketTabs();
  state.selected = null;
  state.detail = null;
  state.rows = [];
  $("detail-panel").innerHTML = `<p class="placeholder">왼쪽에서 종목을 선택하세요.</p>`;
  try {
    state.snapshot = await getJSON(STATIC
      ? `/data/${state.market}/snapshot.json`
      : `/api/snapshot?market=${state.market}`);
  } catch (err) {
    state.snapshot = null;
    $("snapshot-info").textContent = STATIC
      ? `아직 공개된 ${state.market === "US" ? "미국" : "한국"} 분석 결과가 없습니다.`
      : err.message;
    $("hit-count").textContent = "";
    $("chips").innerHTML = "";
    $("stock-list").innerHTML = "";
    return;
  }
  // 저장된 값이 없으면 서버가 판정한 기준을 기본값으로 쓴다 - 그래야
  // 처음 연 화면의 별이 서버 판정(signal_grade)과 같다.
  if (state.months === null) state.months = state.snapshot.drawdown_months ?? 3;
  if (state.dropThreshold === null) {
    state.dropThreshold = state.snapshot.drawdown_threshold ?? -0.25;
  }
  syncSliders();
  renderSnapshotInfo();
  renderScoreBlock();
  renderChips();
  await reload();
}

async function init() {
  loadPrefs();
  wireControls();
  await loadMarket();
}

init();
