from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock

_LOCK = Lock()
_ACTIVE_USERS: dict[int, dict] = {}


def mark_user_activity(*, user_id: int, nome: str, tipo: str, ip: str, path: str) -> None:
    if not user_id:
        return
    now = datetime.now()
    with _LOCK:
        _ACTIVE_USERS[int(user_id)] = {
            "user_id": int(user_id),
            "nome": str(nome or ""),
            "tipo": str(tipo or ""),
            "ip": str(ip or ""),
            "path": str(path or ""),
            "last_seen": now,
        }


def get_active_users_recent(*, minutes: int = 15) -> list[dict]:
    horizon = datetime.now() - timedelta(minutes=max(int(minutes or 15), 1))
    with _LOCK:
        # Limpeza leve para nao crescer indefinidamente.
        stale_ids = [uid for uid, data in _ACTIVE_USERS.items() if data.get("last_seen") and data["last_seen"] < horizon]
        for uid in stale_ids:
            _ACTIVE_USERS.pop(uid, None)

        rows = [dict(v) for v in _ACTIVE_USERS.values() if v.get("last_seen") and v["last_seen"] >= horizon]
    rows.sort(key=lambda r: r.get("last_seen"), reverse=True)
    return rows
