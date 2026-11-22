from __future__ import annotations

import ipaddress

# Shared third-party services that must not be routed wholesale into an AI egress.
# These show up in hand-written AI lists (auth0, Stripe, Sentry, ...), but they
# serve many unrelated applications.
SHARED_DOMAINS = {
    "auth0.com",
    "stripe.com",
    "algolia.net",
    "statsig.com",
    "featuregates.org",
    "identrust.com",
    "sentry.io",
    "statsigapi.net",
    "datadoghq.com",
    "browser-intake-datadoghq.com",
    "browser-intake-us5-datadoghq.com",
    "intercom.io",
    "intercomcdn.com",
    "cdn.usefathom.com",
    "arkoselabs.com",
    "challenges.cloudflare.com",
    "segment.io",
    "segment.com",
    "cdn.growthbook.io",
}

# Hosting/provider ASNs that are not service-owned. Matching one of these sends
# unrelated customers of the same hosting provider through the AI proxy.
BROAD_ASNS = {
    "20473",   # Vultr
    "14061",   # DigitalOcean
    "16276",   # OVH
    "24940",   # Hetzner
    "63949",   # Linode
    "9009",    # M247
    "45102",   # Alibaba Cloud
    "37963",   # Alibaba Cloud
    "45090",   # Tencent Cloud
    "132203",  # Tencent Cloud
}


def normalize_domain(value: str) -> str | None:
    v = value.strip().strip("'\"")
    if not v:
        return None
    v = v.lower().rstrip(".")
    if v.startswith("*."):
        v = v[2:]
    if v.startswith("."):
        v = v[1:]
    if not v or " " in v or "/" in v or ":" in v:
        return None
    if any(ch in v for ch in "*?^$[]()"):
        return None
    try:
        v = v.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if "." not in v:
        return None
    return v


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


def matches_any(domain: str, suffixes: set[str]) -> bool:
    return any(domain == s or domain.endswith("." + s) for s in suffixes)


def is_shared_domain(domain: str) -> bool:
    return matches_any(domain, SHARED_DOMAINS)


def is_shared_apex(domain: str) -> bool:
    return domain in SHARED_DOMAINS


def classify_role(domain: str) -> str:
    d = domain.lower()
    if is_shared_domain(d):
        return "shared"
    if any(k in d for k in ("auth", "login", "oauth", "sso", "arkose", "stripe", "challenge")):
        return "auth"
    if any(
        k in d
        for k in (
            "sentry",
            "statsig",
            "datadog",
            "telemetry",
            "analytics",
            "intercom",
            "fathom",
            "segment",
            "growthbook",
        )
    ):
        return "telemetry"
    if any(
        k in d
        for k in (
            "cdn",
            "static",
            "assets",
            "imgix",
            "cloudfront",
            "azureedge",
            "blob.core.windows.net",
            "cloudinary",
            "gstatic",
            "oaistatic",
            "oaiusercontent",
            "cursor-cdn",
            "codeiumdata",
        )
    ):
        return "cdn"
    if d.startswith("api.") or ".api." in d or d.endswith("api.com") or "api-" in d:
        return "api"
    return "web"
