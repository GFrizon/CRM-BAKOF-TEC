from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from typing import Dict, List

_LOCK = threading.Lock()
_SUBSCRIBERS: Dict[int, List[queue.Queue]] = {}

_MAX_QUEUES_POR_USUARIO = 4
_MAX_USUARIOS = 256


def _agora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def subscrever(user_id: int) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=32)
    with _LOCK:
        if len(_SUBSCRIBERS) >= _MAX_USUARIOS and user_id not in _SUBSCRIBERS:
            return q
        filas = _SUBSCRIBERS.setdefault(user_id, [])
        if len(filas) >= _MAX_QUEUES_POR_USUARIO:
            try:
                antiga = filas.pop(0)
                try:
                    antiga.put_nowait(None)
                except Exception:
                    pass
            except IndexError:
                pass
        filas.append(q)
    return q


def cancelar(user_id: int, q: queue.Queue) -> None:
    with _LOCK:
        filas = _SUBSCRIBERS.get(user_id)
        if filas:
            try:
                filas.remove(q)
            except ValueError:
                pass
            if not filas:
                _SUBSCRIBERS.pop(user_id, None)


def publicar(evento: str, dados: dict, *, user_ids: list[int] | None = None) -> None:
    """
    Publica um evento SSE.

    - user_ids=None  -> envia para todos os conectados
    - user_ids=[1,2] -> envia apenas para esses usuários
    """
    payload = json.dumps({"evento": evento, "dados": dados, "ts": _agora_iso()}, ensure_ascii=False)
    mensagem = f"data: {payload}\n\n"

    with _LOCK:
        if user_ids is not None:
            alvos = {uid: _SUBSCRIBERS[uid] for uid in user_ids if uid in _SUBSCRIBERS}
        else:
            alvos = dict(_SUBSCRIBERS)

    for uid, filas in alvos.items():
        mortas = []
        for q in filas:
            try:
                q.put_nowait(mensagem)
            except queue.Full:
                mortas.append(q)
        if mortas:
            with _LOCK:
                filas_atuais = _SUBSCRIBERS.get(uid, [])
                for m in mortas:
                    try:
                        filas_atuais.remove(m)
                    except ValueError:
                        pass


def publicar_ligacao_registrada(cliente_id: int, consultor_nome: str, resultado: str) -> None:
    publicar(
        "ligacao_registrada",
        {"cliente_id": cliente_id, "consultor": consultor_nome, "resultado": resultado},
    )


def publicar_lock_assumido(cd_cliente: str, usuario_nome: str) -> None:
    publicar(
        "lock_assumido",
        {"cd_cliente": cd_cliente, "usuario": usuario_nome},
    )


def publicar_lock_liberado(cd_cliente: str) -> None:
    publicar(
        "lock_liberado",
        {"cd_cliente": cd_cliente},
    )
