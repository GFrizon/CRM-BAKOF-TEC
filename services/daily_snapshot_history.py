from __future__ import annotations

import json
import os
import threading
from bisect import bisect_right
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


_STORAGE_CACHE: dict[str, dict[str, Any]] = {}
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _data_ref_str(data_ref: date | None = None) -> str:
    return (data_ref or datetime.now().date()).isoformat()


def _carregar_bruto(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _cache_key_path(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _lock_local_para_path(path: Path) -> threading.RLock:
    chave = _cache_key_path(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(chave)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[chave] = lock
        return lock


@contextmanager
def _lock_exclusivo_snapshot(path: Path):
    """Serializa o read-modify-write entre threads e processos."""
    path = Path(path)
    lock_local = _lock_local_para_path(path)
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_local:
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)

            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _carregar_storage(path: Path) -> dict[str, Any]:
    path = Path(path)
    cache_key = _cache_key_path(path)
    try:
        stat = path.stat()
        assinatura = (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        assinatura = None

    cache_item = _STORAGE_CACHE.get(cache_key)
    if cache_item and cache_item.get("assinatura") == assinatura:
        return cache_item["storage"]

    storage = _normalizar_storage(_carregar_bruto(path))
    snapshots = storage.get("snapshots")
    datas_ordenadas = (
        sorted(str(chave).strip() for chave in snapshots.keys() if str(chave).strip())
        if isinstance(snapshots, dict)
        else []
    )
    _STORAGE_CACHE[cache_key] = {
        "assinatura": assinatura,
        "storage": storage,
        "datas_ordenadas": datas_ordenadas,
    }
    return storage


def _datas_snapshot_ordenadas(path: Path, storage: dict[str, Any] | None = None) -> list[str]:
    cache_item = _STORAGE_CACHE.get(_cache_key_path(path))
    if cache_item and isinstance(cache_item.get("datas_ordenadas"), list):
        return list(cache_item["datas_ordenadas"])
    storage = storage or _carregar_storage(path)
    snapshots = storage.get("snapshots")
    if not isinstance(snapshots, dict):
        return []
    return sorted(str(chave).strip() for chave in snapshots.keys() if str(chave).strip())


def _normalizar_storage(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"snapshots": {}}

    snapshots = payload.get("snapshots")
    if isinstance(snapshots, dict):
        return {"snapshots": snapshots}

    data_ref = str(payload.get("data_ref") or "").strip()
    itens = payload.get("itens")
    if data_ref and isinstance(itens, list):
        return {"snapshots": {data_ref: dict(payload)}}

    return {"snapshots": {}}


def _salvar_storage(path: Path, storage: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(storage, temp_file, ensure_ascii=False, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _compactar_snapshots(
    snapshots: dict[str, Any],
    *,
    data_ref: date,
    dias_detalhados: int,
    meses_historicos: int,
) -> dict[str, Any]:
    """Mantem dias recentes e o primeiro snapshot de cada mes antigo."""
    limite_detalhado = data_ref - timedelta(days=max(1, int(dias_detalhados)))
    limite_historico = data_ref - timedelta(days=max(1, int(meses_historicos)) * 31)
    recentes = {}
    primeiro_por_mes = {}

    for chave in sorted(snapshots):
        try:
            data_chave = date.fromisoformat(str(chave)[:10])
        except Exception:
            continue
        if data_chave >= limite_detalhado:
            recentes[chave] = snapshots[chave]
            continue
        if data_chave < limite_historico:
            continue
        mes_chave = (data_chave.year, data_chave.month)
        primeiro_por_mes.setdefault(mes_chave, (chave, snapshots[chave]))

    compactados = {
        chave: payload
        for chave, payload in primeiro_por_mes.values()
    }
    compactados.update(recentes)
    return {chave: compactados[chave] for chave in sorted(compactados)}


def salvar_snapshot_em_historico(
    path: Path,
    snapshot: dict[str, Any],
    *,
    data_ref: date | None = None,
    max_versoes: int = 400,
    dias_detalhados: int | None = None,
    meses_historicos: int = 24,
) -> dict[str, Any]:
    data_ref_txt = _data_ref_str(data_ref)
    data_ref_obj = data_ref or datetime.now().date()
    with _lock_exclusivo_snapshot(path):
        storage = _carregar_storage(path)
        snapshots = dict(storage.get("snapshots") or {})
        payload = dict(snapshot or {})
        payload["data_ref"] = data_ref_txt
        snapshots[data_ref_txt] = payload

        if dias_detalhados:
            snapshots = _compactar_snapshots(
                snapshots,
                data_ref=data_ref_obj,
                dias_detalhados=dias_detalhados,
                meses_historicos=meses_historicos,
            )
        elif max_versoes and len(snapshots) > max_versoes:
            chaves = sorted(snapshots.keys(), reverse=True)[:max_versoes]
            snapshots = {k: snapshots[k] for k in sorted(chaves)}

        storage["snapshots"] = snapshots
        _salvar_storage(path, storage)
        _STORAGE_CACHE.pop(_cache_key_path(path), None)
    return payload


def carregar_snapshot_do_historico(
    path: Path,
    data_ref: date | None = None,
) -> dict[str, Any] | None:
    storage = _carregar_storage(path)
    snapshots = storage.get("snapshots")
    if not isinstance(snapshots, dict):
        return None
    return snapshots.get(_data_ref_str(data_ref))


def listar_datas_snapshot(path: Path) -> list[str]:
    storage = _carregar_storage(path)
    return _datas_snapshot_ordenadas(path, storage)


def carregar_snapshot_na_data_ou_anterior(
    path: Path,
    data_ref: date | None,
) -> dict[str, Any] | None:
    data_ref_txt = _data_ref_str(data_ref)
    storage = _carregar_storage(path)
    snapshots = storage.get("snapshots")
    if not isinstance(snapshots, dict):
        return None

    datas_ordenadas = _datas_snapshot_ordenadas(path, storage)
    idx = bisect_right(datas_ordenadas, data_ref_txt) - 1
    if idx < 0:
        return None
    return snapshots.get(datas_ordenadas[idx])


def carregar_primeiro_snapshot_do_mes(
    path: Path,
    ano: int,
    mes: int,
) -> dict[str, Any] | None:
    prefixo = f"{int(ano):04d}-{int(mes):02d}-"
    storage = _carregar_storage(path)
    snapshots = storage.get("snapshots")
    if not isinstance(snapshots, dict):
        return None

    candidatas = [
        chave
        for chave in _datas_snapshot_ordenadas(path, storage)
        if str(chave).startswith(prefixo)
    ]
    if not candidatas:
        return None
    return snapshots.get(candidatas[0])
