from datetime import datetime, timedelta

from sqlalchemy import func

from core.extensions import db
from core.helpers import formatar_dinheiro
from core.models import Cliente, Ligacao


def parse_filtro_mes_ano(request_args, tipo_usuario):
    mes_filtro = None
    ano_filtro = None
    if tipo_usuario in ("consultor", "televendas"):
        mes_filtro = request_args.get("mes")
        ano_filtro = request_args.get("ano")
        if mes_filtro:
            mes_filtro = int(mes_filtro)
        if ano_filtro:
            ano_filtro = int(ano_filtro)
    return mes_filtro, ano_filtro


def montar_stats_consultor_televendas(current_user, total_oracle_badge):
    stats = {}
    if current_user.tipo not in ("consultor", "televendas"):
        return stats

    hoje_date = datetime.now().date()
    desde7 = datetime.now() - timedelta(days=7)
    desde30 = datetime.now() - timedelta(days=30)

    stats["total_clientes"] = Cliente.query.filter_by(
        consultor_id=current_user.id,
        ativo=True,
    ).count()

    stats["ligacoes_hoje"] = db.session.query(func.count(Ligacao.id)).filter(
        Ligacao.consultor_id == current_user.id,
        func.date(Ligacao.data_hora) == hoje_date,
    ).scalar() or 0

    stats["ligacoes_semana"] = db.session.query(func.count(Ligacao.id)).filter(
        Ligacao.consultor_id == current_user.id,
        Ligacao.data_hora >= desde7,
    ).scalar() or 0

    stats["ligacoes_mes"] = db.session.query(func.count(Ligacao.id)).filter(
        Ligacao.consultor_id == current_user.id,
        Ligacao.data_hora >= desde30,
    ).scalar() or 0

    stats["meta_diaria"] = current_user.meta_diaria or 10
    stats["progresso_meta"] = round(
        (stats["ligacoes_hoje"] / stats["meta_diaria"] * 100) if stats["meta_diaria"] > 0 else 0,
        1,
    )

    vendas_30 = db.session.query(func.count(Ligacao.id)).filter(
        Ligacao.consultor_id == current_user.id,
        Ligacao.data_hora >= desde30,
        Ligacao.resultado == "comprou",
    ).scalar() or 0
    positivos_30 = db.session.query(func.count(Ligacao.id)).filter(
        Ligacao.consultor_id == current_user.id,
        Ligacao.data_hora >= desde30,
        Ligacao.resultado.in_(("comprou", "relacionamento", "retornar")),
    ).scalar() or 0

    stats["taxa_conversao"] = round(
        (vendas_30 / stats["ligacoes_mes"] * 100) if stats["ligacoes_mes"] > 0 else 0,
        1,
    )
    stats["positivos_30"] = int(positivos_30)
    stats["taxa_positiva_30"] = round(
        (positivos_30 / stats["ligacoes_mes"] * 100) if stats["ligacoes_mes"] > 0 else 0,
        1,
    )
    stats["converteu_30"] = int(vendas_30)

    receita_total = db.session.query(func.sum(Ligacao.valor_venda)).filter(
        Ligacao.consultor_id == current_user.id,
        Ligacao.data_hora >= desde30,
        Ligacao.resultado == "comprou",
    ).scalar() or 0

    stats["receita_mes"] = formatar_dinheiro(receita_total)
    stats["clientes_90_150"] = int(total_oracle_badge or 0)
    return stats


def montar_meses_disponiveis(tipo_usuario):
    meses_disponiveis = []
    if tipo_usuario not in ("consultor", "televendas"):
        return meses_disponiveis

    data_atual = datetime.now()
    meses_nomes = {
        1: "Janeiro",
        2: "Fevereiro",
        3: "Março",
        4: "Abril",
        5: "Maio",
        6: "Junho",
        7: "Julho",
        8: "Agosto",
        9: "Setembro",
        10: "Outubro",
        11: "Novembro",
        12: "Dezembro",
    }
    for i in range(12):
        data = data_atual - timedelta(days=30 * i)
        meses_disponiveis.append(
            {
                "mes": data.month,
                "ano": data.year,
                "texto": f"{meses_nomes[data.month]}/{data.year}",
            }
        )
    return meses_disponiveis
