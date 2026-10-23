"use strict";

let STORAGE_KEY = null;
let accountSub = null;
let ownerAccount = false;
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
let cloudLoading = true;
let cloudReady = false;
let cloudRevision = null;
let emailReady = false;
let cloudSaving = false;

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
    if (!item || typeof item.ticker !== "string" || !/^(?:[A-Z][A-Z0-9.\-]{0,9}|[0-9]{6}\.K[SQ])$/.test(item.ticker) ||
        !finiteRange(item.quantity, Number.MIN_VALUE, 1e9) || !finiteRange(item.averageCost, 0, 1e9) || symbols.has(item.ticker)) throw new Error("종목 코드·수량·평균 매입가를 확인해주세요. 같은 종목은 한 행에 합쳐 입력합니다.");
    symbols.add(item.ticker);
    if (item.targetWeight != null && !finiteRange(item.targetWeight, 0, 100)) throw new Error("목표 비중은 0~100%로 입력해주세요.");
  }
  if (value.rebalanceTolerancePct !== undefined && !finiteRange(value.rebalanceTolerancePct, .01, 50)) throw new Error("허용폭은 0.01~50%p로 입력해주세요.");
  if (value.emailAlerts !== undefined && typeof value.emailAlerts !== "boolean") throw new Error("알림 설정을 확인해주세요.");
  if (value.positions.reduce((sum, item) => sum + (item.targetWeight ?? 0), 0) > 100 + 1e-6) throw new Error("목표 비중 합계는 100% 이하여야 합니다. 남은 비중은 현금입니다.");
  if (value.emailAlerts && (!value.positions.length || value.positions.some(item => item.targetWeight == null))) throw new Error("이메일 알림에는 모든 종목의 목표 비중이 필요합니다.");
}
function validateGoal(value) {
  if (!value || !["KRW", "USD"].includes(value.currency) || !finiteRange(value.targetAmount, .01, 1e15) ||
      !finiteRange(value.monthlyContribution, 0, 1e12) || !finiteRange(value.annualReturnPct, -50, 50)) throw new Error("목표 금액·월 투자금·가정 수익률을 확인해주세요.");
}
function saveState(next) {
  if (!STORAGE_KEY) throw new Error("로그인 상태를 먼저 확인해주세요.");
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
  const headers = accountSub ? {"X-Portfolio-Account": accountSub} : {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, body === undefined ? {headers} : {method: "POST", headers, body: JSON.stringify(body)});
  const result = await response.json();
  if (response.status === 401) { location.assign("/login"); throw new Error("다시 로그인해주세요."); }
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "입력값을 확인하거나 잠시 후 다시 시도해주세요.");
  return result;
}
function renderEmailStatus(status = null) {
  $("#test-email-btn").hidden = !ownerAccount;
  if (!ownerAccount) {
    $("#email-status").textContent = "리밸런싱은 화면에서 점검할 수 있습니다. 이메일 알림은 현재 운영자 전용입니다.";
    return;
  }
  $("#test-email-btn").disabled = !cloudReady || !emailReady;
  $("#email-status").textContent = !emailReady ? "이메일 발송 설정 대기 · 지금은 화면에서 리밸런싱을 점검할 수 있습니다." : `${state.portfolio.emailAlerts ? "이메일 알림 켜짐" : "이메일 알림 꺼짐"}${status ? ` · 최근 점검 ${new Date(status.checkedAt).toLocaleString("ko-KR")} · ${status.message}` : " · 아직 예약 점검 기록이 없습니다."}`;
}
async function persistState(next) {
  if (cloudLoading || cloudSaving) throw new Error("저장 정보를 처리하고 있습니다. 잠시 후 다시 시도해주세요.");
  if (!accountSub) {
    state = next;
    renderEmailStatus();
    return;
  }
  cloudSaving = true;
  try {
    if (cloudReady) {
      const result = await api("/api/dashboard/state", {state: next, revision: cloudRevision});
      cloudRevision = result.revision;
      $("#cloud-status").textContent = "자산·목표를 계정에 저장했습니다. 다른 기기에서도 불러올 수 있습니다.";
    }
    try { saveState(next); }
    catch (error) {
      if (!cloudReady) throw error;
      state = next;
      $("#cloud-status").textContent = "계정 저장은 완료됐지만 브라우저 백업을 저장하지 못했습니다.";
    }
    $("#sync-local-btn").hidden = true;
    renderEmailStatus();
  } finally { cloudSaving = false; }
}
async function loadCloudState() {
  try {
    const result = await api("/api/dashboard/state");
    cloudReady = result.configured;
    emailReady = result.emailReady;
    cloudRevision = result.revision;
    if (result.state) {
      validatePortfolio(result.state.portfolio);
      if (result.state.goal !== null) validateGoal(result.state.goal);
      try {
        const previous = localStorage.getItem(STORAGE_KEY);
        if (previous && previous !== JSON.stringify(result.state) && !localStorage.getItem(STORAGE_KEY + "-before-cloud")) localStorage.setItem(STORAGE_KEY + "-before-cloud", previous);
        saveState(result.state);
      } catch { state = result.state; }
    } else if (cloudReady) {
      $("#sync-local-btn").hidden = !state.portfolio.positions.length && !state.portfolio.cashKRW && !state.portfolio.cashUSD && !state.goal;
    }
    $("#cloud-status").textContent = cloudReady ? result.state ? "계정에 저장된 자산·목표를 불러왔습니다. 수정 시 다른 기기에도 반영됩니다." : "계정에 저장된 정보가 없습니다. 기존 브라우저 정보는 자동으로 업로드하지 않습니다." : "서버 저장 설정 전입니다. 현재 브라우저에만 저장하며 이메일 점검에는 반영되지 않습니다.";
    renderEmailStatus(result.notificationStatus);
  } catch (error) {
    cloudReady = false;
    $("#cloud-status").textContent = `계정 정보를 불러오지 못했습니다. 기존 브라우저 정보는 보존했습니다. ${error.message}`;
  } finally { cloudLoading = false; }
}
$("#sync-local-btn").addEventListener("click", async () => {
  $("#sync-local-btn").disabled = true;
  try { await persistState(state); $("#cloud-status").textContent = "이 브라우저의 자산·목표를 계정에 저장했습니다."; }
  catch (error) { message(error.message); }
  finally { $("#sync-local-btn").disabled = false; }
});
$("#test-email-btn").addEventListener("click", async () => {
  $("#test-email-btn").disabled = true;
  try { renderEmailStatus(await api("/api/dashboard/notifications/test", {})); }
  catch (error) { $("#email-status").textContent = error.message; }
  finally { $("#test-email-btn").disabled = !cloudReady || !emailReady; }
});
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
  const items = data.holdings.map(item => ({label: item.name || item.ticker, value: item.value}));
  if (data.cashValue > 0) items.push({label: "현금", value: data.cashValue});
  items.sort((a, b) => b.value - a.value);
  let offset = 0;
  const segments = items.map((item, index) => {
    const percent = item.value / data.totalValue * 100;
    const start = offset;
    offset += percent;
    const label = `${item.label} · ${number(percent)}%`;
    return `<circle class="allocation-segment" cx="90" cy="90" r="75" pathLength="100" fill="none" stroke="${COLORS[index % COLORS.length]}" stroke-width="30" stroke-dasharray="${percent} ${100-percent}" stroke-dashoffset="${-start}" transform="rotate(-90 90 90)" tabindex="0" role="img" aria-label="${esc(label)}" aria-describedby="allocation-tooltip" data-allocation-label="${esc(label)}"/>`;
  }).join("");
  $("#allocation-visual").innerHTML = `<div class="allocation-donut"><svg viewBox="0 0 180 180" aria-label="종목별 자산 비중">${segments}</svg><div class="donut-hole">보유 자산<b>${items.length}</b></div><div id="allocation-tooltip" class="allocation-tooltip" role="tooltip" hidden></div></div>`;
  $("#allocation-legend").innerHTML = items.map((item, index) => `<div class="allocation-line"><i style="background:${COLORS[index % COLORS.length]}"></i><span>${esc(item.label)}</span><b>${number(item.value / data.totalValue * 100)}%</b></div>`).join("");
}
for (const type of ["pointerover", "focusin"]) $("#allocation-visual").addEventListener(type, event => {
  const segment = event.target.closest("[data-allocation-label]");
  const tooltip = $("#allocation-tooltip");
  if (segment && tooltip) { tooltip.textContent = segment.dataset.allocationLabel; tooltip.hidden = false; }
});
for (const type of ["pointerout", "focusout"]) $("#allocation-visual").addEventListener(type, () => {
  const tooltip = $("#allocation-tooltip");
  if (tooltip) tooltip.hidden = true;
});
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
  $("#holdings-description").textContent = state.portfolioSavedAt ? `직접 입력 · 수정 ${new Date(state.portfolioSavedAt).toLocaleDateString("ko-KR")} · 가격·매입가 현지 통화` : "한국·미국 주식 · ETF / 직접 입력 / 가격·매입가 현지 통화";
  if (data.holdings.length) {
    $("#holdings-body").innerHTML = data.holdings.map(item => `<tr><td><strong>${esc(item.name || item.ticker)}</strong><small>${esc(item.ticker)} · ${item.available ? esc(item.currency) : "시세 미확인"}</small></td><td class="number" data-label="수량">${number(item.quantity, 6)}</td><td class="number" data-label="최근 가격">${item.available ? money(item.price, item.currency) : "—"}${item.available ? `<small>가격 기준일 ${esc(item.asOf)}</small>` : ""}<small class="price-cost-detail">평균 ${money(item.averageCost, item.currency)}</small></td><td class="number" data-label="평가금액">${money(item.value)}</td><td class="number ${item.available ? tone(item.profitPct) : "muted"}" data-label="평가손익률">${item.available ? pct(item.profitPct) : "조회 불가"}</td><td class="number">${item.weightPct === null ? "—" : `${number(item.weightPct)}%`}</td></tr>`).join("");
  } else {
    $("#holdings-body").innerHTML = `<tr><td colspan="6" class="empty-table"><strong>${registered ? "등록한 주식이 없습니다." : "아직 등록한 자산이 없습니다."}</strong><p>종목·보유 수량·평균 매입가를 입력하면 실제 보유 현황이 표시됩니다.</p><button class="button primary" data-edit-portfolio type="button">보유 자산 등록</button></td></tr>`;
  }
  const fxNote = data.fx ? `환율 ${number(data.fx.value)}원/USD (가격 기준일 ${data.fx.asOf})` : "환율 조회 불가";
  $("#valuation-note").textContent = `Yahoo 최근 일봉 · 가격 기준일은 거래소 현지 날짜이며 실시간 체결 시각은 제공하지 않습니다. 화면 조회 ${new Date(data.fetchedAt).toLocaleString("ko-KR", {timeZone: "Asia/Seoul", hour12: false})} (한국시간) · ${fxNote}. 종목별 평가손익을 표시 통화로 환산하며 취득 환율에 따른 환차손익·실현손익·배당 누계는 제외합니다.`;
  $("#updated-at").textContent = `화면 조회 ${new Date(data.fetchedAt).toLocaleTimeString("ko-KR", {timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit"})} (한국시간)`;
  renderAllocation(data);
  const check = data.rebalance;
  $("#rebalance-status").textContent = check ? `${check.message}${check.asOf ? ` 시세 기준 ${check.asOf} · 허용폭 ±${number(check.thresholdPct)}%p` : ""}` : "목표 비중을 입력해주세요.";
  $("#rebalance-body").innerHTML = (check?.items ?? []).map(item => `<tr><td><strong>${esc(item.name || item.ticker)}</strong>${item.name && item.name !== item.ticker ? `<small>${esc(item.ticker)}</small>` : ""}${item.needsRebalance ? " · 점검 필요" : ""}</td><td class="number" data-label="현재 비중">${number(item.weightPct)}%</td><td class="number" data-label="목표 비중">${number(item.targetWeightPct)}%</td><td class="number" data-label="차이 (%p)">${pct(item.driftPct).replace("%", "%p")}</td><td class="number">${money(item.adjustmentValue)}</td></tr>`).join("");
}
async function loadPortfolio() {
  const sequence = ++portfolioSequence;
  ++goalSequence;
  portfolioBusy = true;
  snapshot = null;
  $("#rebalance-status").textContent = "최신 시세로 점검하고 있습니다.";
  $("#rebalance-body").textContent = "";
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
      $("#rebalance-status").textContent = "자산 평가에 실패해 리밸런싱 점검을 보류했습니다.";
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

let symbolSearchSequence = 0;
function clearSymbolSearch() {
  symbolSearchSequence++;
  $("#symbol-results").replaceChildren();
  $("#symbol-search-status").textContent = "";
}
async function searchSymbols() {
  clearSymbolSearch();
  const query = $("#symbol-query").value.trim();
  if (!query) return;
  const sequence = symbolSearchSequence;
  $("#symbol-search-status").textContent = "검색 중…";
  try {
    const data = await api(`/api/dashboard/symbols?q=${encodeURIComponent(query)}`);
    if (sequence !== symbolSearchSequence) return;
    $("#symbol-search-status").textContent = data.items.length ? "종목을 선택한 뒤 수량과 평균 매입가를 입력하세요." : "검색 결과가 없습니다. 다른 이름이나 코드로 검색해주세요.";
    for (const item of data.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button quiet";
      button.textContent = `${item.name} · ${item.ticker} · ${item.market} · ${item.currency}`;
      button.addEventListener("click", () => {
        const rows = [...$("#owned-positions").children];
        const existing = rows.find(row => row.querySelector('[name=ticker]').value.trim().toUpperCase() === item.ticker);
        const empty = rows.find(row => [...row.querySelectorAll("input")].every(input => !input.value.trim()));
        if (existing) {
          existing.querySelector('[name=quantity]').focus();
          $("#symbol-search-status").textContent = "이미 입력한 종목입니다. 기존 행에서 수량을 수정해주세요.";
          return;
        }
        if (!empty && rows.length >= 30) {
          $("#symbol-search-status").textContent = "종목은 최대 30개까지 입력할 수 있습니다.";
          return;
        }
        const row = empty || addOwnedRow({ticker: item.ticker});
        row.querySelector('[name=ticker]').value = item.ticker;
        row.querySelector(".owned-name").textContent = item.name;
        row.querySelector('[name=quantity]').focus();
        clearSymbolSearch();
        $("#symbol-search-status").textContent = `${item.name} 선택됨 · 평균 매입가는 ${item.currency === "KRW" ? "원화" : "달러"}로 입력하세요.`;
      });
      $("#symbol-results").appendChild(button);
    }
  } catch (error) {
    if (sequence === symbolSearchSequence) $("#symbol-search-status").textContent = error.message;
  }
}
$("#search-symbols").addEventListener("click", searchSymbols);
$("#symbol-query").addEventListener("input", clearSymbolSearch);
$("#symbol-query").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); searchSymbols(); }
});

function addOwnedRow(position = {}) {
  if ($("#owned-positions").children.length >= 30) return;
  const fragment = $("#owned-position-template").content.cloneNode(true);
  const row = fragment.querySelector(".owned-row");
  for (const name of ["ticker", "quantity", "averageCost", "targetWeight"]) row.querySelector(`[name=${name}]`).value = position[name] ?? "";
  row.querySelector(".owned-name").textContent = snapshot?.holdings.find(item => item.ticker === position.ticker)?.name ?? "";
  row.querySelector('[name=ticker]').addEventListener("input", () => { row.querySelector(".owned-name").textContent = ""; });
  row.querySelector(".remove-owned").addEventListener("click", () => { row.remove(); $("#add-owned-position").disabled = false; });
  $("#owned-positions").appendChild(fragment);
  $("#add-owned-position").disabled = $("#owned-positions").children.length >= 30;
  return row;
}
function openPortfolio() {
  if (cloudLoading) { message("계정 정보를 확인하고 있습니다. 잠시 후 다시 시도해주세요."); return; }
  clearSymbolSearch();
  $("#symbol-query").value = "";
  $("#owned-positions").textContent = "";
  if (state.portfolio.positions.length) state.portfolio.positions.forEach(addOwnedRow); else addOwnedRow();
  for (const name of ["cashKRW", "cashUSD", "baseCurrency"]) $("#portfolio-form").elements[name].value = state.portfolio[name];
  $("#portfolio-form").elements.rebalanceTolerancePct.value = state.portfolio.rebalanceTolerancePct ?? 5;
  $("#portfolio-form").elements.emailAlerts.checked = state.portfolio.emailAlerts ?? false;
  $("#portfolio-form").elements.emailAlerts.disabled = !cloudReady || !emailReady;
  $("#portfolio-form-status").textContent = "";
  $("#portfolio-dialog").showModal();
}
function openGoal() {
  if (cloudLoading) { message("계정 정보를 확인하고 있습니다. 잠시 후 다시 시도해주세요."); return; }
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
$("#portfolio-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const positions = [...$("#owned-positions").children].flatMap(row => {
      const inputs = ["ticker", "quantity", "averageCost"].map(name => row.querySelector(`[name=${name}]`).value.trim());
      if (inputs.every(value => value === "")) return [];
      if (inputs.some(value => value === "")) throw new Error("종목의 수량과 평균 매입가를 모두 입력해주세요.");
      const target = row.querySelector('[name=targetWeight]').value.trim();
      return [{ticker: inputs[0].toUpperCase(), quantity: Number(inputs[1]), averageCost: Number(inputs[2]), targetWeight: target === "" ? null : Number(target)}];
    });
    const data = new FormData(event.target);
    const portfolio = {positions, cashKRW: Number(data.get("cashKRW")), cashUSD: Number(data.get("cashUSD")), baseCurrency: data.get("baseCurrency"), rebalanceTolerancePct: Number(data.get("rebalanceTolerancePct")), emailAlerts: data.has("emailAlerts")};
    validatePortfolio(portfolio);
    await persistState({...state, portfolio, portfolioSavedAt: new Date().toISOString()});
    $("#portfolio-dialog").close();
    message(!accountSub ? "입력을 적용했습니다. 비로그인 입력은 저장되지 않으며 새로고침·로그인 시 초기화됩니다." : cloudReady ? "보유 자산과 목표 비중을 계정에 저장했습니다. 최신 시세로 점검합니다." : "현재 브라우저에만 저장했습니다. 이메일 점검에는 반영되지 않습니다.");
    loadPortfolio();
  } catch (error) { $("#portfolio-form-status").textContent = error.name === "QuotaExceededError" || error.name === "SecurityError" ? "브라우저 저장에 실패했습니다. 입력은 유지됩니다. 저장 공간과 권한을 확인해주세요." : error.message; }
});
$("#goal-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const data = new FormData(event.target);
    const goal = {currency: data.get("currency"), targetAmount: Number(data.get("targetAmount")), monthlyContribution: Number(data.get("monthlyContribution")), annualReturnPct: Number(data.get("annualReturnPct"))};
    validateGoal(goal);
    await persistState({...state, goal});
    $("#goal-dialog").close();
    message(!accountSub ? "목표를 적용했습니다. 비로그인 입력은 저장되지 않으며 새로고침·로그인 시 초기화됩니다." : cloudReady ? "목표 가정을 계정에 저장했습니다." : "목표 가정을 현재 브라우저에만 저장했습니다.");
    loadGoal();
  } catch (error) { $("#goal-form-status").textContent = error.name === "QuotaExceededError" || error.name === "SecurityError" ? "목표 저장에 실패했습니다. 현재 입력은 유지됩니다." : error.message; }
});

async function initializeAccount() {
  try {
    const response = await fetch("/auth/session");
    if (!response.ok) throw new Error("로그인 상태를 확인하지 못했습니다.");
    const data = await response.json();
    if (!data.user) {
      cloudLoading = false;
      $("#signed-in-user").textContent = "저장 없이 이용 중";
      $("#cloud-status").textContent = "로그인 없이 이용할 수 있습니다. 입력은 저장되지 않으며 새로고침·로그인 시 초기화됩니다. 저장하려면 Google로 로그인해주세요.";
      $("#portfolio-form button[type=submit]").textContent = "적용하고 현황 보기";
      $("#goal-form button[type=submit]").textContent = "목표 적용·계산";
      renderEmailStatus();
      loadMarkets();
      loadPortfolio();
      return;
    }
    accountSub = data.user.sub;
    ownerAccount = data.isOwner === true;
    STORAGE_KEY = `portfolio-dashboard-v1:${encodeURIComponent(accountSub)}`;
    $("#signed-in-user").textContent = data.user.email;
    $("#login-link").hidden = true;
    $("#logout-form").hidden = false;
    if (ownerAccount) {
      try {
        const legacy = localStorage.getItem("portfolio-dashboard-v1");
        if (legacy && localStorage.getItem(STORAGE_KEY) === null) localStorage.setItem(STORAGE_KEY, legacy);
      } catch { /* Browser storage may be disabled; server storage still works. */ }
    }
    restoreState();
    loadMarkets();
    await loadCloudState();
    loadPortfolio();
  } catch { message("로그인 상태를 확인하지 못했습니다. 새로고침해주세요."); }
}
initializeAccount();
document.addEventListener("visibilitychange", async () => {
  if (document.hidden) return;
  try {
    const response = await fetch("/auth/session");
    const data = await response.json();
    if ((data.user?.sub ?? null) !== accountSub) location.reload();
  } catch { /* API authorization still protects account data. */ }
});
let observedProjectionWidth = 0;
new ResizeObserver(([entry]) => {
  const width = Math.round(entry.contentRect.width);
  if (width === observedProjectionWidth) return;
  observedProjectionWidth = width;
  if (projectionView) renderProjection(projectionView.data, projectionView.initial);
}).observe($("#projection-chart"));
