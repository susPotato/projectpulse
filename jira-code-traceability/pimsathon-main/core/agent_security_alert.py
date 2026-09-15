"""Email alert to the configured admin when an agent-security layer blocks an
action (core/agent_security.py) — reuses the SAME signed-in Microsoft 365
account as every other MS365 feature in the app (core/ms365_auth.py +
core/ms365_graph.send_mail), so no separate SMTP setup is required.

Best-effort only: a failed alert never raises — the block itself has already
happened by the time this is called, so a delivery failure here must not turn
a handled security event into an unhandled crash.
"""
from __future__ import annotations

from typing import Tuple

from . import ms365_graph
from .agent_security import SecurityVerdict
from .ms365_auth import Ms365AuthError, get_access_token


def notify_admin(config, verdict: SecurityVerdict, detail: str = "") -> Tuple[bool, str]:
    """Best-effort email to the configured admin address. Returns
    ``(sent, note)`` — ``note`` explains why nothing was sent when ``sent`` is
    False. Never raises."""
    sec = config.data.get("agent_security", {})
    admin_email = (sec.get("admin_email") or "").strip()
    if not admin_email:
        return False, "no admin_email configured in Settings"
    ms365 = config.ms365
    tenant_id, client_id = ms365.get("tenant_id", ""), ms365.get("client_id", "")
    try:
        token = get_access_token(tenant_id, client_id)
        subject = f"[Cowork Local] Cảnh báo bảo mật agent — lớp {verdict.layer}"
        body = (
            f"Lớp kiểm tra: {verdict.layer}\n"
            f"Lý do chặn: {verdict.reason}\n\n"
            f"Chi tiết:\n{detail}"
        )
        ms365_graph.send_mail(token, admin_email, subject, body)
        return True, "sent"
    except Ms365AuthError as exc:
        return False, f"Microsoft 365 chưa đăng nhập: {exc}"
    except ms365_graph.Ms365GraphError as exc:
        return False, f"Gửi email thất bại: {exc}"
    except Exception as exc:  # noqa: BLE001 - alerting must never crash the caller
        return False, str(exc)
