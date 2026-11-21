"""Alert delivery (Telegram + Web Push) with idempotency and risk gating.

The dispatcher is the only path to the user's phone. It re-checks that the
signal carries a risk-engine decision whose alert level permits alerting, so a
bug elsewhere cannot leak an un-vetted signal. Each (signal, event, channel)
is delivered at most once (DB unique constraint).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AlertDelivery, PushSubscription, SignalRow
from app.risk.engine import AlertLevel

log = structlog.get_logger(__name__)

ALERTABLE = {AlertLevel.ACTIONABLE.value, AlertLevel.RESEARCH_ONLY.value}
EVENT_TITLES = {
    "CREATED": "New signal",
    "INVALIDATED": "Signal invalidated",
    "EXPIRED": "Signal expired",
    "STOP_HIT": "Stop level reached",
    "TARGET_HIT": "Target level reached",
}


class Channel(Protocol):
    name: str

    def configured(self) -> bool: ...

    async def send(self, title: str, body: str, data: dict[str, Any]) -> None: ...


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def format_alert(sig: SignalRow, event: str, detail: str = "") -> tuple[str, str]:
    level = "PAPER ALERT" if sig.alert_level == AlertLevel.ACTIONABLE.value else "RESEARCH ONLY"
    title = f"[{sig.provenance}] {EVENT_TITLES.get(event, event)}: {sig.direction} {sig.symbol} {sig.timeframe}"
    lines = [
        f"{level} · {sig.strategy} · strength {sig.score:.2f} (not a probability)",
        f"Signal bar closed: {_fmt_ts(sig.signal_ts)}",
    ]
    if event == "CREATED":
        lines += [
            f"Ref {sig.entry_ref:.5g} | Stop {sig.stop:.5g} | Target {sig.target:.5g}" if sig.stop and sig.target else "",
            f"Invalidation {sig.invalidation:.5g} · entry window until {_fmt_ts(sig.entry_window_until)}"
            if sig.invalidation
            else "",
            f"Risk: {sig.risk_status}"
            + (f" · est. risk {sig.risk.get('risk_amount', 0):.2f}" if sig.risk_status == "APPROVED" else ""),
            "Why: " + "; ".join(sig.reasons[:3]),
        ]
    if detail:
        lines.append(detail)
    lines.append("Decision support only - not financial advice. No order has been placed.")
    return title, "\n".join(x for x in lines if x)


class TelegramChannel:
    name = "telegram"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else None
        self.chat_id = settings.telegram_chat_id
        self.client = client

    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, title: str, body: str, data: dict[str, Any]) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": f"{title}\n{body}", "disable_web_page_preview": True}
        client = self.client or httpx.AsyncClient(timeout=10)
        try:
            r = await client.post(url, json=payload)
            if r.status_code >= 400:
                # never include the URL (contains the bot token) in errors
                raise RuntimeError(f"telegram HTTP {r.status_code}: {r.text[:200]}")
        finally:
            if self.client is None:
                await client.aclose()


class WebPushChannel:
    name = "webpush"

    def __init__(self, settings: Settings, session_factory: Any) -> None:
        self.settings = settings
        self.session_factory = session_factory

    def configured(self) -> bool:
        return bool(self.settings.vapid_public_key and self.settings.vapid_private_key)

    async def send(self, title: str, body: str, data: dict[str, Any]) -> None:
        from pywebpush import WebPushException, webpush

        assert self.settings.vapid_private_key is not None
        with self.session_factory() as s:
            subs = [(p.id, p.endpoint, p.keys) for p in s.scalars(select(PushSubscription))]
        if not subs:
            raise RuntimeError("no push subscriptions registered")
        errors = []
        for sid, endpoint, keys in subs:
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info={"endpoint": endpoint, "keys": keys},
                    data=json.dumps({"title": title, "body": body, **data}),
                    vapid_private_key=self.settings.vapid_private_key.get_secret_value(),
                    vapid_claims={"sub": self.settings.vapid_subject},
                    ttl=3600,
                )
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):  # subscription gone
                    with self.session_factory() as s:
                        row = s.get(PushSubscription, sid)
                        if row:
                            s.delete(row)
                else:
                    errors.append(str(exc)[:200])
        if errors and len(errors) == len(subs):
            raise RuntimeError("; ".join(errors))


class Dispatcher:
    def __init__(self, channels: list[Channel], max_attempts: int = 3) -> None:
        self.channels = channels
        self.max_attempts = max_attempts

    async def dispatch(self, s: Session, sig: SignalRow, event: str, detail: str = "") -> list[AlertDelivery]:
        if sig.alert_level not in ALERTABLE or sig.risk_status not in ("APPROVED", "RISK_UNAVAILABLE"):
            log.info("alert.blocked_by_risk", signal=sig.id, alert_level=sig.alert_level, risk=sig.risk_status)
            return []
        title, body = format_alert(sig, event, detail)
        out = []
        for ch in self.channels:
            if not ch.configured():
                continue
            row = AlertDelivery(signal_id=sig.id, event_kind=event, channel=ch.name, status="PENDING", attempts=0)
            try:
                with s.begin_nested():
                    s.add(row)
                    s.flush()
            except IntegrityError:
                continue  # already delivered/attempted: idempotent
            for attempt in range(1, self.max_attempts + 1):
                row.attempts = attempt
                try:
                    await ch.send(title, body, {"signal_id": sig.id, "event": event, "url": f"/signals/{sig.id}"})
                    row.status, row.error = "SENT", None
                    break
                except Exception as exc:
                    row.status, row.error = "FAILED", str(exc)[:500]
                    await asyncio.sleep(min(2**attempt, 10) if attempt < self.max_attempts else 0)
            log.info("alert.delivery", signal=sig.id, alert_event=event, channel=ch.name, status=row.status)
            out.append(row)
        return out
