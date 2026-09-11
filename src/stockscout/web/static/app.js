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

// 지금은 종목명과 한 줄 요약만 보여준다. 우측 상세 리포트(차트,
// 재무 표, 판정 근거)는 별도로 채운다.
function selectTicker(ticker) {
  state.selected = ticker;
  renderList();

  const panel = $("detail-panel");
  const row = state.rows.find((r) => r.ticker === ticker);
  if (!row) {
    panel.innerHTML = '<p class="placeholder">왼쪽에서 종목을 선택하세요.</p>';
    return;
  }

  panel.innerHTML = "";
  const h2 = document.createElement("h2");
  h2.textContent = `${row.name} (${row.ticker})`;
  const summary = document.createElement("p");
  summary.className = "muted";
  summary.textContent =
    `현재가 ${won(row.price_current)}원 · 5년 평균 대비 ${pct(row.drop_vs_avg_5y)}`;
  panel.append(h2, summary);
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
