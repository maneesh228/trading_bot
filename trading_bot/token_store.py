from __future__ import annotations

import json
import os
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from kiteconnect import KiteConnect


@dataclass(frozen=True)
class KiteCredentials:
    api_key: str
    api_secret: str | None = None
    access_token: str | None = None


def load_local_credentials() -> KiteCredentials:
    load_dotenv()
    api_key = os.getenv("KITE_API_KEY")
    if not api_key:
        raise RuntimeError("KITE_API_KEY is required")
    return KiteCredentials(
        api_key=api_key,
        api_secret=os.getenv("KITE_API_SECRET"),
        access_token=os.getenv("KITE_ACCESS_TOKEN"),
    )


def load_secret_credentials(secret_name: str) -> KiteCredentials:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    raw = json.loads(response["SecretString"])
    api_key = raw.get("api_key")
    if not api_key:
        raise RuntimeError(f"api_key missing from secret {secret_name}")
    return KiteCredentials(
        api_key=api_key,
        api_secret=raw.get("api_secret"),
        access_token=raw.get("access_token"),
    )


def load_runtime_credentials() -> KiteCredentials:
    load_dotenv()
    secret_name = os.getenv("KITE_SECRET_NAME")
    if secret_name:
        return load_secret_credentials(secret_name)
    return load_local_credentials()


def save_access_token(secret_name: str, access_token: str) -> None:
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    raw = json.loads(response["SecretString"])
    raw["access_token"] = access_token
    try:
        client.put_secret_value(SecretId=secret_name, SecretString=json.dumps(raw))
    except ClientError:
        raise


def make_kite_client(credentials: KiteCredentials) -> KiteConnect:
    kite = KiteConnect(api_key=credentials.api_key)
    if credentials.access_token:
        kite.set_access_token(credentials.access_token)
    return kite


def make_kite_client_from_secret(secret_name: str) -> KiteConnect:
    return make_kite_client(load_secret_credentials(secret_name))
