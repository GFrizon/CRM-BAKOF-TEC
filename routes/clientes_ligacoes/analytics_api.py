from datetime import datetime

from sqlalchemy import case, desc, extract, func

from core.extensions import db
from core.helpers import _percent, formatar_dinheiro
from core.models import Ligacao, Usuario


def parse_mes_ano(args):
    mes = int(args.get("mes", datetime.now().month))
    ano = int(args.get("ano", datetime.now().year))
    return mes, ano


def consultar_resultados_consultores_mes(mes, ano):
    if mes < 1 or mes > 12:
        return {"ok": False, "erro": "Mês inválido"}, 400

    inicio = datetime(ano, mes, 1)
    fim = datetime(ano + (1 if mes == 12 else 0), (1 if mes == 12 else mes + 1), 1)

    subq = (
        db.session.query(
            Ligacao.consultor_id.label("cid"),
            func.count(Ligacao.id).label("total"),
            func.sum(case((Ligacao.resultado == "comprou", 1), else_=0)).label("vendas"),
            func.sum(case((Ligacao.resultado == "comprou", Ligacao.valor_venda), else_=0)).label("receita"),
        )
        .filter(Ligacao.data_hora >= inicio, Ligacao.data_hora < fim)
        .group_by(Ligacao.consultor_id)
        .subquery()
    )

    rows = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            func.coalesce(subq.c.total, 0).label("total"),
            func.coalesce(subq.c.vendas, 0).label("vendas"),
            func.coalesce(subq.c.receita, 0.0).label("receita"),
        )
        .outerjoin(subq, subq.c.cid == Usuario.id)
        .filter(Usuario.tipo == "consultor", Usuario.ativo == True)
        .order_by(desc("receita"))
        .all()
    )

    resultado = []
    for uid, nome, total, vendas, receita in rows:
        total = int(total or 0)
        vendas = int(vendas or 0)
        receita = float(receita or 0)
        conv = _percent(vendas, total) if total else 0.0
        resultado.append(
            {
                "id": uid,
                "nome": nome,
                "total_ligacoes": total,
                "vendas": vendas,
                "conversao": round(conv, 1),
                "receita": receita,
                "receita_fmt": formatar_dinheiro(receita),
            }
        )

    return {"ok": True, "mes": mes, "ano": ano, "consultores": resultado}, 200


def consultar_ligacoes_consultor_mes(consultor_id, mes, ano):
    ligacoes = (
        db.session.query(Ligacao)
        .filter(Ligacao.consultor_id == consultor_id)
        .filter(extract("month", Ligacao.data_hora) == mes)
        .filter(extract("year", Ligacao.data_hora) == ano)
        .order_by(Ligacao.data_hora.desc())
        .all()
    )

    resultado = []
    for lig in ligacoes:
        resultado.append(
            {
                "id": lig.id,
                "cliente_id": lig.cliente_id,
                "cliente_nome": lig.cliente.nome if lig.cliente else "N/A",
                "data_hora": lig.data_hora.strftime("%d/%m/%Y %H:%M"),
                "resultado": lig.resultado,
                "valor_venda": float(lig.valor_venda or 0),
                "valor_venda_fmt": formatar_dinheiro(lig.valor_venda),
                "observacao": lig.observacao,
            }
        )

    total_ligacoes = len(resultado)
    vendas = len([l for l in resultado if l["resultado"] == "comprou"])
    positivos = len([l for l in resultado if l["resultado"] in ("comprou", "relacionamento", "retornar")])
    receita_total = sum([l["valor_venda"] for l in resultado if l["resultado"] == "comprou"])
    taxa_conversao = _percent(vendas, total_ligacoes) if total_ligacoes else 0
    taxa_positiva = _percent(positivos, total_ligacoes) if total_ligacoes else 0

    return {
        "ok": True,
        "mes": mes,
        "ano": ano,
        "ligacoes": resultado,
        "estatisticas": {
            "total_ligacoes": total_ligacoes,
            "positivos": positivos,
            "vendas": vendas,
            "receita_total": receita_total,
            "receita_fmt": formatar_dinheiro(receita_total),
            "taxa_conversao": round(taxa_conversao, 1),
            "taxa_positiva": round(taxa_positiva, 1),
        },
    }
