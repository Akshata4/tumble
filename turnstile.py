"""Cloudflare Turnstile verification: the bot-check gate on POST /start.

Disabled by default (TURNSTILE_SECRET_KEY unset) so local dev and the CLI
are never blocked by it -- consistent with every other optional layer in
this project (CORS_ORIGINS, the daily budget). When it IS configured, a
missing or invalid token fails closed, same as any real deployment of it.
"""

import os

import httpx

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify(token: str | None, remote_ip: str | None = None) -> bool:
    secret = os.environ.get("TURNSTILE_SECRET_KEY")
    if not secret:
        return True
    if not token:
        return False

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        resp = httpx.post(VERIFY_URL, data=payload, timeout=10)
        return resp.json().get("success", False)
    except httpx.HTTPError:
        # Cloudflare being unreachable must never crash the request -- treat
        # it the same as a failed check rather than raising into the route.
        return False
