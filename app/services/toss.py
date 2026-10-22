from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx


class TossError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def amount(value):
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("Non-finite amount")
    return str(number)


class TossService:
    def holdings(self) -> dict:
        client_id, secret = os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET")
        if not client_id or not secret:
            raise TossError("서버에 Toss API 키를 설정해주세요.", 503)
        try:
            with httpx.Client(base_url="https://openapi.tossinvest.com", timeout=15) as client:
                response = client.post("/oauth2/token", data={"grant_type": "client_credentials",
                                       "client_id": client_id, "client_secret": secret})
                response.raise_for_status()
                client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
                response = client.get("/api/v1/accounts")
                response.raise_for_status()
                accounts = response.json()["result"]
                rows = []
                for index, account in enumerate(accounts, 1):
                    response = client.get("/api/v1/holdings", headers={"X-Tossinvest-Account": str(account["accountSeq"])})
                    response.raise_for_status()
                    for item in response.json()["result"]["items"]:
                        if item["currency"] not in ("USD", "KRW"):
                            raise ValueError("Unsupported currency")
                        rows.append({"account": f"계좌 {index}", "symbol": item["symbol"], "name": item["name"],
                                     "currency": item["currency"], "quantity": amount(item["quantity"]),
                                     "averageCost": amount(item["averagePurchasePrice"]),
                                     "marketValue": amount(item["marketValue"]["amount"]),
                                     "profitLoss": amount(item["profitLoss"]["amount"]),
                                     "profitLossPct": amount(Decimal(item["profitLoss"]["rate"]) * 100)})
                return {"items": rows, "accountCount": len(accounts),
                        "fetchedAt": datetime.now(timezone.utc).isoformat()}
        except httpx.HTTPStatusError as exc:
            message = "Toss 조회 한도에 도달했습니다. 잠시 후 다시 시도해주세요." if exc.response.status_code == 429 else "Toss 계좌 조회에 실패했습니다. API 키와 조회 권한을 확인해주세요."
            raise TossError(message) from None
        except (httpx.RequestError, KeyError, TypeError, ValueError, InvalidOperation):
            raise TossError("Toss 응답을 확인하지 못했습니다. 잠시 후 다시 시도해주세요.") from None
