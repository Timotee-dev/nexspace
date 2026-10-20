"""Tiny client for the Anthropic Messages API (standard library only)."""
import json
import urllib.error
import urllib.request

from django.conf import settings

ENDPOINT = "https://api.anthropic.com/v1/messages"


class NexAIError(Exception):
    pass


def complete(*, system, messages, max_tokens=900, temperature=0.2):
    if not settings.ANTHROPIC_API_KEY:
        raise NexAIError("NexAI's writing model isn't set up on this server.")
    body = json.dumps({
        "model": settings.NEXAI_MODEL, "max_tokens": max_tokens, "temperature": temperature,
        "system": system, "messages": messages,
    }).encode()
    request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "x-api-key": settings.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise NexAIError("NexAI is busy right now. Try again in a minute." if exc.code in (429, 529)
                         else "NexAI couldn't answer that right now.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise NexAIError("NexAI couldn't be reached. Check your connection and try again.") from exc
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    usage = data.get("usage", {})
    return text.strip(), usage.get("input_tokens", 0), usage.get("output_tokens", 0)
