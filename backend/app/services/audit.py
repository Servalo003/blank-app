"""Hash-chained audit log. Each entry's hash covers its content and the previous hash,
so any edit or deletion of history is detectable with ``verify_chain``."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.types import stable_hash
from app.models import AuditLog

GENESIS = "0" * 64


def record(s: Session, action: str, entity: str, entity_id: str, payload: dict[str, Any], actor: str = "system") -> AuditLog:
    prev = s.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(1)).first()
    prev_hash = prev.hash if prev else GENESIS
    body = {"actor": actor, "action": action, "entity": entity, "entity_id": entity_id, "payload": payload, "prev": prev_hash}
    row = AuditLog(
        actor=actor,
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload=payload,
        prev_hash=prev_hash,
        hash=stable_hash(body),
    )
    s.add(row)
    s.flush()
    return row


def verify_chain(s: Session) -> dict[str, Any]:
    prev_hash = GENESIS
    n = 0
    for row in s.scalars(select(AuditLog).order_by(AuditLog.id)):
        body = {
            "actor": row.actor,
            "action": row.action,
            "entity": row.entity,
            "entity_id": row.entity_id,
            "payload": row.payload,
            "prev": prev_hash,
        }
        if row.prev_hash != prev_hash or row.hash != stable_hash(body):
            return {"ok": False, "entries": n, "broken_at": row.id}
        prev_hash = row.hash
        n += 1
    return {"ok": True, "entries": n, "head": prev_hash}
