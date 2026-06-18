from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import case, func

from core.extensions import db
from core.helpers import formatar_dinheiro
from core.models import Cliente, Ligacao


def _formatar_data_chart(valor):
    if hasattr(valor, "strftime"):
        return valor.strftime("%Y-%m-%d")
    return str(valor or "")[:10]


def _montar_janela_chart_operador(usuario_id, dias=30):
    hoje = datetime.now().date()
    fim = hoje
    inicio = fim - timedelta(days=dias - 1)
    total_periodo = (
        db.session.query(func.count(Ligacao.id))
        .filter(
            Ligacao.consultor_id == usuario_id,
            Ligacao.data_hora >= datetime.combine(inicio, datetime.min.time()),
            Ligacao.data_hora < datetime.combine(fim + timedelta(days=1), datetime.min.time()),
        )
        .scalar()
    ) or 0
    if total_periodo:
        return inicio, fim, f"{dias} dias"

    ultima_ligacao = (
        db.session.query(func.max(Ligacao.data_hora))
        .filter(Ligacao.consultor_id == usuario_id)
        .scalar()
    )
    if not ultima_ligacao:
        return inicio, fim, f"{dias} dias"

    fim = ultima_ligacao.date()
    inicio = fim - timedelta(days=dias - 1)
    return inicio, fim, f"Ate {fim.strftime('%d/%m')}"


def _montar_series_operador(usuario_id, dias=30):
    inicio, fim, periodo_label = _montar_janela_chart_operador(usuario_id, dias=dias)
    mapa_ligacoes = {
        _formatar_data_chart(data): int(total or 0)
        for data, total in (
            db.session.query(func.date(Ligacao.data_hora), func.count(Ligacao.id))
            .filter(
                Ligacao.consultor_id == usuario_id,
                Ligacao.data_hora >= datetime.combine(inicio, datetime.min.time()),
                Ligacao.data_hora < datetime.combine(fim + timedelta(days=1), datetime.min.time()),
            )
            .group_by(func.date(Ligacao.data_hora))
            .all()
        )
    }
    serie = []
    for i in range(dias):
        data_ref = inicio + timedelta(days=i)
        serie.append(
            {
                "data": data_ref.strftime("%Y-%m-%d"),
                "label": data_ref.strftime("%d/%m"),
                "total": int(mapa_ligacoes.get(data_ref.strftime("%Y-%m-%d"), 0)),
            }
        )
    return {
        "serie": serie,
        "inicio": inicio,
        "fim": fim,
        "periodo_label": periodo_label,
        "total": sum(item["total"] for item in serie),
    }


def _montar_resultados_operador(usuario_id, inicio, fim):
    rows = (
        db.session.query(Ligacao.resultado, func.count(Ligacao.id))
        .filter(
            Ligacao.consultor_id == usuario_id,
            Ligacao.data_hora >= datetime.combine(inicio, datetime.min.time()),
            Ligacao.data_hora < datetime.combine(fim + timedelta(days=1), datetime.min.time()),
        )
        .group_by(Ligacao.resultado)
        .all()
    )
    return {(resultado or "nao_comprou"): int(total or 0) for resultado, total in rows}


def _montar_comparativo_card(serie_atual, serie_anterior, fluxo=False):
    valor_atual = int(sum(serie_atual or [])) if fluxo else int((serie_atual or [0])[-1] or 0)
    valor_anterior = int(sum(serie_anterior or [])) if fluxo else int((serie_anterior or [0])[-1] or 0)
    diferenca = valor_atual - valor_anterior
    if diferenca == 0 and valor_atual == valor_anterior:
        texto = "Estavel vs 30d ant."
    elif valor_anterior:
        percentual = round((diferenca / valor_anterior) * 100, 1)
        texto = f"{percentual:+.1f}% vs 30d ant."
    elif valor_atual:
        texto = "Novo nos 30d" if fluxo else "+ em 30d"
    else:
        texto = "Sem movimento 30d" if fluxo else "Estavel 30d"
    return {
        "serie": [int(v or 0) for v in serie_atual],
        "anterior": [int(v or 0) for v in serie_anterior],
        "delta": diferenca,
        "texto": texto,
        "classe": "positive" if diferenca > 0 else ("negative" if diferenca < 0 else "neutral"),
    }


def _rolling_window(series, window_size):
    if not series:
        return []
    janela = max(int(window_size or 1), 1)
    soma = 0
    resultado = []
    for idx, valor in enumerate(series):
        soma += int(valor or 0)
        if idx >= janela:
            soma -= int(series[idx - janela] or 0)
        resultado.append(soma)
    return resultado


def _montar_series_clientes_ativos(usuario_id, datas):
    if not datas:
        return []
    inicio = min(datas)
    fim = max(datas)
    acumulado = (
        db.session.query(func.count(Cliente.id))
        .filter(
            Cliente.consultor_id == usuario_id,
            Cliente.ativo == True,
            Cliente.data_cadastro < datetime.combine(inicio, datetime.min.time()),
        )
        .scalar()
    ) or 0
    rows = (
        db.session.query(func.date(Cliente.data_cadastro), func.count(Cliente.id))
        .filter(
            Cliente.consultor_id == usuario_id,
            Cliente.ativo == True,
            Cliente.data_cadastro < datetime.combine(fim + timedelta(days=1), datetime.min.time()),
        )
        .group_by(func.date(Cliente.data_cadastro))
        .all()
    )
    por_data = {
        _formatar_data_chart(data_ref): int(total or 0)
        for data_ref, total in rows
    }
    serie = []
    for data_ref in sorted(datas):
        acumulado += por_data.get(data_ref.strftime("%Y-%m-%d"), 0)
        serie.append(acumulado)
    return serie


def _montar_series_ligacoes_agregadas(usuario_id, datas):
    if not datas:
        return {
            "total": [],
            "positivos": [],
            "comprou": [],
        }
    inicio = min(datas)
    fim = max(datas)
    rows = (
        db.session.query(
            func.date(Ligacao.data_hora).label("dia"),
            func.count(Ligacao.id).label("total"),
            func.sum(
                case(
                    (Ligacao.resultado.in_(("comprou", "relacionamento", "retornar")), 1),
                    else_=0,
                )
            ).label("positivos"),
            func.sum(
                case(
                    (Ligacao.resultado == "comprou", 1),
                    else_=0,
                )
            ).label("comprou"),
        )
        .filter(
            Ligacao.consultor_id == usuario_id,
            Ligacao.data_hora >= datetime.combine(inicio, datetime.min.time()),
            Ligacao.data_hora < datetime.combine(fim + timedelta(days=1), datetime.min.time()),
        )
        .group_by(func.date(Ligacao.data_hora))
        .all()
    )
    por_data = {
        _formatar_data_chart(row.dia): {
            "total": int(row.total or 0),
            "positivos": int(row.positivos or 0),
            "comprou": int(row.comprou or 0),
        }
        for row in rows
    }
    total = []
    positivos = []
    comprou = []
    for data_ref in sorted(datas):
        chave = data_ref.strftime("%Y-%m-%d")
        item = por_data.get(chave, {})
        total.append(int(item.get("total") or 0))
        positivos.append(int(item.get("positivos") or 0))
        comprou.append(int(item.get("comprou") or 0))
    return {
        "total": total,
        "positivos": positivos,
        "comprou": comprou,
    }


def _montar_series_90_150(usuario_id, datas):
    if not datas:
        return []
    inicio = min(datas) - timedelta(days=150)
    fim = max(datas) - timedelta(days=90)
    rows = (
        db.session.query(func.date(Cliente.ultimo_pedido_oracle), func.count(Cliente.id))
        .filter(
            Cliente.consultor_id == usuario_id,
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle >= datetime.combine(inicio, datetime.min.time()),
            Cliente.ultimo_pedido_oracle < datetime.combine(fim + timedelta(days=1), datetime.min.time()),
        )
        .group_by(func.date(Cliente.ultimo_pedido_oracle))
        .all()
    )
    por_dia_pedido = defaultdict(int)
    for data_ref, total in rows:
        por_dia_pedido[_formatar_data_chart(data_ref)] += int(total or 0)

    serie = []
    for data_ref in sorted(datas):
        inicio_janela = data_ref - timedelta(days=150)
        fim_janela = data_ref - timedelta(days=90)
        total = 0
        cursor = inicio_janela
        while cursor <= fim_janela:
            total += por_dia_pedido.get(cursor.strftime("%Y-%m-%d"), 0)
            cursor += timedelta(days=1)
        serie.append(total)
    return serie


def _montar_card_sparklines_usuario(usuario_id, tipo_usuario, total_oracle_badge, hoje_date):
    inicio_atual = hoje_date - timedelta(days=29)
    inicio_anterior = inicio_atual - timedelta(days=30)
    dias_atual = [inicio_atual + timedelta(days=i) for i in range(30)]
    dias_anterior = [inicio_anterior + timedelta(days=i) for i in range(30)]
    todos_os_dias = dias_anterior + dias_atual
    serie_clientes_60 = _montar_series_clientes_ativos(usuario_id, todos_os_dias)
    serie_ligacoes_60 = _montar_series_ligacoes_agregadas(usuario_id, todos_os_dias)
    serie_90_150_60 = _montar_series_90_150(usuario_id, todos_os_dias) if tipo_usuario == "consultor" else []

    idx_split = len(dias_anterior)
    clientes_anterior = serie_clientes_60[:idx_split]
    clientes_atual = serie_clientes_60[idx_split:]
    lig_dia_anterior = serie_ligacoes_60["total"][:idx_split]
    lig_dia_atual = serie_ligacoes_60["total"][idx_split:]
    lig_7_anterior = _rolling_window(lig_dia_anterior, 7)
    lig_7_atual = _rolling_window(lig_dia_atual, 7)
    lig_30_anterior = _rolling_window(lig_dia_anterior, 30)
    lig_30_atual = _rolling_window(lig_dia_atual, 30)
    positivos_30_anterior = _rolling_window(serie_ligacoes_60["positivos"][:idx_split], 30)
    positivos_30_atual = _rolling_window(serie_ligacoes_60["positivos"][idx_split:], 30)
    comprou_30_anterior = _rolling_window(serie_ligacoes_60["comprou"][:idx_split], 30)
    comprou_30_atual = _rolling_window(serie_ligacoes_60["comprou"][idx_split:], 30)

    cards = {
        "clientes": _montar_comparativo_card(
            clientes_atual,
            clientes_anterior,
        ),
        "ligacoes_hoje": _montar_comparativo_card(
            lig_dia_atual,
            lig_dia_anterior,
            fluxo=True,
        ),
        "ligacoes_semana": _montar_comparativo_card(
            lig_7_atual,
            lig_7_anterior,
        ),
        "ligacoes_mes": _montar_comparativo_card(
            lig_30_atual,
            lig_30_anterior,
        ),
        "positivos_30": _montar_comparativo_card(
            positivos_30_atual,
            positivos_30_anterior,
        ),
        "converteu_30": _montar_comparativo_card(
            comprou_30_atual,
            comprou_30_anterior,
        ),
    }
    if tipo_usuario == "consultor":
        cards["clientes_90_150"] = _montar_comparativo_card(
            serie_90_150_60[idx_split:],
            serie_90_150_60[:idx_split],
        )
        if not cards["clientes_90_150"]["serie"][-1] and total_oracle_badge:
            cards["clientes_90_150"]["serie"][-1] = int(total_oracle_badge or 0)
    return cards


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


def montar_stats_consultor_televendas(current_user, total_oracle_badge, total_ativos_badge=None):
    stats = {}
    if current_user.tipo not in ("consultor", "televendas"):
        return stats

    hoje_date = datetime.now().date()
    desde7 = datetime.now() - timedelta(days=7)
    desde30 = datetime.now() - timedelta(days=30)

    if current_user.tipo == "consultor":
        stats["total_clientes"] = int(total_ativos_badge or 0)
    else:
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
    stats["card_sparklines"] = _montar_card_sparklines_usuario(
        current_user.id,
        current_user.tipo,
        total_oracle_badge,
        hoje_date,
    )
    chart_operador = _montar_series_operador(current_user.id, dias=30)
    stats["chart_ligacoes_14d"] = chart_operador["serie"]
    stats["chart_periodo_label"] = chart_operador["periodo_label"]
    stats["chart_total_periodo"] = chart_operador["total"]
    stats["chart_resultados_30d"] = _montar_resultados_operador(
        current_user.id,
        chart_operador["inicio"],
        chart_operador["fim"],
    )
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
