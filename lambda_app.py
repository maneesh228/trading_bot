from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

import boto3

from trading_bot.token_store import (
    load_secret_credentials,
    make_kite_client,
    save_access_token,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def login_url_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    credentials = _load_lambda_credentials()
    kite = make_kite_client(credentials)
    return _json_response({"login_url": kite.login_url()})


def kite_callback_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    status = query.get("status")
    request_token = query.get("request_token")

    if status and status != "success":
        logger.warning("Kite login returned non-success status: %s", query)
        return _html_response("Kite login failed. Check CloudWatch logs.", 400)
    if not request_token:
        return _html_response("Missing request_token in Kite callback.", 400)

    secret_name = _secret_name()
    credentials = _load_lambda_credentials()
    if not credentials.api_secret:
        return _html_response("KITE api_secret is missing in AWS Secrets Manager.", 500)

    kite = make_kite_client(credentials)
    session = kite.generate_session(request_token, api_secret=credentials.api_secret)
    save_access_token(secret_name, session["access_token"])
    logger.info("Stored Kite access token in secret %s", secret_name)
    return _html_response("Kite access token saved. You can close this tab.")


def kite_postback_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    payload = _decode_body(event)
    logger.info("Kite postback payload: %s", payload)

    table_name = os.getenv("POSTBACK_TABLE_NAME")
    if table_name:
        item = {
            "event_id": {"S": event.get("requestContext", {}).get("requestId", context.aws_request_id)},
            "created_at": {"S": datetime.now(timezone.utc).isoformat()},
            "payload": {"S": json.dumps(payload)},
        }
        boto3.client("dynamodb").put_item(TableName=table_name, Item=item)

    return _json_response({"ok": True})


def _load_lambda_credentials():
    return load_secret_credentials(_secret_name())


def _secret_name() -> str:
    secret_name = os.getenv("KITE_SECRET_NAME")
    if not secret_name:
        raise RuntimeError("KITE_SECRET_NAME is required")
    return secret_name


def _decode_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    content_type = headers.get("content-type", "")
    if "application/json" in content_type:
        return json.loads(body or "{}")
    if "application/x-www-form-urlencoded" in content_type:
        return {key: values[-1] for key, values in parse_qs(body).items()}
    if body:
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}
    return {}


def _json_response(payload: dict[str, Any], status_code: int = 200) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }


def _html_response(message: str, status_code: int = 200) -> dict[str, Any]:
    body = f"<!doctype html><html><body><h1>{message}</h1></body></html>"
    return {
        "statusCode": status_code,
        "headers": {"content-type": "text/html"},
        "body": body,
    }
