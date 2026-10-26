from datetime import date, datetime, timezone

import pandas as pd

from app.dashboard_schemas import PortfolioSnapshotRequest
from app.services import alert_data


def quote_date_problem(snapshot: dict, fx_needed: bool, now: datetime) -> str | None:
    sessions = {}
    for item in snapshot["holdings"]:
        name = "XKRX" if item["ticker"].endswith((".KS", ".KQ")) else "XNYS"
        try:
            if name not in sessions:
                expected = alert_data.expected_session({"calendar": name}, now)
                schedule = alert_data.calendar(name, now.year).schedule
                opened = schedule[schedule["open"] <= pd.Timestamp(now)]
                sessions[name] = expected, set(opened.loc[str(expected):].index.date)
        except Exception:
            return f"{item['ticker']} 거래일을 확인할 수 없어 점검을 보류했습니다. 잠시 후 다시 조회해주세요."
        expected, allowed = sessions[name]
        try:
            observed = date.fromisoformat(item.get("asOf"))
        except (ValueError, TypeError):
            observed = None
        if observed not in allowed:
            return (f"{item['ticker']} 시세 기준일 {item.get('asOf') or '미확인'}: "
                    f"오래되었거나 유효하지 않아 점검을 보류했습니다. 필요 기준일: {expected} 확정 시세 "
                    "또는 이후 개장한 거래일의 가격. 5분 후 새로고침해주세요.")
    if fx_needed:
        raw = (snapshot.get("fx") or {}).get("asOf")
        expected = alert_data.expected_fx_day(now)
        try:
            observed = date.fromisoformat(raw)
        except (ValueError, TypeError):
            observed = None
        if observed is None or not expected <= observed <= now.date() or observed.weekday() > 4:
            return (f"환율 기준일 {raw or '미확인'}: 오래되었거나 유효하지 않아 점검을 보류했습니다. "
                    f"필요 기준일: {expected} 이후 현재까지의 영업일. 5분 후 새로고침해주세요.")
    return None


def evaluate_rebalance(request: PortfolioSnapshotRequest, snapshot: dict, today: date | None = None) -> dict:
    result = {"ready": False, "status": "not_configured", "items": [], "asOf": None,
              "thresholdPct": request.rebalanceTolerancePct,
              "message": "모든 종목의 개별 또는 그룹 목표 비중을 입력하면 리밸런싱을 점검합니다."}
    members = {ticker for group in request.rebalanceGroups for ticker in group.tickers}
    if not request.positions or any(p.targetWeight is None and p.ticker not in members for p in request.positions):
        return result
    if not snapshot["complete"] or not snapshot["totalValue"]:
        return {**result, "status": "unavailable", "message": "평가금액이나 시세가 부족해 점검을 보류했습니다."}
    now = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) if today else datetime.now(timezone.utc)
    fx_needed = (request.cashKRW > 0 and request.baseCurrency == "USD") or (request.cashUSD > 0 and request.baseCurrency == "KRW") or any(
        item.get("currency", "USD") != request.baseCurrency for item in snapshot["holdings"])
    problem = quote_date_problem(snapshot, fx_needed, now)
    if problem:
        return {**result, "status": "stale", "message": problem}
    dates = {item["asOf"] for item in snapshot["holdings"]}
    total = snapshot["totalValue"]
    targets = {p.ticker: p.targetWeight for p in request.positions if p.ticker not in members}
    names = {item["ticker"]: item.get("name") or item["ticker"] for item in snapshot["holdings"]}
    values = [(item["ticker"], item["value"], targets[item["ticker"]]) for item in snapshot["holdings"] if item["ticker"] not in members]
    for group in request.rebalanceGroups:
        key = " + ".join(group.tickers)
        names[key] = f"{group.name} ({' + '.join(names[ticker] for ticker in group.tickers)})"
        values.append((key, sum(item["value"] for item in snapshot["holdings"] if item["ticker"] in group.tickers), group.targetWeight))
    values.append(("현금", snapshot["cashValue"], max(0, 100 - sum(targets.values()) - sum(g.targetWeight for g in request.rebalanceGroups))))
    rows = []
    for ticker, value, target in values:
        weight = value / total * 100
        drift = weight - target
        rows.append({"ticker": ticker, "name": names.get(ticker, ticker), "weightPct": weight, "targetWeightPct": target,
                     "driftPct": drift, "adjustmentValue": total * target / 100 - value,
                     "needsRebalance": abs(drift) > request.rebalanceTolerancePct + 1e-9})
    needed = any(row["needsRebalance"] for row in rows)
    return {**result, "ready": True, "status": "rebalance_needed" if needed else "balanced",
            "items": rows, "asOf": max(dates), "quoteDates": sorted(dates),
            "message": "목표 비중의 허용폭을 벗어난 자산이 있습니다." if needed else "모든 자산이 목표 비중의 허용폭 안에 있습니다."}
