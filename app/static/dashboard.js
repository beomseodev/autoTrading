"use strict";

const STORAGE_KEY = "portfolio-dashboard-v1";
const COLORS = ["#0b8f76", "#6487d5", "#a68bce", "#e5b364", "#6cabb5", "#bd8696", "#99a7b5"];
const $ = selector => document.querySelector(selector);
const emptyState = () => ({version: 1, portfolio: {positions: [], cashKRW: 0, cashUSD: 0, baseCurrency: "KRW"}, goal: null, portfolioSavedAt: null});
let state = emptyState();
let invalidStoredValue = null;
let snapshot = null;
let portfolioSequence = 0;
let goalSequence = 0;
let marketSequence = 0;
let portfolioBusy = false;
let marketBusy = false;
let projectionView = null;

function esc(value) {
  return String(value).replace(/[&<>"']/g, char => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[char]);
}
function number(value, digits = 2) {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : new Intl.NumberFormat("ko-KR", {maximumFractionDigits: digits}).format(value);
}
function money(value, currency = state.portfolio.baseCurrency) {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : new Intl.NumberFormat("ko-KR", {style: "currency", currency, currencyDisplay: "narrowSymbol", maximumFractionDigits: currency === "KRW" ? 0 : 2}).format(value);
}
function pct(value) { return value === null || value === undefined ? "—" : `${value > 0 ? "+" : ""}${number(value)}%`; }
function tone(value) { return value > 0 ? "positive" : value < 0 ? "negative" : ""; }
function message(text) { $("#app-status").textContent = text; }
function finiteRange(value, min, max) { return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max; }

function validatePortfolio(value) {
  if (!value || !["KRW", "USD"].includes(value.baseCurrency) || !Array.isArray(value.positions) || value.positions.length > 30 ||
      !finiteRange(value.cashKRW, 0, 1e15) || !finiteRange(value.cashUSD, 0, 1e12)) throw new Error("보유 자산의 형식 또는 금액을 확인해주세요.");
  const symbols = new Set();
  for (const item of value.positions) {
    if (!item || typeof item.ticker !== "string" || !/^[A-Z][A-Z0-9.\-]{0,9}$/.test(item.ticker) ||
        !finiteRange(item.quantity, Number.MIN_VALUE, 1e9) || !finiteRange(item.averageCost, 0, 1e9) || symbols.has(item.ticker)) throw new Error("종목 코드·수량·평균 매입가를 확인해주세요. 같은 종목은 한 행에 합쳐 입력합니다.");
    symbols.add(item.ticker);
  }
}
function validateGoal(value) {
  if (!value || !["KRW", "USD"].includes(value.currency) || !finiteRange(value.targetAmount, .01, 1e15) ||
      !finiteRange(value.monthlyContribution, 0, 1e12) || !finiteRange(value.annualReturnPct, -50, 50)) throw new Error("목표 금액·월 투자금·가정 수익률을 확인해주세요.");
}
function saveState(next) {
  if (invalidStoredValue !== null) localStorage.setItem(STORAGE_KEY + "-recovery", invalidStoredValue);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  invalidStoredValue = null;
  state = next;
}
function restoreState() {
  let raw;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return;
    const value = JSON.parse(raw);
    if (value.version !== 1) throw new Error("저장 버전을 확인할 수 없습니다.");
    validatePortfolio(value.portfolio);
    if (value.goal !== null) validateGoal(value.goal);
    if (value.portfolioSavedAt !== null && (typeof value.portfolioSavedAt !== "string" || Number.isNaN(Date.parse(value.portfolioSavedAt)))) throw new Error("저장 날짜가 올바르지 않습니다.");
    state = value;
  } catch {
    invalidStoredValue = raw ?? null;
    message("저장된 정보를 읽지 못했습니다. 원본은 유지됩니다. 새로 저장할 때 기존 원본을 복구용으로 보관합니다. 저장소 접근이 차단된 경우 브라우저 설정을 확인해주세요.");
  }
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  const result = await response.json();
  if (response.status === 401) { location.assign("/login"); throw new Error("다시 로그인해주세요."); }
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "입력값을 확인하거나 잠시 후 다시 시도해주세요.");
  return result;
}
function updateRefreshButton() { $("#refresh-btn").disabled = portfolioBusy || marketBusy; }

function renderMarket(data) {
  $("#market-strip").innerHTML = data.markets.map(item => `<article class="market-item"><span>${esc(item.label)}</span><strong>${item.available ? number(item.value) : "—"}</strong><small class="${item.available ? tone(item.changePct) : "muted"}">${item.available ? pct(item.changePct) : "조회 불가"}</small><a class="market-meta" href="${esc(item.source)}" target="_blank" rel="noopener noreferrer">${item.available ? `${esc(item.asOf)} · Yahoo Finance ↗` : "출처 확인 ↗"}</a></article>`).join("");
  $("#macro-list").innerHTML = data.macro.map(item => `<article class="macro-item"><div class="macro-top"><a href="${esc(item.source)}" title="${esc(item.description)}" target="_blank" rel="noopener noreferrer">${esc(item.label)} ↗</a><strong>${item.available ? `${number(item.value)}%` : "—"}</strong></div><div class="macro-bottom"><span>${item.available ? `관측 ${esc(item.asOf)}` : "데이터 조회 불가"}</span><span>${item.available ? `${item.change > 0 ? "+" : ""}${number(item.change)}%p` : "잠시 후 새로고침"}</span></div><p class="macro-meaning">${esc(item.description)}</p></article>`).join("");
}
async function loadMarkets() {
  const sequence = ++marketSequence;
  marketBusy = true;
  updateRefreshButton();
  try {
    const data = await api("/api/dashboard/markets");
    if (sequence === marketSequence) renderMarket(data);
  } catch {
    if (sequence === marketSequence) {
      $("#market-strip").querySelectorAll("small").forEach(item => { item.textContent = "조회 실패 · 재시도"; });
      $("#macro-list").textContent = "지표를 불러오지 못했습니다. 새로고침으로 다시 시도해주세요.";
    }
  } finally {
    if (sequence === marketSequence) {
      marketBusy = false;
      $("#market-strip").setAttribute("aria-busy", "false");
      $("#macro-list").setAttribute("aria-busy", "false");
      updateRefreshButton();
    }
  }
}

function setMetric(selector, value, amount) {
  $(selector).textContent = value;
  $(selector).className = tone(amount);
}
function renderAllocation(data) {
  if (!data.complete || !data.hasAssets || !data.totalValue) {
    $("#allocation-visual").innerHTML = `<div class="empty-donut"><span>${data.complete ? "내 자산 등록" : "시세 확인 필요"}</span></div>`;
    $("#allocation-legend").innerHTML = `<p class="empty-note">${data.complete ? "종목과 현금을 등록해 자산 구성을 확인하세요." : "일부 시세가 누락되어 비중을 계산하지 않았습니다."}</p>`;
    return;
  }
  const items = data.holdings.map(item => ({label: item.ticker, value: item.value}));
  if (data.cashValue > 0) items.push({label: "현금", value: data.cashValue});
  items.sort((a, b) => b.value - a.value);
  let offset = 0;
  const stops = items.map((item, index) => { const start = offset; offset += item.value / data.totalValue * 100; return `${COLORS[index % COLORS.length]} ${start}% ${offset}%`; });
  $("#allocation-visual").innerHTML = `<div class="allocation-donut" aria-hidden="true" style="background:conic-gradient(${stops.join(",")})"><div class="donut-hole">보유 자산<b>${items.length}</b></div></div>`;
  $("#allocation-legend").innerHTML = items.map((item, index) => `<div class="allocation-line"><i style="background:${COLORS[index % COLORS.length]}"></i><span>${esc(item.label)}</span><b>${number(item.value / data.totalValue * 100)}%</b></div>`).join("");
}
function renderPortfolio(data) {
  const registered = data.hasAssets || Boolean(state.portfolioSavedAt);
  $("#total-assets").textContent = registered && data.complete ? money(data.totalValue) : "—";
  $("#total-caption").textContent = !registered ? "보유 자산을 등록해주세요" : data.complete ? `${data.baseCurrency} 기준 · 주식 + 현금` : "일부 시세 누락 · 총액 계산 보류";
  setMetric("#unrealized-profit", registered ? money(data.unrealizedProfit) : "—", data.unrealizedProfit);
  $("#profit-caption").textContent = registered && data.complete ? `${pct(data.unrealizedProfitPct)} · 환차손익 제외` : "평균 매입가 기준 · 환차손익 제외";
  setMetric("#daily-change", registered ? money(data.dayChange) : "—", data.dayChange);
  $("#daily-caption").textContent = registered && data.dayChange !== null ? `${pct(data.dayChangePct)} · 현재 수량 기준` : "현재 수량 기준 · 관측일 불일치 시 미제공";
  $("#cash-value").textContent = registered ? money(data.cashValue) : "—";
  $("#cash-caption").textContent = `₩${number(state.portfolio.cashKRW, 0)} + $${number(state.portfolio.cashUSD)}`;
  $("#holding-count").textContent = `${data.holdings.length}개 종목`;
  $("#holdings-description").textContent = state.portfolioSavedAt ? `직접 입력 · 수정 ${new Date(state.portfolioSavedAt).toLocaleDateString("ko-KR")} · 가격·매입가 USD` : "미국 주식 · ETF / 직접 입력 / 가격·매입가 USD";
  if (data.holdings.length) {
    $("#holdings-body").innerHTML = data.holdings.map(item => `<tr><td><strong>${esc(item.ticker)}</strong><small>${item.available ? esc(item.asOf) : "시세 미확인"}</small></td><td class="number">${number(item.quantity, 6)}</td><td class="number">${item.available ? money(item.price, "USD") : "—"}<small>평균 ${money(item.averageCost, "USD")}</small></td><td class="number">${money(item.value)}</td><td class="number ${item.available ? tone(item.profitPct) : "muted"}">${item.available ? pct(item.profitPct) : "조회 불가"}</td><td class="number">${item.weightPct === null ? "—" : `${number(item.weightPct)}%`}</td></tr>`).join("");
  } else {
    $("#holdings-body").innerHTML = `<tr><td colspan="6" class="empty-table"><strong>${registered ? "등록한 주식이 없습니다." : "아직 등록한 자산이 없습니다."}</strong><p>종목·보유 수량·평균 매입가를 입력하면 실제 보유 현황이 표시됩니다.</p><button class="button primary" data-edit-portfolio type="button">보유 자산 등록</button></td></tr>`;
  }
  const fxNote = data.fx ? `환율 ${number(data.fx.value)}원/USD (${data.fx.asOf})` : "환율 조회 불가";
  $("#valuation-note").textContent = `Yahoo 최근 일봉 · ${fxNote}. USD 평가손익을 현재 환율로 환산하며 취득 환율에 따른 환차손익·실현손익·배당 누계는 제외합니다.`;
  $("#updated-at").textContent = `조회 ${new Date(data.fetchedAt).toLocaleTimeString("ko-KR", {hour: "2-digit", minute: "2-digit"})}`;
  renderAllocation(data);
}
async function loadPortfolio() {
  const sequence = ++portfolioSequence;
  ++goalSequence;
  portfolioBusy = true;
  snapshot = null;
  $("#asset-summary").setAttribute("aria-busy", "true");
  $("#updated-at").textContent = "보유 자산 평가 중";
  showGoalPending("보유 자산을 평가한 뒤 목표 경로를 계산합니다.");
  updateRefreshButton();
  try {
    const data = await api("/api/dashboard/portfolio", state.portfolio);
    if (sequence !== portfolioSequence) return;
    snapshot = data;
    renderPortfolio(data);
    await loadGoal();
  } catch (error) {
    if (sequence === portfolioSequence) {
      ["#total-assets", "#unrealized-profit", "#daily-change", "#cash-value"].forEach(selector => { $(selector).textContent = "—"; });
      $("#total-caption").textContent = "시세 조회 실패 · 새로고침해주세요";
      message(error.message || "자산 평가에 실패했습니다.");
      showGoalPending("자산 평가를 완료한 뒤 계산할 수 있습니다.");
    }
  } finally {
    if (sequence === portfolioSequence) {
      portfolioBusy = false;
      $("#asset-summary").setAttribute("aria-busy", "false");
      updateRefreshButton();
    }
  }
}

function showGoalPending(text) {
  projectionView = null;
  $("#goal-overview").innerHTML = `<div><span class="muted">가정상 목표 도달 시점</span><strong>${state.goal ? "계산 대기" : "목표를 설정해주세요"}</strong><p>${esc(text)}</p></div><div class="goal-amount"><span>목표 금액</span><b>${state.goal ? money(state.goal.targetAmount, state.goal.currency) : "—"}</b></div>`;
  $("#projection-chart").classList.add("empty-chart");
  $("#projection-chart").textContent = text;
  $("#goal-scenarios").textContent = "";
  $("#goal-progress").setAttribute("aria-valuenow", "0");
  $("#goal-progress span").style.width = "0%";
  $("#progress-labels").innerHTML = `<span>현재 자산 확인 필요</span><span>${state.goal ? money(state.goal.targetAmount, state.goal.currency) : "목표 미설정"}</span>`;
}
function goalInitialAmount() {
  if (!snapshot || !snapshot.complete || (!snapshot.hasAssets && !state.portfolioSavedAt)) return null;
  if (snapshot.baseCurrency === state.goal.currency) return snapshot.totalValue;
  if (!snapshot.fx) return null;
  return state.goal.currency === "USD" ? snapshot.totalValue / snapshot.fx.value : snapshot.totalValue * snapshot.fx.value;
}
function goalDate(scenario) {
  if (scenario.monthsToGoal === null) return "60년 내 미도달";
  if (scenario.monthsToGoal === 0) return "이미 목표 달성";
  const [year, month] = scenario.reachedDate.split("-");
  return `${year}년 ${Number(month)}월`;
}
function renderProjection(data, initial) {
  projectionView = {data, initial};
  const goal = state.goal;
  const base = data.scenarios[1];
  $("#goal-overview").innerHTML = `<div><span class="muted">입력 가정상 목표 도달 시점</span><strong>${goalDate(base)}</strong><p>매월 ${money(goal.monthlyContribution, goal.currency)} · 연 ${number(goal.annualReturnPct)}% 가정</p></div><div class="goal-amount"><span>목표 금액 · ${goal.currency}</span><b>${money(goal.targetAmount, goal.currency)}</b></div>`;
  const progress = Math.min(100, Math.max(0, initial / goal.targetAmount * 100));
  $("#goal-progress").setAttribute("aria-valuenow", progress.toFixed(2));
  $("#goal-progress span").style.width = `${progress}%`;
  $("#progress-labels").innerHTML = `<span>현재 ${money(initial, goal.currency)}</span><span>${number(progress, 1)}% 달성</span>`;
  $("#goal-scenarios").innerHTML = data.scenarios.map((scenario, index) => `<div class="scenario-result ${index === 1 ? "base" : ""}"><span>${esc(scenario.label)} · 연 ${number(scenario.annualReturnPct)}%</span><b>${goalDate(scenario)}</b></div>`).join("");
  const width = Math.max(260, Math.round($("#projection-chart").clientWidth)), height = 160, left = 54, right = 12, top = 16, bottom = 28;
  const max = Math.max(goal.targetAmount, ...data.scenarios.flatMap(scenario => scenario.points.map(point => point.value)), 1);
  const firstDate = Date.parse(data.scenarios[0].points[0].date);
  const lastDate = Date.parse(data.scenarios[0].points.at(-1).date);
  const x = point => left + (Date.parse(point.date) - firstDate) / Math.max(1, lastDate - firstDate) * (width - left - right);
  const y = value => top + (1 - value / max) * (height - top - bottom);
  const lineColors = ["#9eb0be", "#0b8f76", "#81a4d7"];
  const grid = Array.from({length: 4}, (_, index) => {
    const value = max * index / 3;
    return `<line x1="${left}" y1="${y(value)}" x2="${width-right}" y2="${y(value)}" stroke="#edf1f4"/><text x="${left-7}" y="${y(value)+3}" text-anchor="end" fill="#9aa6af" font-size="9">${new Intl.NumberFormat("ko-KR", {notation: "compact", maximumFractionDigits: 1}).format(value)}</text>`;
  }).join("");
  const lines = data.scenarios.map((scenario, index) => `<polyline points="${scenario.points.map(point => `${x(point)},${y(point.value)}`).join(" ")}" fill="none" stroke="${lineColors[index]}" stroke-width="${index === 1 ? 2.5 : 1.5}" ${index === 1 ? "" : 'stroke-dasharray="4 4"'}/>`).join("");
  $("#projection-chart").classList.remove("empty-chart");
  $("#projection-chart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="목표 자산 전망. 낮은 가정 ${goalDate(data.scenarios[0])}, 입력 가정 ${goalDate(base)}, 높은 가정 ${goalDate(data.scenarios[2])}. 세로축 ${goal.currency}, 가로축 경과 기간.">${grid}<line x1="${left}" y1="${y(goal.targetAmount)}" x2="${width-right}" y2="${y(goal.targetAmount)}" stroke="#d4b477" stroke-dasharray="3 4"/>${lines}<text x="${left}" y="${height-6}" fill="#9aa6af" font-size="9">현재</text><text x="${width-right}" y="${height-6}" text-anchor="end" fill="#9aa6af" font-size="9">${esc(data.scenarios[0].points.at(-1).date.slice(0,7))} · ${goal.currency}</text></svg>`;
}
async function loadGoal() {
  const sequence = ++goalSequence;
  if (!state.goal) { showGoalPending("목표 금액·월 투자금·가정 수익률을 설정해주세요."); return; }
  const initial = goalInitialAmount();
  if (initial === null) { showGoalPending("보유 자산을 등록하고 모든 시세를 확인하면 계산합니다. 누락된 자산을 0으로 간주하지 않습니다."); return; }
  showGoalPending("입력한 가정으로 목표 달성 경로를 계산하고 있습니다.");
  try {
    const data = await api("/api/dashboard/goal", {initialAmount: initial, targetAmount: state.goal.targetAmount, monthlyContribution: state.goal.monthlyContribution, annualReturnPct: state.goal.annualReturnPct});
    if (sequence === goalSequence) renderProjection(data, initial);
  } catch {
    if (sequence === goalSequence) showGoalPending("목표 계산에 실패했습니다. 목표 설정을 확인하거나 다시 시도해주세요.");
  }
}

function addOwnedRow(position = {}) {
  if ($("#owned-positions").children.length >= 30) return;
  const fragment = $("#owned-position-template").content.cloneNode(true);
  const row = fragment.querySelector(".owned-row");
  for (const name of ["ticker", "quantity", "averageCost"]) row.querySelector(`[name=${name}]`).value = position[name] ?? "";
  row.querySelector(".remove-owned").addEventListener("click", () => { row.remove(); $("#add-owned-position").disabled = false; });
  $("#owned-positions").appendChild(fragment);
  $("#add-owned-position").disabled = $("#owned-positions").children.length >= 30;
}
function openPortfolio() {
  $("#owned-positions").textContent = "";
  if (state.portfolio.positions.length) state.portfolio.positions.forEach(addOwnedRow); else addOwnedRow();
  for (const name of ["cashKRW", "cashUSD", "baseCurrency"]) $("#portfolio-form").elements[name].value = state.portfolio[name];
  $("#portfolio-form-status").textContent = "";
  $("#portfolio-dialog").showModal();
}
function openGoal() {
  const form = $("#goal-form");
  form.reset();
  form.elements.currency.value = state.goal?.currency ?? state.portfolio.baseCurrency;
  if (state.goal) for (const name of ["targetAmount", "monthlyContribution", "annualReturnPct"]) form.elements[name].value = state.goal[name];
  $("#goal-form-status").textContent = "";
  $("#goal-dialog").showModal();
}
document.addEventListener("click", event => {
  if (event.target.closest("[data-edit-portfolio]")) openPortfolio();
  if (event.target.closest("[data-close-dialog]")) event.target.closest("dialog").close();
});
$("#add-owned-position").addEventListener("click", () => addOwnedRow());
$("#edit-goal-btn").addEventListener("click", openGoal);
$("#refresh-btn").addEventListener("click", () => { loadPortfolio(); loadMarkets(); });
$("#portfolio-form").addEventListener("submit", event => {
  event.preventDefault();
  try {
    const positions = [...$("#owned-positions").children].flatMap(row => {
      const inputs = ["ticker", "quantity", "averageCost"].map(name => row.querySelector(`[name=${name}]`).value.trim());
      if (inputs.every(value => value === "")) return [];
      if (inputs.some(value => value === "")) throw new Error("종목의 수량과 평균 매입가를 모두 입력해주세요.");
      return [{ticker: inputs[0].toUpperCase(), quantity: Number(inputs[1]), averageCost: Number(inputs[2])}];
    });
    const data = new FormData(event.target);
    const portfolio = {positions, cashKRW: Number(data.get("cashKRW")), cashUSD: Number(data.get("cashUSD")), baseCurrency: data.get("baseCurrency")};
    validatePortfolio(portfolio);
    saveState({...state, portfolio, portfolioSavedAt: new Date().toISOString()});
    $("#portfolio-dialog").close();
    message("보유 자산을 이 브라우저에 저장했습니다. 최신 시세로 평가하고 있습니다.");
    loadPortfolio();
  } catch (error) { $("#portfolio-form-status").textContent = error.name === "QuotaExceededError" || error.name === "SecurityError" ? "브라우저 저장에 실패했습니다. 입력은 유지됩니다. 저장 공간과 권한을 확인해주세요." : error.message; }
});
$("#goal-form").addEventListener("submit", event => {
  event.preventDefault();
  try {
    const data = new FormData(event.target);
    const goal = {currency: data.get("currency"), targetAmount: Number(data.get("targetAmount")), monthlyContribution: Number(data.get("monthlyContribution")), annualReturnPct: Number(data.get("annualReturnPct"))};
    validateGoal(goal);
    saveState({...state, goal});
    $("#goal-dialog").close();
    message("목표 가정을 이 브라우저에 저장했습니다. 달성 시점은 가정이 바뀌면 달라집니다.");
    loadGoal();
  } catch (error) { $("#goal-form-status").textContent = error.name === "QuotaExceededError" || error.name === "SecurityError" ? "목표 저장에 실패했습니다. 현재 입력은 유지됩니다." : error.message; }
});

$("#load-toss").addEventListener("click", async () => {
  const button = $("#load-toss");
  button.disabled = true;
  $("#toss-status").textContent = "Toss 계좌를 조회하고 있습니다.";
  try {
    const data = await api("/api/toss/holdings");
    $("#toss-body").innerHTML = data.items.map(item => `<tr><td>${esc(item.account)} · ${esc(item.symbol)}<br><small>${esc(item.name)}</small></td><td class="number">${number(Number(item.quantity), 8)}</td><td class="number">${money(Number(item.averageCost), item.currency)}</td><td class="number">${money(Number(item.marketValue), item.currency)}</td><td class="number ${tone(Number(item.profitLoss))}">${money(Number(item.profitLoss), item.currency)}<br>${pct(Number(item.profitLossPct))}</td></tr>`).join("");
    $("#toss-status").textContent = `${data.accountCount}개 계좌 · ${data.items.length}개 보유 종목 · 조회 ${new Date(data.fetchedAt).toLocaleString("ko-KR")}`;
  } catch (error) {
    $("#toss-body").textContent = "";
    $("#toss-status").textContent = error.message;
  } finally { button.disabled = false; }
});
fetch("/auth/session").then(response => response.json()).then(data => {
  if (!data.user) { location.replace("/login"); return; }
  $("#signed-in-user").textContent = data.user.email;
}).catch(() => message("로그인 상태를 확인하지 못했습니다. 새로고침해주세요."));

restoreState();
loadMarkets();
loadPortfolio();
let observedProjectionWidth = 0;
new ResizeObserver(([entry]) => {
  const width = Math.round(entry.contentRect.width);
  if (width === observedProjectionWidth) return;
  observedProjectionWidth = width;
  if (projectionView) renderProjection(projectionView.data, projectionView.initial);
}).observe($("#projection-chart"));
