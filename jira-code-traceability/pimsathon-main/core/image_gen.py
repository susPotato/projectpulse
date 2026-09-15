"""Generate illustration images via an image/vision model, to support editing
and creating images inside files (e.g. a new picture for a slide, or a
standalone image file).

Uses the active provider's OpenAI-compatible ``/images/generations`` endpoint
(the internal gateway, OpenAI, or any compatible server) — the same base URL +
API key the chat model already uses. Best-effort and never raises: returns
``(ok, message_or_path)`` so the UI can fall back gracefully when the endpoint
or model isn't available. Pure logic (no Qt) → unit-testable with the HTTP
layer mocked.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional, Tuple

_DEFAULT_MODEL = "gpt-image-1"
_TIMEOUT = (15, 180)

# Substrings (lowercased) that mark a model as image-GENERATION capable, across
# providers/gateways — so the AI-edit model picker can suggest one regardless of
# whether the endpoint is OpenAI, Anthropic-routed, or another gateway.
_IMAGE_MODEL_MARKERS = (
    "image", "dall-e", "dalle", "imagen", "flux", "stable-diffusion", "sdxl",
    "sd3", "sd-", "grok-2-image", "seedream", "firefly", "titan-image", "photon",
)


def looks_like_image_model(name: str) -> bool:
    n = (name or "").lower()
    return any(m in n for m in _IMAGE_MODEL_MARKERS)


def suggest_image_model(models) -> Optional[str]:
    """Pick the most likely text-to-image model from a provider's model list
    (any provider). Returns None if none look image-capable."""
    for m in models or []:
        if looks_like_image_model(m):
            return m
    return None


def _conf(config):
    """(base_url, api_key, model, ca_bundle) for image generation, from the
    active provider + optional image_gen overrides in config."""
    prov = config.provider_conf(config.active_provider) if config else {}
    igen = (getattr(config, "data", {}) or {}).get("image_gen", {}) if config else {}
    base = (igen.get("base_url") or prov.get("base_url") or "").rstrip("/")
    key = igen.get("api_key") or prov.get("api_key") or ""
    model = igen.get("model") or _DEFAULT_MODEL
    ca = getattr(config, "ca_bundle", "") if config else ""
    return base, key, model, ca


def is_configured(config) -> bool:
    """True when an image endpoint can be attempted (a base URL is set). The
    actual call still degrades gracefully if the server/model can't generate."""
    base, _key, _model, _ca = _conf(config)
    return bool(base)


def generate_image(config, prompt: str, out_path: str,
                   model: Optional[str] = None, size: str = "1024x1024",
                   base_url: Optional[str] = None, api_key: Optional[str] = None) -> Tuple[bool, str]:
    """Generate an image for ``prompt`` and save it to ``out_path`` (PNG).
    Returns ``(True, out_path)`` or ``(False, reason)``. Never raises.

    By default the active provider's endpoint is used; pass ``base_url``/``api_key``
    to target a DIFFERENT provider (e.g. an image model discovered on another
    configured provider)."""
    prompt = (prompt or "").strip()
    if not prompt:
        return False, "empty prompt"
    base, key, cfg_model, ca = _conf(config)
    if base_url:                       # explicit provider override (cross-provider image model)
        base = base_url.rstrip("/")
        key = api_key or ""
    if not base:
        return False, "no image endpoint configured (set a provider base URL or image_gen.base_url)"
    from . import tls_trust

    url = base + "/images/generations"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"model": model or cfg_model, "prompt": prompt, "n": 1, "size": size}
    try:
        resp = tls_trust.request("post", url, ca_bundle=ca or None, json=payload,
                                 headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - endpoint/model unsupported, network, TLS…
        return False, f"image generation failed: {exc}"
    item = (data.get("data") or [{}])[0] if isinstance(data, dict) else {}
    try:
        if item.get("b64_json"):
            Path(out_path).write_bytes(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            img = tls_trust.request("get", item["url"], ca_bundle=ca or None, timeout=_TIMEOUT)
            img.raise_for_status()
            Path(out_path).write_bytes(img.content)
        else:
            return False, "no image returned by the model"
    except Exception as exc:  # noqa: BLE001
        return False, f"could not save image: {exc}"
    return True, out_path
