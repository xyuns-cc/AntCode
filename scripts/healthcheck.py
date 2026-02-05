#!/usr/bin/env python3
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _jwt_exp(token: str):
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("utf-8")))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return float(exp)
        return None
    except Exception:
        return None


def _read_cached_token(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("access_token")
        exp = data.get("exp") or _jwt_exp(token or "")
        if not token or not exp:
            return None
        if time.time() >= float(exp) - 60:
            return None
        return token
    except Exception:
        return None


def _write_cached_token(path: str, token: str):
    try:
        exp = _jwt_exp(token)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"access_token": token, "exp": exp}, f)
    except Exception:
        pass


def _http_json(method: str, url: str, body=None, headers=None, timeout=5):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.getcode(), json.loads(raw) if raw else None


def main():
    port = os.getenv("SERVER_PORT", "8000")
    base_url = os.getenv("HEALTHCHECK_BASE_URL", f"http://127.0.0.1:{port}")

    username = (
        os.getenv("HEALTHCHECK_USERNAME")
        or os.getenv("DEFAULT_ADMIN_USERNAME")
        or "admin"
    )
    password = (
        os.getenv("HEALTHCHECK_PASSWORD")
        or os.getenv("DEFAULT_ADMIN_PASSWORD")
        or "Admin123!"
    )

    cache_path = os.getenv("HEALTHCHECK_TOKEN_CACHE", "/tmp/antcode_healthcheck_token.json")

    token = _read_cached_token(cache_path)
    if not token:
        try:
            code, payload = _http_json(
                "POST",
                f"{base_url}/api/v1/auth/login",
                body={"username": username, "password": password},
                timeout=5,
            )
        except urllib.error.HTTPError as e:
            sys.stderr.write(f"login http error: {e.code}\n")
            sys.exit(1)
        except Exception as e:
            sys.stderr.write(f"login error: {e}\n")
            sys.exit(1)

        if code != 200 or not isinstance(payload, dict):
            sys.stderr.write(f"login failed: http {code}\n")
            sys.exit(1)

        token = (payload.get("data") or {}).get("access_token")
        if not token:
            sys.stderr.write("login failed: missing access_token\n")
            sys.exit(1)

        _write_cached_token(cache_path, token)

    try:
        req = urllib.request.Request(
            f"{base_url}/api/v1/health",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.getcode() != 200:
                sys.stderr.write(f"health failed: http {resp.getcode()}\n")
                sys.exit(1)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"health http error: {e.code}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"health error: {e}\n")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
