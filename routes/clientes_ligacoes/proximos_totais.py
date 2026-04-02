from core.extensions import db
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.domain_utils import _cliente_tem_representante_vinculado


def calcular_totais_abas_proximos(current_user, codigos_representantes_vinculados):
    apenas_meus_px = current_user.tipo in ("consultor", "televendas")
    base_q_px = Cliente.query.filter_by(ativo=True)
    if apenas_meus_px:
        base_q_px = base_q_px.filter(Cliente.consultor_id == current_user.id)

    if current_user.tipo == "supervisor_repr":
        base_clientes_px = [
            c for c in base_q_px.all()
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        ]
        ids_px = [c.id for c in base_clientes_px if c.id]
        ligados_ids_px = set()
        if ids_px:
            rows_lig_px = (
                db.session.query(Ligacao.cliente_id)
                .filter(Ligacao.cliente_id.in_(ids_px))
                .distinct()
                .all()
            )
            ligados_ids_px = {row.cliente_id for row in rows_lig_px if row.cliente_id}

        total_pendentes_px = sum(1 for c in base_clientes_px if c.id not in ligados_ids_px)
        total_contatados_px = sum(
            1 for c in base_clientes_px
            if c.id in ligados_ids_px and c.proxima_ligacao is None
        )
        total_retornar_px = sum(1 for c in base_clientes_px if c.proxima_ligacao is not None)
        return total_pendentes_px, total_contatados_px, total_retornar_px

    clig_px = (
        db.session.query(Ligacao.cliente_id)
        .filter(Ligacao.consultor_id == current_user.id)
        .distinct()
    ) if apenas_meus_px else db.session.query(Ligacao.cliente_id).distinct()
    total_pendentes_px = base_q_px.filter(Cliente.id.notin_(clig_px)).count()
    total_contatados_px = base_q_px.filter(
        Cliente.id.in_(clig_px), Cliente.proxima_ligacao.is_(None)
    ).count()
    total_retornar_px = base_q_px.filter(Cliente.proxima_ligacao.isnot(None)).count()
    return total_pendentes_px, total_contatados_px, total_retornar_px
