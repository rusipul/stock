"use strict";

const state = {
  market: "KR",
  rows: [],
  snapshot: null,
  selected: null,
  chip: "all",
  dropThreshold: -0.40,
  minScore: 70,
  chart: null,
};

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
const signClass = (v) => (v === null || v === undefined) ? "" : (v < 0 ? "neg" : "pos");

// 슬라이더 값은 다음 방문에 복원한다. 설정 파일 기본값은 건드리지 않는다 (설계서 §11.1).
function loadPrefs() {
  try {
    const saved = localStorage.getItem("stockscout.dropThreshold");
    if (saved !== null) state.dropThreshold = parseFloat(saved);
    const minScore = localStorage.getItem("stockscout.minScore");
    if (minScore !== null) state.minScore = parseFloat(minScore);
    const market = localStorage.getItem("stockscout.market");
    if (market === "KR" || market === "US") state.market = market;
  } catch (e) { /* 시크릿 모드 등에서 막힐 수 있다 */ }
}

function savePrefs() {
  try {
    localStorage.setItem("stockscout.dropThreshold", String(state.dropThreshold));
    localStorage.setItem("stockscout.minScore", String(state.minScore));
    localStorage.setItem("stockscout.market", state.market);
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

function hits(row) {
  const d = row.drop_vs_avg_5y;
  return d !== null && d <= state.dropThreshold;
}

// 서버의 grade_signal(analysis/grading.py)과 같은 규칙이다. 품질이 먼저다 -
// 점수가 기준에 못 미치면 아무리 떨어져도 등급이 없다. 기본 상태에서는
// 슬라이더 하나가 평균·고점 두 임계치를 함께 움직인다 (§11.1).
function gradeOf(row) {
  if (!state.snapshot.has_scores) return null;
  if (!row.passed_filters || row.score_total === null) return null;
  if (row.score_total < state.minScore) return null;
  const avg = row.drop_vs_avg_5y;
  const avgHit = avg !== null && avg <= state.dropThreshold;
  const highHit = row.drop_vs_high !== null && row.drop_vs_high <= state.dropThreshold;
  if (avgHit && highHit) return "STRONG";
  if (avgHit || highHit) return "WATCH";
  return null;
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
      `현재 기준 -> ★★ ${strong}종목 · ★ ${single}종목 / 통과 ${passing.length}종목`;
    return;
  }
  const n = passing.filter(hits).length;
  $("hit-count").textContent =
    `현재 기준 -> ${n}종목 해당 / 통과 ${passing.length}종목`;
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
    dp.className = "dp " + signClass(row.drop_vs_avg_5y);
    dp.textContent = pct(row.drop_vs_avg_5y);

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
  const method = row.in_watchlist ? "DELETE" : "POST";
  await fetch(`/api/watchlist/${encodeURIComponent(row.ticker)}`, { method });
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
    const [detail, prices] = await Promise.all([
      getJSON(`/api/securities/${encodeURIComponent(ticker)}`),
      getJSON(`/api/securities/${encodeURIComponent(ticker)}/prices`),
    ]);
    renderDetail(detail, prices);
  } catch (err) {
    panel.innerHTML = `<p class="placeholder">${err.message}</p>`;
  }
}

function renderDetail(d, prices) {
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
    kpi(pct(d.drop_vs_avg_5y), "5년평균 대비", signClass(d.drop_vs_avg_5y)),
    kpi(pct(d.drop_vs_high), "5년고점 대비", signClass(d.drop_vs_high)),
    d.score_total === null
      ? kpi("—", "우량주 점수", "", true)
      : kpi(d.score_total.toFixed(0), "우량주 점수"),
  ].join("");
  panel.appendChild(kpis);

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
    line("매출", (r) => money(r.revenue)) +
    line("영업이익", (r) => money(r.operating_income)) +
    line("영업이익률", (r) => pct(r.operating_margin)) +
    line("ROE", (r) => pct(r.roe)) +
    line("부채비율", (r) => r.debt_ratio === null ? "—" : r.debt_ratio.toFixed(1) + "%") +
    line("영업현금흐름", (r) => money(r.operating_cash_flow));
  return table;
}

function drawChart(p) {
  if (state.chart) state.chart.destroy();

  // 기준선은 서버가 준 값을 그대로 쓴다. 여기서 다시 평균을 내면
  // 창 정의나 반올림이 갈려 차트와 판정 근거가 어긋난다.
  const refs = [
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

function wireControls() {
  const slider = $("drop-threshold");
  slider.value = String(Math.round(-state.dropThreshold * 100));
  $("drop-value").textContent = `-${slider.value}%`;

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

  $("search").oninput = async () => { await reload(); };
  $("sort").onchange = async () => { await reload(); };
}

async function reload() {
  if (!state.snapshot) return;   // 이 시장은 아직 분석하지 않았다
  const q = $("search").value.trim();
  const sort = $("sort").value;
  const params = new URLSearchParams({ market: state.market, sort });
  if (q) params.set("q", q);
  state.rows = await getJSON(`/api/securities?${params}`);
  renderHitCount();
  renderList();
}

// 시장을 바꿀 때마다 부른다. 스냅샷이 없으면 페이지를 지우지 않는다 -
// 탭은 남아 있어야 다른 시장으로 돌아갈 수 있다.
async function loadMarket() {
  renderMarketTabs();
  state.selected = null;
  state.rows = [];
  $("detail-panel").innerHTML = `<p class="placeholder">왼쪽에서 종목을 선택하세요.</p>`;
  try {
    state.snapshot = await getJSON(`/api/snapshot?market=${state.market}`);
  } catch (err) {
    state.snapshot = null;
    $("snapshot-info").textContent = err.message;
    $("hit-count").textContent = "";
    $("chips").innerHTML = "";
    $("stock-list").innerHTML = "";
    return;
  }
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
