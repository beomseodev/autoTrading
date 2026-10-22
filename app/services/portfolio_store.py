import os
import re
from urllib.parse import quote

import httpx
from fastapi import HTTPException


def storage_ready():
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN"))


class PortfolioStore:
    def __init__(self):
        token = os.getenv("BLOB_READ_WRITE_TOKEN", "")
        match = re.fullmatch(r"vercel_blob_rw_([A-Za-z0-9]+)_[A-Za-z0-9_-]+", token)
        if not match:
            raise HTTPException(503, "서버의 비공개 저장소 설정이 필요합니다.")
        self.headers = {"Authorization": "Bearer " + token, "x-api-version": "12"}
        self.base = f"https://{match[1].lower()}.private.blob.vercel-storage.com"

    def read(self, name):
        try:
            response = httpx.get(self.base + "/" + quote(name, safe="/"), params={"cache": "0"},
                                 headers=self.headers, timeout=15)
            if response.status_code == 404:
                return None, None
            response.raise_for_status()
            return response.json(), response.headers["etag"]
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(503, "서버 저장 정보를 읽지 못했습니다. 기존 데이터는 유지됩니다.") from None

    def write(self, name, value, revision=None, *, create=False):
        headers = {**self.headers, "x-vercel-blob-access": "private", "x-add-random-suffix": "0",
                   "x-allow-overwrite": "0" if create else "1", "x-content-type": "application/json",
                   "x-cache-control-max-age": "60"}
        if revision:
            headers["x-if-match"] = revision
        try:
            # Blob API v12 mirrors the official SDK; private reads bypass its CDN cache.
            response = httpx.put("https://vercel.com/api/blob", params={"pathname": name},
                                 headers=headers, json=value, timeout=15)
            if response.status_code in (409, 412) or (response.status_code == 400 and
                    response.json().get("error", {}).get("message", "").startswith("This blob already exists")):
                raise HTTPException(409, "다른 기기에서 정보가 변경되었습니다. 입력을 보관하고 새로고침 후 다시 확인해주세요.")
            response.raise_for_status()
            return response.json()["etag"]
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(503, "서버 저장에 실패했습니다. 입력 내용은 유지됩니다.") from None
