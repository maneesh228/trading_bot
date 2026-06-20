from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode
from urllib.request import Request, urlopen

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def login_url_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    credentials = _load_lambda_credentials()
    query = urlencode({"api_key": credentials["api_key"], "v": "3"})
    return _json_response({"login_url": f"https://kite.zerodha.com/connect/login?{query}"})


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
    api_secret = credentials.get("api_secret")
    if not api_secret:
        return _html_response("KITE api_secret is missing in AWS Secrets Manager.", 500)

    access_token = _generate_kite_access_token(
        credentials["api_key"],
        api_secret,
        request_token,
    )
    _save_access_token(secret_name, access_token)
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


def _load_lambda_credentials() -> dict[str, str]:
    secret_name = _secret_name()
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    raw = json.loads(response["SecretString"])
    api_key = raw.get("api_key")
    if not api_key:
        raise RuntimeError(f"api_key missing from secret {secret_name}")
    return raw


def _save_access_token(secret_name: str, access_token: str) -> None:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    raw = json.loads(response["SecretString"])
    raw["access_token"] = access_token
    try:
        client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(raw))
    except ClientError:
        raise


def _generate_kite_access_token(api_key: str, api_secret: str, request_token: str) -> str:
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode("utf-8")).hexdigest()
    payload = urlencode(
        {
            "api_key": api_key,
            "request_token": request_token,
            "checksum": checksum,
        }
    ).encode("utf-8")
    request = Request(
        "https://api.kite.trade/session/token",
        data=payload,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    with urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))

    access_token = body.get("data", {}).get("access_token")
    if not access_token:
        raise RuntimeError(f"Kite session response did not include access_token: {body}")
    return access_token


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
