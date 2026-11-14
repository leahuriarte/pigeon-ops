"""Minimal client for an OpenAI-compatible chat completions endpoint (standard library only).

Configuration comes from the environment, falling back to a repo-root `.env` file (gitignored):
PIGEONOPS_LLM_BASE_URL, PIGEONOPS_LLM_MODEL, PIGEONOPS_LLM_API_KEY, PIGEONOPS_LLM_TIMEOUT and
PIGEONOPS_LLM_VISION. Without an API key the client is disabled. Every failure raises
LLMUnavailable, and after one the client short-circuits for COOLDOWN_S seconds so a blocked
network does not stall every panel. Output from the model is advisory and never measured.
"""
import http.client
import json
import os
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
DEFAULT_BASE_URL = "https://aisquared-bolt-hackathon-proxy.hf.space/v1"
DEFAULT_MODEL = "bolt-instruct-v2"
DEFAULT_TIMEOUT_S = 30.0
COOLDOWN_S = 30.0
BASIS = "llm_generated"

_down_until = 0.0
_down_reason = ""


class LLMUnavailable(Exception):
    """The LLM could not give a usable answer; the caller shows a quiet 'unavailable' state."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def parse_env_file(path):
    """Read KEY=VALUE lines; ignore blanks, comments and malformed lines."""
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def config():
    """Current settings. Read on every call so tests can patch the environment or ENV_FILE."""
    file_values = parse_env_file(ENV_FILE)

    def get(name, default=None):
        value = os.environ.get(name)
        if value is None:
            value = file_values.get(name)
        return default if value is None or value == "" else value

    try:
        timeout = float(get("PIGEONOPS_LLM_TIMEOUT", DEFAULT_TIMEOUT_S))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_S
    return {
        "base_url": get("PIGEONOPS_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "model": get("PIGEONOPS_LLM_MODEL", DEFAULT_MODEL),
        "api_key": get("PIGEONOPS_LLM_API_KEY"),
        "timeout": timeout if timeout > 0 else DEFAULT_TIMEOUT_S,
        "vision": str(get("PIGEONOPS_LLM_VISION", "")).lower() in ("1", "true", "yes", "on"),
    }


def status():
    settings = config()
    return {
        "enabled": bool(settings["api_key"]),
        "model": settings["model"],
        "provider_host": urlparse(settings["base_url"]).hostname or "",
        "basis": BASIS,
    }


def reset_backoff():
    """Forget an earlier failure (tests use this)."""
    global _down_until, _down_reason
    _down_until, _down_reason = 0.0, ""


def _trip(reason):
    global _down_until, _down_reason
    _down_until, _down_reason = time.monotonic() + COOLDOWN_S, reason
    return LLMUnavailable(reason)


def _post(settings, body):
    request = Request(
        f"{settings['base_url']}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {settings['api_key']}"},
        method="POST",
    )
    with urlopen(request, timeout=settings["timeout"]) as response:
        return json.loads(response.read().decode("utf-8"))


def chat(messages, tools=None, tool_choice=None, max_tokens=1500):
    """Send one chat completion request and return the assistant message dict."""
    settings = config()
    if not settings["api_key"]:
        raise LLMUnavailable("disabled: no API key configured")
    if time.monotonic() < _down_until:
        raise LLMUnavailable(f"recently failed ({_down_reason}); retrying shortly")

    body = {"model": settings["model"], "messages": messages, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    if tool_choice is not None:
        body["tool_choice"] = tool_choice
    try:
        try:
            reply = _post(settings, body)
        except HTTPError as error:
            # tool_choice support is unknown on the hackathon service: retry once without it.
            if "tool_choice" in body and 400 <= error.code < 500:
                body.pop("tool_choice")
                reply = _post(settings, body)
            else:
                raise
    except HTTPError as error:
        raise _trip(f"HTTP {error.code}") from None
    except (URLError, socket.timeout, TimeoutError, ConnectionError) as error:
        raise _trip(f"unreachable: {getattr(error, 'reason', error)}") from None
    except (ValueError, UnicodeDecodeError):
        raise _trip("malformed response body") from None
    except (OSError, http.client.HTTPException) as error:
        raise _trip(f"network error: {error}") from None

    try:
        message = reply["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise _trip("malformed response: no message") from None
    if not isinstance(message, dict):
        raise _trip("malformed response: message is not an object")
    return message
