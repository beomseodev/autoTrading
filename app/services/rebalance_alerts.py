from datetime import date, datetime, timezone

from app.dashboard_schemas import PortfolioSnapshotRequest


def evaluate_rebalance(request: PortfolioSnapshotRequest, snapshot: dict, today: date | None = None) -> dict:
    result = {"ready": False, "status": "not_configured", "items": [], "asOf": None,
              "thresholdPct": request.rebalanceTolerancePct,
              "message": "모든 종목의 목표 비중을 입력하면 리밸런싱을 점검합니다."}
    if not request.positions or any(p.targetWeight is None for p in request.positions):
        return result
    if not snapshot["complete"] or not snapshot["totalValue"]:
        return {**result, "status": "unavailable", "message": "평가금액이나 시세가 부족해 점검을 보류했습니다."}
    dates = {item["asOf"] for item in snapshot["holdings"]}
    parsed = [date.fromisoformat(value) for value in dates]
    today = today or datetime.now(timezone.utc).date()
    per_currency = {}
    for item in snapshot["holdings"]:
        per_currency.setdefault(item.get("currency", "USD"), set()).add(item["asOf"])
    fx_needed = (request.cashKRW > 0 and request.baseCurrency == "USD") or (request.cashUSD > 0 and request.baseCurrency == "KRW") or any(
        item.get("currency", "USD") != request.baseCurrency for item in snapshot["holdings"])
    fx = snapshot.get("fx")
    stale_fx = fx_needed and (not fx or not 0 <= (today - date.fromisoformat(fx["asOf"])).days <= 4 or
                             abs((date.fromisoformat(fx["asOf"]) - max(parsed)).days) > 1)
    if any(len(group) > 1 for group in per_currency.values()) or (max(parsed) - min(parsed)).days > 1 or any(
            not 0 <= (today - observed).days <= 4 for observed in parsed) or stale_fx:
        return {**result, "status": "stale", "message": "종목별 시세 날짜가 다르거나 오래되어 점검을 보류했습니다."}
    total = snapshot["totalValue"]
    targets = {p.ticker: p.targetWeight for p in request.positions}
    values = [(item["ticker"], item["value"], targets[item["ticker"]]) for item in snapshot["holdings"]]
    values.append(("현금", snapshot["cashValue"], max(0, 100 - sum(targets.values()))))
    rows = []
    for ticker, value, target in values:
        weight = value / total * 100
        drift = weight - target
        rows.append({"ticker": ticker, "weightPct": weight, "targetWeightPct": target,
                     "driftPct": drift, "adjustmentValue": total * target / 100 - value,
                     "needsRebalance": abs(drift) > request.rebalanceTolerancePct + 1e-9})
    needed = any(row["needsRebalance"] for row in rows)
    return {**result, "ready": True, "status": "rebalance_needed" if needed else "balanced",
            "items": rows, "asOf": max(dates), "quoteDates": sorted(dates),
            "message": "목표 비중의 허용폭을 벗어난 자산이 있습니다." if needed else "모든 자산이 목표 비중의 허용폭 안에 있습니다."}
