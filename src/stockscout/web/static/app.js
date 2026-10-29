"use strict";

const state = {
  market: "KR",
  mode: "dip",           // "dip" = 낙폭 우량주, "sense" = 영탁센스
  sense: { lookback: null, mult: null, days: null },   // null = 스냅샷 기본값
  rows: [],
  snapshot: null,
  selected: null,
  chip: "all",
  dropThreshold: null,   // 고점 대비 하락 임계치. null이면 스냅샷 기본값
  months: null,          // 하락 기간(개월). null이면 스냅샷 기본값
  minScore: 70,
  detail: null,       // 열려 있는 상세(기간을 바꾸면 다시 그린다)
  chartPeriod: "all", // 차트 기간 버튼: "all" 또는 개월 수
  chartInterval: "D", // 봉 주기: D 일봉 · W 주봉 · M 월봉
  chartFull: null,    // 확대 전 전체 차트 데이터 {labels, data: [[...], ...]}
  chartView: null,    // 보이는 구간 [i0, i1] (전체 기준 인덱스)
  drag: null,         // 드래그 확대 중인 구간 {x0, x1}
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

// 재무제표 금액. 미국은 10-K의 관례대로 백만 달러, 한국은 억 원으로 쓴다 -
// $82,584,000,000.00이나 279,604,799,000,000은 절반 폭 칸에서 읽을 수 없다.
function amount(v) {
  if (v === null || v === undefined) return "—";
  if (state.market === "US") {
    return "$" + Math.round(v / 1e6).toLocaleString("en-US") + "M";
  }
  return Math.round(v / 1e8).toLocaleString("ko-KR") + "억";
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
    const interval = localStorage.getItem("stockscout.chartInterval");
    if (["D", "W", "M"].includes(interval)) state.chartInterval = interval;
    const period = localStorage.getItem("stockscout.chartPeriod");
    if (period === "all") state.chartPeriod = "all";
    else if (Number(period) > 0) state.chartPeriod = Number(period);
    const mode = localStorage.getItem("stockscout.mode");
    if (mode === "dip" || mode === "sense") state.mode = mode;
    const sense = JSON.parse(localStorage.getItem("stockscout.sense") || "null");
    if (sense && typeof sense === "object") state.sense = { ...state.sense, ...sense };
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
    localStorage.setItem("stockscout.mode", state.mode);
    localStorage.setItem("stockscout.chartPeriod", String(state.chartPeriod));
    localStorage.setItem("stockscout.chartInterval", state.chartInterval);
    localStorage.setItem("stockscout.sense", JSON.stringify(state.sense));
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
  if (sort === "drop" && state.mode === "sense") {
    // 신호순: 돌파(최근 돌파부터) -> 임박(고점에 가까운 순) -> 나머지(고점에 가까운 순)
    const rank = (r) => {
      const s = senseOf(r), b = breakoutOf(r);
      const dist = b ? b.distance_to_high : -9;
      if (s === "BREAKOUT") return [0, b.breakout_days_ago, -dist];
      if (s === "NEAR") return [1, -dist, 0];
      return [2, -dist, 0];
    };
    out.sort((a, b) => {
      const ra = rank(a), rb = rank(b);
      for (let i = 0; i < 3; i++) if (ra[i] !== rb[i]) return ra[i] - rb[i];
      return 0;
    });
  } else if (sort === "drop") {
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

// --- 영탁센스 ---------------------------------------------------------------

// 선택한 신고가 기간(거래일)의 돌파 원재료 (analysis/breakout.compute_breakouts).
function breakoutOf(row) {
  return (row.breakout && row.breakout[state.sense.lookback]) || null;
}

// 돌파·임박·없음. 서버의 analysis/breakout.breakout_signal과 같은 규칙이다.
// 위험 종목(위험 공시·자금 여력·거래대금·자본잠식)은 빼고, 재무 약점은 빼지 않는다 -
// 추세 종목은 재무가 아직 약한 경우가 많다.
function senseOf(row) {
  const cfg = state.snapshot.breakout || {};
  const blocking = cfg.blocking_failures || [];
  if ((row.filter_failures || []).some((f) => blocking.includes(f))) return null;
  const b = breakoutOf(row);
  if (!b) return null;
  if (b.breakout_days_ago !== null && b.breakout_days_ago !== undefined
      && b.breakout_days_ago <= state.sense.days
      && b.breakout_volume_ratio !== null && b.breakout_volume_ratio !== undefined
      && b.breakout_volume_ratio >= state.sense.mult
      && b.holding) return "BREAKOUT";
  if (b.distance_to_high >= -(cfg.near_pct ?? 0.05)
      && b.recent_volume_ratio !== null && b.recent_volume_ratio !== undefined
      && b.recent_volume_ratio >= (cfg.near_volume_multiple ?? 1.2)) return "NEAR";
  return null;
}

const SENSE_BADGES = { BREAKOUT: "돌파", NEAR: "임박" };
const LOOKBACK_LABELS = { 126: "6개월", 252: "1년", 1260: "5년" };

function lookbackChoices() {
  return (state.snapshot && state.snapshot.breakout && state.snapshot.breakout.lookback_choices)
    || [126, 252, 1260];
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
  else if (state.chip === "breakout") rows = rows.filter((r) => senseOf(r) === "BREAKOUT");
  else if (state.chip === "near") rows = rows.filter((r) => senseOf(r) === "NEAR");
  return rows;
}

// --- 렌더 ------------------------------------------------------------

const MARKETS = [["KR", "한국"], ["US", "미국"]];
const MODES = [["dip", "낙폭 우량주"], ["sense", "영탁센스"]];

function renderModeTabs() {
  const nav = $("mode-tabs");
  nav.innerHTML = "";
  for (const [key, label] of MODES) {
    const b = document.createElement("button");
    b.className = "tab" + (state.mode === key ? " on" : "");
    b.textContent = label;
    b.onclick = () => {
      if (state.mode === key) return;
      state.mode = key;
      savePrefs();
      applyMode();
    };
    nav.appendChild(b);
  }
}

// 모드에 맞는 슬라이더·칩·목록·상세로 바꾼다. 데이터는 다시 받지 않는다.
function applyMode() {
  renderModeTabs();
  $("dip-controls").hidden = state.mode === "sense";
  $("sense-controls").hidden = state.mode !== "sense";
  $("sort").options[0].textContent = state.mode === "sense" ? "신호순" : "하락률순";
  if (!state.snapshot) return;
  state.rows = filterAndSort(state.rows, $("search").value.trim(), $("sort").value);
  renderChips();
  renderHitCount();
  renderList();
  if (state.detail && state.selected === state.detail.d.ticker) {
    renderDetail(state.detail.d, state.detail.prices);
  }
}

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
  const defs = state.mode === "sense"
    ? [["all", "전체"], ["breakout", "돌파"], ["near", "임박"], ["watch", "관심"]]
    : [
      ["all", "전체"],
      ...gradeDefs,
      ["passed", "통과"],
      ["excluded", "제외"],
      ["watch", "관심"],
    ];
  if (!defs.some(([key]) => key === state.chip)) state.chip = "all";
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
  if (state.mode === "sense") {
    const n = (s) => state.rows.filter((r) => senseOf(r) === s).length;
    $("hit-count").textContent =
      `${LOOKBACK_LABELS[state.sense.lookback] ?? state.sense.lookback + "일"} 신고가 · `
      + `거래량 ${state.sense.mult.toFixed(1)}배 -> 돌파 ${n("BREAKOUT")} · 임박 ${n("NEAR")}`
      + ` / 전체 ${state.rows.length}종목`;
    return;
  }
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
    const dp = document.createElement("span");
    if (state.mode === "sense") {
      const s = senseOf(row), b = breakoutOf(row);
      if (s) {
        const badge = document.createElement("span");
        badge.className = "sense " + s.toLowerCase();
        badge.textContent = SENSE_BADGES[s];
        nm.append(" ", badge);
      }
      // 돌파면 그날 거래량 배수, 아니면 고점까지 거리
      if (s === "BREAKOUT") {
        dp.className = "dp pos";
        dp.textContent = `거래량 ${b.breakout_volume_ratio.toFixed(1)}배`;
      } else {
        const dist = b ? b.distance_to_high : null;
        dp.className = "dp " + signClass(dist);
        dp.textContent = dist === null ? "—" : `고점 ${pct(dist)}`;
      }
    } else {
      const grade = gradeOf(row);
      if (grade) {
        const badge = document.createElement("span");
        badge.className = "grade " + grade.toLowerCase();
        badge.textContent = GRADE_BADGES[grade];
        nm.append(" ", badge);
      }
      const dd = drawdownOf(row);
      const drop = dd ? dd.drop_from_high : null;
      dp.className = "dp " + signClass(drop);
      dp.textContent = pct(drop);
    }

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

  // UI.pptx 배치: 윗메뉴 / 차트 / 적합점수·판정근거 / 재무추이·공시.
  // 넓은 화면에서는 한 화면에 들어가고, 칸이 넘치면 그 칸 안에서만 스크롤된다.
  panel.innerHTML = `
    <div class="detail-grid">
      <section class="cell cell-top" id="cell-top"></section>
      <section class="cell cell-chart">
        <div class="chart-tools" id="chart-tools"></div>
        <div id="chart-box"><canvas id="price-chart"></canvas></div>
        <input id="chart-scroll" type="range" min="0" max="0" value="0" hidden
               aria-label="차트 구간 이동">
      </section>
      <section class="cell" id="cell-score"><h3 class="cell-title">적합점수</h3></section>
      <section class="cell" id="cell-reason"><h3 class="cell-title">판정근거</h3></section>
      <section class="cell" id="cell-fin"><h3 class="cell-title">재무추이</h3></section>
      <section class="cell" id="cell-filings"><h3 class="cell-title">최근 공시</h3></section>
    </div>`;

  // --- 윗메뉴: 종목 헤더 + 핵심 수치 -------------------------------------
  const top = $("cell-top");
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
  top.appendChild(header);
  header.querySelector("#detail-star").onclick = async () => {
    const row = state.rows.find((r) => r.ticker === d.ticker);
    if (row) { await toggleWatch(row); d.in_watchlist = row.in_watchlist; }
    header.querySelector("#detail-star").textContent = d.in_watchlist ? "★" : "☆";
  };

  const kpis = document.createElement("div");
  kpis.className = "kpis";
  const bo = breakoutOf(d);
  const sense = senseOf(d);
  const lbLabel = LOOKBACK_LABELS[state.sense.lookback] ?? `${state.sense.lookback}일`;
  kpis.innerHTML = state.mode === "sense" ? [
    kpi(money(d.price_current), "현재가"),
    kpi(bo ? pct(bo.distance_to_high) : "—", `${lbLabel} 고점 대비`,
        bo ? signClass(bo.distance_to_high) : "", !bo),
    bo && bo.breakout_volume_ratio !== null && bo.breakout_volume_ratio !== undefined
      ? kpi(`${bo.breakout_volume_ratio.toFixed(1)}배`, "돌파일 거래량 (20일 평균 대비)",
            bo.breakout_volume_ratio >= state.sense.mult ? "pos" : "")
      : kpi("—", "돌파일 거래량", "", true),
    bo && bo.breakout_days_ago !== null && bo.breakout_days_ago !== undefined
      ? kpi(`${bo.breakout_days_ago}거래일 전`, `돌파 ${bo.breakout_date ?? ""}`)
      : kpi("—", "최근 20거래일 돌파 없음", "", true),
    kpi(sense ? SENSE_BADGES[sense] : "신호 없음", "영탁센스",
        sense === "BREAKOUT" ? "pos" : sense === "NEAR" ? "" : "", !sense),
  ].join("") : [
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
  ].join("");
  top.appendChild(kpis);

  // --- 적합점수: 총점 · 축별 막대 · 가치평가 --------------------------------
  const score = $("cell-score");
  const grade = gradeOf(d);
  const total = document.createElement("div");
  total.className = "score-total";
  total.innerHTML = d.score_total === null
    ? `<b class="muted-big">—</b><span>${d.passed_filters ? "재무 데이터 없음" : "제외되어 채점하지 않음"}</span>`
    : `<b>${d.score_total.toFixed(0)}</b><span>/ 100 우량주 점수</span>`
      + (grade ? `<span class="grade ${grade.toLowerCase()}">${GRADE_BADGES[grade]}</span>` : "");
  score.appendChild(total);
  // 점수 분해 (설계서 §11.2). 제외된 종목은 채점하지 않았으므로 막대가 없다.
  if (d.score_detail) {
    const bars = document.createElement("div");
    bars.className = "score-bars";
    bars.title = "점수 분해";
    bars.innerHTML = scoreBars(d.score_detail);
    score.appendChild(bars);
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
  score.appendChild(val);

  // --- 판정근거 -----------------------------------------------------------
  const rat = document.createElement("div");
  rat.className = "rationale";
  const senseLines = state.mode === "sense" ? senseRationale(d, bo, sense, lbLabel) : [];
  rat.innerHTML = [...senseLines, ...d.rationale]
    .map((line) => `<div>${escapeHtml(line)}</div>`).join("");
  $("cell-reason").appendChild(rat);

  // --- 재무추이: 최근 실적 변화 + 연도별 표 ---------------------------------
  const fin = $("cell-fin");
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
    fin.appendChild(rec);
  }
  if (!d.financials.length) {
    const box2 = document.createElement("div");
    box2.innerHTML = state.market === "US" ? EDGAR_GUIDE : DART_GUIDE;
    fin.appendChild(box2);
  } else {
    fin.appendChild(financialsTable(d.financials));
  }

  // --- 최근 공시. 하락 원인과 회복 계기는 사람이 판단한다 - 그 재료를 보인다.
  const fil = $("cell-filings");
  if (d.filings && d.filings.length) {
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
    fil.appendChild(ul);
  } else {
    const none = document.createElement("p");
    none.className = "placeholder small";
    none.textContent = "최근 1년 공시가 없습니다";
    fil.appendChild(none);
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

// 영탁센스 판정 근거. 화면 슬라이더 값으로 다시 쓴다.
function senseRationale(d, bo, sense, lbLabel) {
  const cfg = state.snapshot.breakout || {};
  const blocked = (d.filter_failures || []).filter((f) => (cfg.blocking_failures || []).includes(f));
  const lines = [`[영탁센스] ${sense ? SENSE_BADGES[sense] : "신호 없음"}`
    + ` (${lbLabel} 신고가 · 거래량 ${state.sense.mult.toFixed(1)}배 · ${state.sense.days}거래일 이내)`];
  if (blocked.length) lines.push(`위험 종목이라 보지 않습니다: ${blocked.join(", ")}`);
  if (!bo) {
    lines.push(`${lbLabel} 신고가를 말할 만큼 주가 이력이 없습니다`);
    return lines;
  }
  if (bo.breakout_days_ago !== null && bo.breakout_days_ago !== undefined) {
    lines.push(`돌파: ${bo.breakout_date} (${bo.breakout_days_ago}거래일 전) 직전 고점 `
      + `${money(bo.breakout_level)} 돌파 · 그날 거래량 20일 평균의 `
      + `${bo.breakout_volume_ratio === null ? "—" : bo.breakout_volume_ratio.toFixed(1) + "배"}`);
    lines.push(bo.holding ? `지금도 돌파선 위를 유지 (현재 ${money(d.price_current)})`
                          : `돌파선 아래로 3% 넘게 밀렸습니다 (현재 ${money(d.price_current)})`);
  } else {
    lines.push("최근 20거래일 안에 신고가 돌파가 없습니다");
  }
  lines.push(`${lbLabel} 고점 ${money(bo.high)} 대비 ${pct(bo.distance_to_high)}`
    + ` · 최근 5일 거래량 ${bo.recent_volume_ratio === null ? "—" : bo.recent_volume_ratio.toFixed(1) + "배"}`);
  lines.push("— 아래는 낙폭 우량주 기준의 판정 근거 —");
  return lines;
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

// 재무추이 칸은 화면 절반 폭이라 최근 몇 해만 보인다 (수집은 10년치).
const FIN_YEARS_SHOWN = 5;

function financialsTable(allRows) {
  const rows = allRows.slice(-FIN_YEARS_SHOWN);
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

// 캔들 색: 국내 관례(상승 빨강 · 하락 파랑).
const CANDLE_UP = "#e53e3e";
const CANDLE_DOWN = "#3182ce";

function drawChart(p) {
  if (state.chart) state.chart.destroy();

  // 기준선은 서버가 준 값을 그대로 쓴다. 여기서 다시 평균을 내면
  // 창 정의나 반올림이 갈려 차트와 판정 근거가 어긋난다.
  const dd = state.detail ? drawdownOf(state.detail.d) : null;
  const bo = state.detail && state.mode === "sense" ? breakoutOf(state.detail.d) : null;
  const lbLabel = LOOKBACK_LABELS[state.sense.lookback] ?? `${state.sense.lookback}일`;
  const refs = state.mode === "sense" ? [
    [`돌파선`, bo ? bo.breakout_level : null, "#d53f8c"],
    [`${lbLabel} 고점`, bo ? bo.high : null, "#e53e3e"],
  ] : [
    [`${state.months}개월 고점`, dd ? dd.high : null, "#d69e2e"],
    ["5년 고점", p.high_5y, "#e53e3e"],
    ["5년 평균", p.avg_price_5y, "#805ad5"],
    ["3년 평균", p.avg_price_3y, "#38a169"],
    ["1년 평균", p.avg_price_1y, "#dd6b20"],
  ].filter(([, v]) => v !== null && v !== undefined);

  // 일봉 -> 주봉·월봉. 시가·고가·저가가 없는 옛 데이터는 종가선으로 그린다.
  // 거래정지일처럼 시가·고가·저가가 빈 봉이 섞여 있어도 대부분(90%) 있으면
  // 캔들로 그리고, 빈 봉은 종가로 채운다 - 하루 때문에 차트 전체가 선이 되면 안 된다.
  const raw = aggregateBars(p.series, state.chartInterval);
  const withOhlc = raw.filter((b) => b.open !== null && b.open !== undefined).length;
  const hasOhlc = raw.length > 0 && withOhlc >= raw.length * 0.9;
  const bars = raw.map((b) => (b.open !== null && b.open !== undefined ? b
    : { ...b, open: b.close, high: b.close, low: b.close }));
  const labels = bars.map((b) => b.date);
  const closes = bars.map((b) => b.close);
  const upColors = bars.map((b) => (hasOhlc && b.close < b.open ? CANDLE_DOWN : CANDLE_UP));

  // 0번은 항상 종가다. 안내창·가로선·가격축 맞춤이 이 데이터셋을 본다.
  // 캔들일 때는 선을 감추고 막대(몸통·꼬리)로 그린다.
  const datasets = [{
    label: "종가",
    data: closes,
    borderColor: hasOhlc ? "rgba(0,0,0,0)" : "#2b6cb0",
    borderWidth: hasOhlc ? 0 : 1.4,
    pointRadius: 0,
    tension: 0,
    yAxisID: "y",
  }];
  if (hasOhlc) {
    datasets.push({
      type: "bar", label: "꼬리", candle: "wick",
      data: bars.map((b) => [b.low, b.high]),
      backgroundColor: upColors, borderWidth: 0,
      // grouped: false - 꼬리·몸통·거래량을 한 칸에 나란히 놓지 않고 겹쳐 그린다.
      // 기본값(true)이면 꼬리가 몸통 옆에 붙는다.
      grouped: false, barPercentage: 0.18, categoryPercentage: 1.0, yAxisID: "y", order: 2,
    });
    datasets.push({
      type: "bar", label: "봉", candle: "body",
      data: bars.map((b) => [Math.min(b.open, b.close), Math.max(b.open, b.close)
                                                       + (b.open === b.close ? 1e-9 : 0)]),
      backgroundColor: upColors, borderWidth: 0,
      grouped: false, barPercentage: 0.8, categoryPercentage: 1.0, yAxisID: "y", order: 1,
    });
  }
  for (const [label, value, color] of refs) {
    datasets.push({
      label: `${label} ${money(value)}`,
      data: labels.map(() => value),
      borderColor: color,
      borderWidth: 1,
      borderDash: [5, 4],
      pointRadius: 0,
      yAxisID: "y",
    });
  }
  // 거래량 막대. 아래쪽 4분의 1만 쓰도록 축 최대값을 키운다 (첨부 차트처럼).
  const volumes = bars.map((b) => b.volume ?? null);
  const maxVol = Math.max(0, ...volumes.filter((v) => v !== null));
  if (maxVol > 0) {
    datasets.push({
      type: "bar",
      label: "거래량",
      data: volumes,
      backgroundColor: hasOhlc ? upColors.map((c) => c + "55") : "rgba(113, 128, 150, 0.35)",
      borderWidth: 0,
      grouped: false, barPercentage: 0.8, categoryPercentage: 1.0,
      yAxisID: "yv",
      order: 9,
    });
  }

  // 확대 전 전체 데이터. 확대·이동은 이 배열을 잘라 보여 준다.
  state.chartFull = {
    labels, bars, hasOhlc,
    data: datasets.map((ds) => ds.data),
    colors: datasets.map((ds) => (Array.isArray(ds.backgroundColor) ? ds.backgroundColor : null)),
  };
  state.drag = null;

  state.chart = new Chart($("price-chart"), {
    type: "line",
    data: { labels, datasets },
    plugins: [crosshairPlugin],
    options: {
      // 애니메이션을 끈다. 1,200점짜리 선 6개를 애니메이션할 이유가 없고,
      // 화면 없는 브라우저(검증 스크립트)에서는 그리다 멈춰 선이 중간에서 끊겼다.
      animation: false,
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { boxWidth: 12, font: { size: 11 },
                            filter: (item, data) => !data.datasets[item.datasetIndex].candle
                              && !(hasOhlc && item.datasetIndex === 0) } },
        // 안내창에는 시가·고가·저가·종가와 거래량만. 기준선은 범례에 값이 이미 있다.
        tooltip: {
          filter: (item) => item.datasetIndex === 0 || item.dataset.yAxisID === "yv",
          callbacks: { label: (item) => chartTooltipLabel(item) },
        },
      },
      scales: {
        x: { stacked: false, ticks: { maxTicksLimit: 8, font: { size: 10 } },
             grid: { display: false } },
        y: { ticks: { font: { size: 10 }, callback: (v) => money(v) } },
        yv: { display: false, beginAtZero: true, max: maxVol * 4 || undefined,
              grid: { display: false } },
      },
    },
  });

  const canvas = $("price-chart");
  canvas.onmousedown = (e) => onChartMouseDown(e);
  canvas.onmousemove = (e) => onChartMouseMove(e);
  canvas.onmouseup = (e) => onChartMouseUp(e);
  canvas.ondblclick = () => onChartDoubleClick();
  canvas.onmouseleave = () => onChartMouseLeave();
  canvas.onwheel = (e) => onChartWheel(e);
  $("chart-scroll").oninput = () => onChartScroll();
  renderChartTools();
  const [i0, i1] = periodRange(labels, state.chartPeriod);
  applyChartRange(i0, i1);
}

// 안내창 한 줄. 종가 데이터셋은 캔들이면 시가·고가·저가·종가를 함께 보인다.
function chartTooltipLabel(item) {
  if (item.dataset.yAxisID === "yv") {
    return `거래량 ${item.raw === null ? "—" : Number(item.raw).toLocaleString("ko-KR")}`;
  }
  const full = state.chartFull;
  const b = full && state.chartView ? full.bars[state.chartView[0] + item.dataIndex] : null;
  if (b && full.hasOhlc) {
    return `시 ${money(b.open)} · 고 ${money(b.high)} · 저 ${money(b.low)} · 종 ${money(b.close)}`;
  }
  return `종가 ${money(item.raw)}`;
}

// --- 봉 주기 -------------------------------------------------------------

const CHART_INTERVALS = [["D", "일봉"], ["W", "주봉"], ["M", "월봉"]];

// 날짜(YYYY-MM-DD)가 속한 묶음의 열쇠. 주봉은 그 주 월요일(UTC로 계산 -
// 연도로 자르면 연말연시 한 주가 둘로 갈린다), 월봉은 YYYY-MM.
function periodKey(dateText, interval) {
  if (interval === "M") return dateText.slice(0, 7);
  const [y, m, d] = dateText.split("-").map(Number);
  const t = Date.UTC(y, m - 1, d);
  const weekday = (new Date(t).getUTCDay() + 6) % 7;   // 월요일 = 0
  return new Date(t - weekday * 86400000).toISOString().slice(0, 10);
}

// 일봉을 주봉·월봉으로 묶는다. 시가 = 첫날 시가, 고가 = 최고, 저가 = 최저,
// 종가 = 마지막 날 종가, 거래량 = 합. 묶음의 날짜는 그 묶음의 마지막 거래일이다.
// 시가·고가·저가가 없는 날은 종가로 대신한다(그 묶음의 봉이 덜 정확해질 뿐이다).
function aggregateBars(series, interval) {
  if (interval === "D") return series;
  const out = [];
  let key = null, cur = null;
  for (const x of series) {
    const k = periodKey(x.date, interval);
    const hi = x.high ?? x.close, lo = x.low ?? x.close;
    if (k !== key) {
      if (cur) out.push(cur);
      key = k;
      cur = { date: x.date, open: x.open ?? x.close, high: hi, low: lo,
              close: x.close, volume: x.volume ?? null };
    } else {
      cur.date = x.date;
      cur.high = Math.max(cur.high, hi);
      cur.low = Math.min(cur.low, lo);
      cur.close = x.close;
      if (x.volume !== null && x.volume !== undefined) cur.volume = (cur.volume ?? 0) + x.volume;
    }
  }
  if (cur) out.push(cur);
  return out;
}

function onChartInterval(key) {
  if (state.chartInterval === key || !state.detail) return;
  state.chartInterval = key;
  savePrefs();
  drawChart(state.detail.prices);
}

// --- 차트 확대 ------------------------------------------------------------

const CHART_PERIODS = [[1, "1개월"], [3, "3개월"], [6, "6개월"], [12, "1년"],
                       [36, "3년"], ["all", "전체"]];
const MIN_ZOOM_POINTS = 5;   // 드래그가 이보다 좁으면 확대하지 않는다 (실수 클릭)

// 날짜 배열에서 마지막 날짜로부터 months개월 안의 [첫 인덱스, 끝 인덱스].
// "all"이거나 데이터가 그보다 짧으면 전체.
function periodRange(dates, months) {
  const end = dates.length - 1;
  if (months === "all" || end < 0) return [0, Math.max(end, 0)];
  // UTC로만 계산한다. 로컬 자정 -> toISOString은 한국 시간(UTC+9)에서 하루 당겨진다.
  const [y, m, d] = dates[end].split("-").map(Number);
  const cut = new Date(Date.UTC(y, m - 1 - months, d)).toISOString().slice(0, 10);
  const start = dates.findIndex((d) => d >= cut);
  return [Math.max(start, 0), end];
}

// 보이는 구간 [i0, i1]을 폭은 그대로 shift만큼 옮긴다. 양 끝에서 멈춘다.
function panRange(view, total, shift) {
  const len = view[1] - view[0];
  const i0 = Math.min(Math.max(view[0] + shift, 0), Math.max(total - 1 - len, 0));
  return [i0, i0 + len];
}

// 전체 데이터에서 [i0, i1]만 보이게 한다. 가격축은 보이는 구간의 저가·고가
// (캔들이 아니면 종가)에 맞추고, 거래량축은 보이는 구간의 최대값으로 맞춘다.
function applyChartRange(i0, i1) {
  const chart = state.chart, full = state.chartFull;
  if (!chart || !full) return;
  state.chartView = [i0, i1];
  chart.data.labels = full.labels.slice(i0, i1 + 1);
  chart.data.datasets.forEach((ds, k) => {
    ds.data = full.data[k].slice(i0, i1 + 1);
    if (full.colors[k]) ds.backgroundColor = full.colors[k].slice(i0, i1 + 1);
  });
  // 5년 고점 같은 먼 기준선까지 담으면 확대해도 가격선이 납작해진다 - 범위 밖
  // 기준선은 잘린다.
  const visible = full.bars.slice(i0, i1 + 1);
  const highs = visible.map((b) => (full.hasOhlc ? b.high : b.close));
  const lows = visible.map((b) => (full.hasOhlc ? b.low : b.close));
  if (highs.length) {
    const lo = Math.min(...lows), hi = Math.max(...highs);
    const pad = (hi - lo) * 0.08 || hi * 0.02;
    chart.options.scales.y.min = lo - pad;
    chart.options.scales.y.max = hi + pad;
  }
  const vol = chart.data.datasets.find((ds) => ds.yAxisID === "yv");
  if (vol) {
    const maxVol = Math.max(0, ...vol.data.filter((v) => v !== null));
    chart.options.scales.yv.max = maxVol * 4 || undefined;
  }
  chart.update("none");
  renderChartTools();
  syncChartScroll();
}

// 스크롤바: 보이는 구간이 전체보다 좁을 때만 보인다. 값 = 보이는 구간의 시작.
function syncChartScroll() {
  const bar = $("chart-scroll"), full = state.chartFull, view = state.chartView;
  if (!bar || !full || !view) return;
  const len = view[1] - view[0];
  const room = full.labels.length - 1 - len;
  bar.hidden = room <= 0;
  bar.max = String(Math.max(room, 0));
  bar.value = String(view[0]);
}

function onChartScroll() {
  const full = state.chartFull, view = state.chartView;
  if (!full || !view) return;
  const len = view[1] - view[0];
  const i0 = Number($("chart-scroll").value);
  applyChartRange(i0, i0 + len);
}

// 휠: 확대해 있을 때만 앞뒤로 옮긴다(보이는 폭의 10%씩). 전체를 보고 있으면
// 페이지 스크롤을 막지 않는다.
function onChartWheel(e) {
  const full = state.chartFull, view = state.chartView;
  if (!full || !view || view[1] - view[0] + 1 >= full.labels.length) return;
  e.preventDefault();
  const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
  const step = Math.max(1, Math.round((view[1] - view[0]) * 0.1));
  const [i0, i1] = panRange(view, full.labels.length, Math.sign(delta) * step);
  applyChartRange(i0, i1);
}

function renderChartTools() {
  const tools = $("chart-tools");
  if (!tools) return;
  const full = state.chartFull, view = state.chartView;
  const zoomed = full && view
    && JSON.stringify(view) !== JSON.stringify(periodRange(full.labels, state.chartPeriod));
  tools.innerHTML = "";
  for (const [key, label] of CHART_INTERVALS) {
    const b = document.createElement("button");
    b.className = "ptab interval" + (state.chartInterval === key ? " on" : "");
    b.textContent = label;
    b.onclick = () => onChartInterval(key);
    tools.appendChild(b);
  }
  const sep = document.createElement("span");
  sep.className = "chart-sep";
  tools.appendChild(sep);
  for (const [key, label] of CHART_PERIODS) {
    const b = document.createElement("button");
    b.className = "ptab" + (!zoomed && state.chartPeriod === key ? " on" : "");
    b.textContent = label;
    b.onclick = () => onChartPeriod(key);
    tools.appendChild(b);
  }
  const hint = document.createElement("span");
  hint.className = "chart-hint";
  hint.textContent = zoomed && view
    ? `${full.labels[view[0]]} ~ ${full.labels[view[1]]} · 휠·아래 막대로 이동 · 더블클릭하면 원래대로`
    : "드래그하면 그 구간을 확대합니다";
  tools.appendChild(hint);
}

function onChartPeriod(key) {
  state.chartPeriod = key;
  savePrefs();
  const [i0, i1] = periodRange(state.chartFull.labels, key);
  applyChartRange(i0, i1);
}

// 마우스 x -> 보이는 구간 안의 인덱스 (0 ~ 보이는 개수-1)
function chartIndexAt(e) {
  const chart = state.chart;
  const rect = chart.canvas.getBoundingClientRect();
  const x = Math.min(Math.max(e.clientX - rect.left, chart.chartArea.left), chart.chartArea.right);
  const idx = Math.round(chart.scales.x.getValueForPixel(x));
  return Math.min(Math.max(idx, 0), chart.data.labels.length - 1);
}

function onChartMouseDown(e) {
  if (!state.chart) return;
  const x = e.clientX - state.chart.canvas.getBoundingClientRect().left;
  state.drag = { x0: x, x1: x, i0: chartIndexAt(e) };
}

// Chart.js의 이벤트 처리 뒤에 불린다(핸들러를 차트 생성 뒤에 달았다). 가로선은
// 이벤트만으로는 다시 그려지지 않아(animation: false) 여기서 직접 그린다.
function onChartMouseMove(e) {
  if (!state.chart) return;
  if (state.drag) state.drag.x1 = e.clientX - state.chart.canvas.getBoundingClientRect().left;
  state.chart.draw();
}

function onChartMouseLeave() {
  if (!state.chart) return;
  state.chart.$cross = null;
  state.chart.draw();
}

function onChartMouseUp(e) {
  const drag = state.drag;
  state.drag = null;
  if (!drag || !state.chartView) return;
  const a = Math.min(drag.i0, chartIndexAt(e)), b = Math.max(drag.i0, chartIndexAt(e));
  if (b - a + 1 < MIN_ZOOM_POINTS) { state.chart.draw(); return; }
  const base = state.chartView[0];
  applyChartRange(base + a, base + b);
}

function onChartDoubleClick() {
  if (!state.chartFull) return;
  const [i0, i1] = periodRange(state.chartFull.labels, state.chartPeriod);
  applyChartRange(i0, i1);
}

// --- 마우스 가로선 --------------------------------------------------------

// 마우스 높이에 가로 점선과 그 높이의 가격을, 드래그 중이면 선택 구간을 그린다.
const crosshairPlugin = {
  id: "crosshair",
  afterEvent(chart, args) {
    const e = args.event;
    if (e.type === "mousemove") chart.$cross = { x: e.x, y: e.y };
    else if (e.type === "mouseout") chart.$cross = null;
  },
  afterDraw(chart) { crosshairAfterDraw(chart); },
};

function crosshairAfterDraw(chart) {
  const { ctx, chartArea: area } = chart;
  ctx.save();
  if (state.drag && state.chart === chart) {
    const x0 = Math.max(Math.min(state.drag.x0, state.drag.x1), area.left);
    const x1 = Math.min(Math.max(state.drag.x0, state.drag.x1), area.right);
    ctx.fillStyle = "rgba(49, 130, 206, 0.12)";
    ctx.fillRect(x0, area.top, x1 - x0, area.bottom - area.top);
  }
  const c = chart.$cross;
  if (c && c.x >= area.left && c.x <= area.right && c.y >= area.top && c.y <= area.bottom) {
    ctx.strokeStyle = "rgba(45, 55, 72, 0.55)";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(area.left, c.y);
    ctx.lineTo(area.right, c.y);
    ctx.stroke();
    ctx.setLineDash([]);
    // 오른쪽 끝에 그 높이의 가격
    const label = money(chart.scales.y.getValueForPixel(c.y));
    ctx.font = "11px 'Malgun Gothic', sans-serif";
    const w = ctx.measureText(label).width + 10;
    ctx.fillStyle = "#2d3748";
    ctx.fillRect(area.right - w, c.y - 9, w, 18);
    ctx.fillStyle = "#fff";
    ctx.textBaseline = "middle";
    ctx.fillText(label, area.right - w + 5, c.y);
  }
  ctx.restore();
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

  const lbs = lookbackChoices();
  if (!lbs.includes(state.sense.lookback)) state.sense.lookback = lbs[Math.min(1, lbs.length - 1)];
  $("sense-lookback").max = String(lbs.length - 1);
  $("sense-lookback").value = String(lbs.indexOf(state.sense.lookback));
  $("sense-lookback-value").textContent = LOOKBACK_LABELS[state.sense.lookback] ?? `${state.sense.lookback}일`;
  $("sense-mult").value = String(state.sense.mult);
  $("sense-mult-value").textContent = `${state.sense.mult.toFixed(1)}배`;
  $("sense-days").value = String(state.sense.days);
  $("sense-days-value").textContent = `${state.sense.days}거래일`;
}

// 영탁센스 슬라이더가 바뀌면 서버 왕복 없이 다시 판정·정렬하고 열린 상세를 다시 그린다.
function onSenseChanged() {
  savePrefs();
  syncSliders();
  state.rows = filterAndSort(state.rows, $("search").value.trim(), $("sort").value);
  renderHitCount();
  renderList();
  if (state.detail && state.selected === state.detail.d.ticker) {
    renderDetail(state.detail.d, state.detail.prices);
  }
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

  $("sense-lookback").oninput = () => {
    state.sense.lookback = lookbackChoices()[Number($("sense-lookback").value)];
    onSenseChanged();
  };
  $("sense-mult").oninput = () => {
    state.sense.mult = Number($("sense-mult").value);
    onSenseChanged();
  };
  $("sense-days").oninput = () => {
    state.sense.days = Number($("sense-days").value);
    onSenseChanged();
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
  const bcfg = state.snapshot.breakout || {};
  if (state.sense.lookback === null) state.sense.lookback = bcfg.lookback ?? 252;
  if (state.sense.mult === null) state.sense.mult = bcfg.volume_multiple ?? 2.0;
  if (state.sense.days === null) state.sense.days = bcfg.recent_days ?? 10;
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
  applyMode();
}

init();
