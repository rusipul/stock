"use strict";

const state = {
  rows: [],
  snapshot: null,
  selected: null,
  chip: "all",
  dropThreshold: -0.40,
  chart: null,
};

const $ = (id) => document.getElementById(id);
const pct = (v) => (v === null || v === undefined) ? "—" : (v * 100).toFixed(1) + "%";
const won = (v) => (v === null || v === undefined) ? "—" : v.toLocaleString("ko-KR", { maximumFractionDigits: 0 });
const signClass = (v) => (v === null || v === undefined) ? "" : (v < 0 ? "neg" : "pos");

// 슬라이더 값은 다음 방문에 복원한다. 설정 파일 기본값은 건드리지 않는다 (설계서 §11.1).
function loadPrefs() {
  try {
    const saved = localStorage.getItem("stockscout.dropThreshold");
    if (saved !== null) state.dropThreshold = parseFloat(saved);
  } catch (e) { /* 시크릿 모드 등에서 막힐 수 있다 */ }
}

function savePrefs() {
  try {
    localStorage.setItem("stockscout.dropThreshold", String(state.dropThreshold));
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

function visibleRows() {
  let rows = state.rows;
  if (state.chip === "passed") rows = rows.filter((r) => r.passed_filters);
  else if (state.chip === "excluded") rows = rows.filter((r) => !r.passed_filters);
  else if (state.chip === "watch") rows = rows.filter((r) => r.in_watchlist);
  else if (state.chip === "hit") rows = rows.filter((r) => r.passed_filters && hits(r));
  return rows;
}

// --- 렌더 ------------------------------------------------------------

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
  $("score-slider-block").classList.toggle("disabled-block", !hasScores);
  $("score-value").textContent = hasScores ? "70점" : "—";
  $("score-hint").textContent = hasScores
    ? ""
    : "재무 데이터가 없어 우량주 점수를 계산하지 못했습니다. "
      + "DART 인증키를 넣고 collect · normalize · analyze 를 실행하면 켜집니다.";
}

function renderChips() {
  const defs = [
    ["all", "전체"],
    ["hit", "임계치 이하"],
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
    kpi(won(d.price_current), "현재가"),
    kpi(pct(d.drop_vs_avg_5y), "5년평균 대비", signClass(d.drop_vs_avg_5y)),
    kpi(pct(d.drop_vs_high), "5년고점 대비", signClass(d.drop_vs_high)),
    d.score_total === null
      ? kpi("—", "우량주 점수", "", true)
      : kpi(d.score_total.toFixed(0), "우량주 점수"),
  ].join("");
  panel.appendChild(kpis);

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
    box2.innerHTML = DART_GUIDE;
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
    line("매출", (r) => won(r.revenue)) +
    line("영업이익", (r) => won(r.operating_income)) +
    line("영업이익률", (r) => pct(r.operating_margin)) +
    line("ROE", (r) => pct(r.roe)) +
    line("부채비율", (r) => r.debt_ratio === null ? "—" : r.debt_ratio.toFixed(1) + "%") +
    line("영업현금흐름", (r) => won(r.operating_cash_flow));
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
      label: `${label} ${won(value)}`,
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
        y: { ticks: { font: { size: 10 }, callback: (v) => won(v) } },
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
    if (state.chip === "hit") renderList();
  };

  $("search").oninput = async () => { await reload(); };
  $("sort").onchange = async () => { await reload(); };
}

async function reload() {
  const q = $("search").value.trim();
  const sort = $("sort").value;
  const params = new URLSearchParams({ sort });
  if (q) params.set("q", q);
  state.rows = await getJSON(`/api/securities?${params}`);
  renderHitCount();
  renderList();
}

async function init() {
  loadPrefs();
  try {
    state.snapshot = await getJSON("/api/snapshot");
  } catch (err) {
    document.body.innerHTML =
      `<p style="padding:40px;text-align:center;color:#718096">${err.message}</p>`;
    return;
  }
  renderSnapshotInfo();
  renderScoreBlock();
  renderChips();
  wireControls();
  await reload();
}

init();
