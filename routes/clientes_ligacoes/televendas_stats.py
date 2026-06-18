from datetime import datetime, timedelta

from sqlalchemy import func

from core.extensions import db
from core.models import Ligacao, Usuario


def montar_stats_produtividade_televendas():
    stats_televendas = []
    hoje_date = datetime.now().date()
    desde7 = datetime.now() - timedelta(days=7)
    desde30 = datetime.now() - timedelta(days=30)
    equipe_tv = (
        Usuario.query
        .filter(Usuario.tipo == "televendas", Usuario.ativo == True)
        .order_by(Usuario.nome.asc())
        .all()
    )
    for tv in equipe_tv:
        lig_hoje = (
            db.session.query(func.count(Ligacao.id))
            .filter(
                Ligacao.consultor_id == tv.id,
                func.date(Ligacao.data_hora) == hoje_date,
            )
            .scalar() or 0
        )
        lig_semana = (
            db.session.query(func.count(Ligacao.id))
            .filter(
                Ligacao.consultor_id == tv.id,
                Ligacao.data_hora >= desde7,
            )
            .scalar() or 0
        )
        lig_mes = (
            db.session.query(func.count(Ligacao.id))
            .filter(
                Ligacao.consultor_id == tv.id,
                Ligacao.data_hora >= desde30,
            )
            .scalar() or 0
        )
        stats_televendas.append(
            {
                "usuario_id": tv.id,
                "nome": tv.nome,
                "ligacoes_hoje": int(lig_hoje),
                "ligacoes_semana": int(lig_semana),
                "ligacoes_mes": int(lig_mes),
            }
        )
    return sorted(
        stats_televendas,
        key=lambda x: (-x["ligacoes_hoje"], -x["ligacoes_semana"], x["nome"]),
    )
