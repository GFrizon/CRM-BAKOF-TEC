import time
import threading
from datetime import datetime

from sqlalchemy import func

from core.extensions import db
from core.models import Cliente, Ligacao, Usuario

_STATS_LOCKS_CACHE = {}
_STATS_LOCKS_CACHE_TTL = 180
_STATS_LOCKS_CACHE_LOCK = threading.Lock()


def _stats_cache_key(ids_locais):
    return frozenset(int(i) for i in ids_locais if i)


def invalidar_cache_stats_locks():
    with _STATS_LOCKS_CACHE_LOCK:
        _STATS_LOCKS_CACHE.clear()


def carregar_stats_e_locks_por_cliente_id(ids_locais):
    locks_por_cliente_id = {}
    stats_ligacoes_por_cliente_id = {}

    if not ids_locais:
        return locks_por_cliente_id, stats_ligacoes_por_cliente_id

    cache_key = _stats_cache_key(ids_locais)
    agora = time.perf_counter()
    item = _STATS_LOCKS_CACHE.get(cache_key)
    if item and (agora - item["ts"]) <= _STATS_LOCKS_CACHE_TTL:
        return item["locks"], item["stats"]

    clientes_base = (
        db.session.query(Cliente.id, Cliente.cd_cliente_oracle)
        .filter(Cliente.id.in_(ids_locais))
        .all()
    )
    cd_por_cliente_id = {
        int(row.id): str(row.cd_cliente_oracle or "").strip()
        for row in clientes_base
        if row.id
    }
    codigos_oracle = sorted({cd for cd in cd_por_cliente_id.values() if cd})
    ids_com_codigo = [cliente_id for cliente_id, cd in cd_por_cliente_id.items() if cd]
    ids_sem_codigo = [cliente_id for cliente_id, cd in cd_por_cliente_id.items() if not cd]

    locks_rows = (
        db.session.query(
            Cliente.id.label("cliente_id"),
            Cliente.em_atendimento_ate,
            Usuario.nome.label("usuario_nome"),
        )
        .outerjoin(Usuario, Usuario.id == Cliente.em_atendimento_por)
        .filter(
            Cliente.id.in_(ids_locais),
            Cliente.em_atendimento_por.isnot(None),
        )
        .all()
    )
    locks_por_cliente_id = {
        int(row.cliente_id): {
            "ativo": True,
            "por_nome": (row.usuario_nome or "Outro usuario"),
            "ate": (row.em_atendimento_ate.strftime("%d/%m/%Y %H:%M") if row.em_atendimento_ate else None),
        }
        for row in locks_rows
        if row.em_atendimento_ate and row.em_atendimento_ate > datetime.now()
    }

    ligacoes_por_codigo = {}
    if codigos_oracle:
        clientes_relacionados_codigo = (
            db.session.query(Cliente.id, Cliente.cd_cliente_oracle)
            .filter(Cliente.cd_cliente_oracle.in_(codigos_oracle))
            .all()
        )
        ids_relacionados_codigo = [int(row.id) for row in clientes_relacionados_codigo if row.id]
        cd_por_cliente_relacionado = {
            int(row.id): str(row.cd_cliente_oracle or "").strip()
            for row in clientes_relacionados_codigo
            if row.id and str(row.cd_cliente_oracle or "").strip()
        }

        ligacoes_agg_cd = (
            db.session.query(
                Ligacao.cliente_id.label("cliente_id"),
                func.count(Ligacao.id).label("total_ligacoes"),
                func.max(Ligacao.data_hora).label("ultima_ligacao"),
            )
            .filter(Ligacao.cliente_id.in_(ids_relacionados_codigo))
            .group_by(Ligacao.cliente_id)
            .all()
        )
        for row in ligacoes_agg_cd:
            cd = cd_por_cliente_relacionado.get(int(row.cliente_id or 0), "")
            if not cd:
                continue
            atual = ligacoes_por_codigo.get(cd)
            total_atual = int(atual.get("total_ligacoes") or 0) if atual else 0
            ultima_atual = atual.get("ultima_ligacao") if atual else None
            ultima_nova = row.ultima_ligacao
            ligacoes_por_codigo[cd] = {
                "total_ligacoes": total_atual + int(row.total_ligacoes or 0),
                "ultima_ligacao": (
                    ultima_nova
                    if (ultima_nova and (not ultima_atual or ultima_nova > ultima_atual))
                    else ultima_atual
                ),
            }

    for cliente_id in ids_com_codigo:
        cd = cd_por_cliente_id.get(cliente_id, "")
        stats_ligacoes_por_cliente_id[cliente_id] = dict(
            ligacoes_por_codigo.get(
                cd,
                {"total_ligacoes": 0, "ultima_ligacao": None},
            )
        )

    if ids_sem_codigo:
        ligacoes_agg_ids_sem_codigo = (
            db.session.query(
                Ligacao.cliente_id,
                func.count(Ligacao.id).label("total_ligacoes"),
                func.max(Ligacao.data_hora).label("ultima_ligacao"),
            )
            .filter(Ligacao.cliente_id.in_(ids_sem_codigo))
            .group_by(Ligacao.cliente_id)
            .all()
        )
        for row in ligacoes_agg_ids_sem_codigo:
            stats_ligacoes_por_cliente_id[int(row.cliente_id)] = {
                "total_ligacoes": int(row.total_ligacoes or 0),
                "ultima_ligacao": row.ultima_ligacao,
            }

    ultimos_ligadores_por_codigo = {}
    if codigos_oracle:
        ultimas_ligacoes_cd_subq = (
            db.session.query(
                Ligacao.cliente_id.label("cliente_id"),
                Ligacao.consultor_id.label("consultor_id"),
                Ligacao.data_hora,
                func.row_number().over(
                    partition_by=Ligacao.cliente_id,
                    order_by=(Ligacao.data_hora.desc(), Ligacao.id.desc()),
                ).label("rn"),
            )
            .filter(Ligacao.cliente_id.in_(ids_relacionados_codigo))
            .subquery()
        )
        ultimas_ligacoes_cd = (
            db.session.query(
                ultimas_ligacoes_cd_subq.c.cliente_id,
                ultimas_ligacoes_cd_subq.c.data_hora,
                Usuario.nome.label("ligador_nome"),
            )
            .join(Usuario, Usuario.id == ultimas_ligacoes_cd_subq.c.consultor_id)
            .filter(ultimas_ligacoes_cd_subq.c.rn == 1)
            .all()
        )
        melhor_por_codigo = {}
        for row in ultimas_ligacoes_cd:
            cd = cd_por_cliente_relacionado.get(int(row.cliente_id or 0), "")
            if not cd:
                continue
            atual = melhor_por_codigo.get(cd)
            chave_nova = (row.data_hora, int(row.cliente_id or 0))
            chave_atual = ((atual or {}).get("data_hora"), int((atual or {}).get("cliente_id") or 0))
            if not atual or chave_nova > chave_atual:
                melhor_por_codigo[cd] = {
                    "data_hora": row.data_hora,
                    "cliente_id": int(row.cliente_id or 0),
                    "ligador_nome": row.ligador_nome,
                }
        ultimos_ligadores_por_codigo = {
            cd: dados.get("ligador_nome")
            for cd, dados in melhor_por_codigo.items()
            if cd
        }

    if ids_sem_codigo:
        ultimas_ligacoes_ids_sem_codigo_subq = (
            db.session.query(
                Ligacao.cliente_id.label("cliente_id"),
                Ligacao.consultor_id.label("consultor_id"),
                Ligacao.data_hora,
                func.row_number().over(
                    partition_by=Ligacao.cliente_id,
                    order_by=(Ligacao.data_hora.desc(), Ligacao.id.desc()),
                ).label("rn"),
            )
            .filter(Ligacao.cliente_id.in_(ids_sem_codigo))
            .subquery()
        )
        ultimas_ligacoes_ids_sem_codigo = (
            db.session.query(
                ultimas_ligacoes_ids_sem_codigo_subq.c.cliente_id,
                ultimas_ligacoes_ids_sem_codigo_subq.c.data_hora,
                Usuario.nome.label("ligador_nome"),
            )
            .join(Usuario, Usuario.id == ultimas_ligacoes_ids_sem_codigo_subq.c.consultor_id)
            .filter(ultimas_ligacoes_ids_sem_codigo_subq.c.rn == 1)
            .all()
        )
        for row in ultimas_ligacoes_ids_sem_codigo:
            cliente_id = int(row.cliente_id or 0)
            if cliente_id not in stats_ligacoes_por_cliente_id:
                stats_ligacoes_por_cliente_id[cliente_id] = {
                    "total_ligacoes": 0,
                    "ultima_ligacao": row.data_hora,
                }
            stats_ligacoes_por_cliente_id[cliente_id]["ultima_ligacao_por"] = row.ligador_nome
    for cliente_id, cd in cd_por_cliente_id.items():
        if not cd or cd not in ultimos_ligadores_por_codigo:
            continue
        if cliente_id not in stats_ligacoes_por_cliente_id:
            stats_ligacoes_por_cliente_id[cliente_id] = {
                "total_ligacoes": 0,
                "ultima_ligacao": None,
            }
        stats_ligacoes_por_cliente_id[cliente_id]["ultima_ligacao_por"] = ultimos_ligadores_por_codigo[cd]

    with _STATS_LOCKS_CACHE_LOCK:
        _STATS_LOCKS_CACHE[cache_key] = {
            "ts": time.perf_counter(),
            "locks": locks_por_cliente_id,
            "stats": stats_ligacoes_por_cliente_id,
        }
        if len(_STATS_LOCKS_CACHE) > 16:
            itens = sorted(_STATS_LOCKS_CACHE.items(), key=lambda x: x[1]["ts"])
            _STATS_LOCKS_CACHE.clear()
            _STATS_LOCKS_CACHE.update(dict(itens[-12:]))

    return locks_por_cliente_id, stats_ligacoes_por_cliente_id
