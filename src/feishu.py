from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import requests


MAX_BODY_BYTES = 20 * 1024
SAFE_BODY_BYTES = 18_500


def make_signature(timestamp: int, secret: str) -> str:
    """
    飞书自定义机器人签名。
    string_to_sign = timestamp + "\\n" + secret
    以该字符串为 HMAC key，对空字符串计算 SHA256，再 Base64。
    """
    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(string_to_sign, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def build_card(title: str, markdown: str) -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "update_multi": True,
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": markdown,
                        "text_align": "left",
                    }
                ],
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
                "template": "blue",
            },
        },
    }


def _fit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    飞书 webhook 请求体上限 20KB。
    这里保留安全余量，如果超限则逐步截断 markdown。
    """
    body = payload["card"]["body"]["elements"][0]["content"]
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > SAFE_BODY_BYTES:
        if len(body) <= 800:
            body = body[:500] + "\n\n内容过长，已截断。"
            break
        body = body[:-600]
        body = body.rstrip() + "\n\n…内容过长，已截断。"
        payload["card"]["body"]["elements"][0]["content"] = body
    return payload


def send_feishu(
    webhook_url: str,
    title: str,
    markdown: str,
    signing_secret: str | None = None,
    timeout: int = 20,
    retries: int = 3,
) -> tuple[bool, str]:
    payload = build_card(title, markdown)

    if signing_secret:
        timestamp = int(time.time())
        payload["timestamp"] = str(timestamp)
        payload["sign"] = make_signature(timestamp, signing_secret)

    payload = _fit_payload(payload)

    last_error = ""
    for attempt in range(retries):
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )

            if resp.status_code == 429 or resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                time.sleep(2 ** attempt)
                continue

            resp.raise_for_status()
            try:
                obj = resp.json()
            except Exception:
                obj = {}

            code = obj.get("code")
            if code is None:
                code = obj.get("StatusCode")

            if code not in (None, 0, "0"):
                return False, f"Feishu code={code}: {obj}"

            return True, "success"

        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(2 ** attempt)

    return False, last_error or "unknown error"
