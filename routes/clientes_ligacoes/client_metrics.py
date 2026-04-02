from sqlalchemy import func

from core.extensions import db
from core.models import Cliente, Ligacao, Usuario


def carregar_stats_e_locks_por_cliente_id(ids_locais):
    locks_por_cliente_id = {}
    stats_ligacoes_por_cliente_id = {}

    if not ids_locais:
        return locks_por_cliente_id, stats_ligacoes_por_cliente_id

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
            "ate": None,
        }
        for row in locks_rows
    }

    ligacoes_agg = (
        db.session.query(
            Ligacao.cliente_id,
            func.count(Ligacao.id).label("total_ligacoes"),
            func.max(Ligacao.data_hora).label("ultima_ligacao"),
        )
        .filter(Ligacao.cliente_id.in_(ids_locais))
        .group_by(Ligacao.cliente_id)
        .all()
    )
    stats_ligacoes_por_cliente_id = {
        row.cliente_id: {
            "total_ligacoes": int(row.total_ligacoes or 0),
            "ultima_ligacao": row.ultima_ligacao,
        }
        for row in ligacoes_agg
    }

    ultimas_ligacoes = (
        db.session.query(
            Ligacao.cliente_id,
            Ligacao.data_hora,
            Usuario.nome.label("ligador_nome"),
        )
        .join(Usuario, Usuario.id == Ligacao.consultor_id)
        .filter(Ligacao.cliente_id.in_(ids_locais))
        .order_by(Ligacao.cliente_id.asc(), Ligacao.data_hora.desc(), Ligacao.id.desc())
        .all()
    )
    vistos_ligador = set()
    for row in ultimas_ligacoes:
        if row.cliente_id in vistos_ligador:
            continue
        vistos_ligador.add(row.cliente_id)
        if row.cliente_id not in stats_ligacoes_por_cliente_id:
            stats_ligacoes_por_cliente_id[row.cliente_id] = {
                "total_ligacoes": 0,
                "ultima_ligacao": row.data_hora,
            }
        stats_ligacoes_por_cliente_id[row.cliente_id]["ultima_ligacao_por"] = row.ligador_nome

    return locks_por_cliente_id, stats_ligacoes_por_cliente_id
