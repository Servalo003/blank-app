"""Runtime risk configuration persisted in the DB (with audit), defaults from env."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppSetting
from app.risk.engine import RiskConfig
from app.services import audit

RISK_KEY = "risk_config"


def default_risk_config() -> RiskConfig:
    st = get_settings()
    return RiskConfig(
        risk_per_trade_pct=st.risk_per_trade_pct,
        max_daily_loss_pct=st.max_daily_loss_pct,
        max_open_positions=st.max_open_positions,
        research_only_alerts=st.research_only_alerts,
        allow_simulated_actionable=st.allow_simulated_actionable,
    )


def load_risk_config(s: Session) -> RiskConfig:
    base = default_risk_config().to_dict()
    row = s.get(AppSetting, RISK_KEY)
    if row and isinstance(row.value, dict):
        allowed = {f.name for f in fields(RiskConfig)}
        base.update({k: v for k, v in row.value.items() if k in allowed})
    return RiskConfig(**base)


def save_risk_config(s: Session, changes: dict[str, Any], actor: str = "api") -> RiskConfig:
    current = load_risk_config(s).to_dict()
    allowed = {f.name for f in fields(RiskConfig)}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"unknown risk settings: {sorted(unknown)}")
    current.update(changes)
    cfg = RiskConfig(**current)  # validates (incl. hard cap)
    row = s.get(AppSetting, RISK_KEY)
    if row is None:
        s.add(AppSetting(key=RISK_KEY, value=cfg.to_dict()))
    else:
        row.value = cfg.to_dict()
    audit.record(s, "risk_config.update", "settings", RISK_KEY, {"changes": changes, "result": cfg.to_dict()}, actor)
    return cfg
