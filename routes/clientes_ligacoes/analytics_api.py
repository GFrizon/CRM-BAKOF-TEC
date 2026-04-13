import logging
from datetime import datetime, timedelta

from sqlalchemy import case, desc, extract, func

from core.extensions import db
from core.helpers import _percent, formatar_dinheiro
from core.models import Cliente, Ligacao, Usuario
from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.listagem_permissions import consultor_categoria_permitido_para_usuario
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados
from oracle_service import get_cliente_oracle_por_cnpj, get_pedidos_cliente_periodo_oracle

logger = logging.getLogger(__name__)


def _contagem_90_150_por_usuario_mesma_regra_lista_oracle(tipo_operador="consultor"):
    """Conta 90-150 por usuario ativo, aderente a regra da aba Oracle."""
    contagem_por_usuario = {}
    clientes_oracle = carregar_clientes_oracle_deduplicados(logger, periodo_oracle=None)
    if not clientes_oracle:
        return contagem_por_usuario

    codigos_oracle = {
        str(c.get("cd_cliente") or "").strip()
        for c in clientes_oracle
        if c.get("cd_cliente")
    }
    if not codigos_oracle:
        return contagem_por_usuario

    clientes_locais = (
        Cliente.query
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.in_(list(codigos_oracle)),
        )
        .all()
    )
    local_por_cd = {
        str(c.cd_cliente_oracle).strip(): c
        for c in clientes_locais
        if c.cd_cliente_oracle and c.consultor_id
    }
    if not local_por_cd:
        return contagem_por_usuario

    if tipo_operador == "consultor":
        _, mapa_nome_para_id_oracle = carregar_mapa_nome_para_id_usuarios_ativos()
        mapa_codigo_para_id_oracle = construir_mapa_codigo_para_id(mapa_nome_para_id_oracle)
    else:
        mapa_nome_para_id_oracle = {}
        mapa_codigo_para_id_oracle = {}

    for row in clientes_oracle:
        cd_cliente = str(row.get("cd_cliente") or "").strip()
        if not cd_cliente:
            continue
        cli_local = local_por_cd.get(cd_cliente)
        if not cli_local or not cli_local.consultor_id:
            continue

        usuario_id = int(cli_local.consultor_id)
        usuario = db.session.get(Usuario, usuario_id)
        if not usuario or not usuario.ativo or usuario.tipo != tipo_operador:
            continue

        consultor_cliente = str(row.get("consultor") or "").strip()
        if tipo_operador == "consultor":
            if not consultor_categoria_permitido_para_usuario(
                tipo_usuario="consultor",
                consultor_cliente=consultor_cliente,
                current_user_id=usuario_id,
                mapa_codigo_para_id=mapa_codigo_para_id_oracle,
                mapa_nome_para_id=mapa_nome_para_id_oracle,
            ):
                continue

        contagem_por_usuario[usuario_id] = contagem_por_usuario.get(usuario_id, 0) + 1

    return contagem_por_usuario


def parse_mes_ano(args):
    mes = int(args.get("mes", datetime.now().month))
    ano = int(args.get("ano", datetime.now().year))
    return mes, ano


def consultar_resultados_consultores_mes(mes, ano, meta_conversao=10.0, tipo_operador="consultor"):
    if mes < 1 or mes > 12:
        return {"ok": False, "erro": "Mês inválido"}, 400

    inicio = datetime(ano, mes, 1)
    fim = datetime(ano + (1 if mes == 12 else 0), (1 if mes == 12 else mes + 1), 1)

    subq_lig = (
        db.session.query(
            Ligacao.consultor_id.label("cid"),
            func.count(Ligacao.id).label("total"),
            func.sum(case((Ligacao.resultado == "comprou", 1), else_=0)).label("vendas"),
            func.sum(case((Ligacao.resultado == "retornar", 1), else_=0)).label("retornar"),
            func.sum(case((Ligacao.resultado == "comprou", Ligacao.valor_venda), else_=0)).label("receita"),
        )
        .filter(Ligacao.data_hora >= inicio, Ligacao.data_hora < fim)
        .group_by(Ligacao.consultor_id)
        .subquery()
    )

    agora = datetime.now()
    limite_90 = agora - timedelta(days=90)
    limite_150 = agora - timedelta(days=150)
    limite_151 = agora - timedelta(days=151)
    limite_180 = agora - timedelta(days=180)

    subq_carteira = (
        db.session.query(
            Cliente.consultor_id.label("cid"),
            func.sum(
                case(
                    (Cliente.ultimo_pedido_oracle.between(limite_180, limite_151), 1),
                    else_=0,
                )
            ).label("total_proximos"),
        )
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
        )
        .group_by(Cliente.consultor_id)
        .subquery()
    )

    # Totais gerais (mesma referência dos cards do dashboard supervisor)
    total_90_150_geral_oracle = len(carregar_clientes_oracle_deduplicados(logger, periodo_oracle=None) or [])
    total_proximos_geral_oracle = int(
        (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
            )
            .count()
        ) or 0
    )

    rows = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            func.coalesce(subq_lig.c.total, 0).label("total"),
            func.coalesce(subq_lig.c.vendas, 0).label("vendas"),
            func.coalesce(subq_lig.c.retornar, 0).label("retornar"),
            func.coalesce(subq_lig.c.receita, 0.0).label("receita"),
            func.coalesce(subq_carteira.c.total_proximos, 0).label("total_proximos"),
        )
        .outerjoin(subq_lig, subq_lig.c.cid == Usuario.id)
        .outerjoin(subq_carteira, subq_carteira.c.cid == Usuario.id)
        .filter(Usuario.tipo == tipo_operador, Usuario.ativo == True)
        .order_by(desc("receita"))
        .all()
    )

    receita_oracle_por_operador = {}
    try:
        compras_clientes = (
            db.session.query(
                Ligacao.consultor_id.label("operador_id"),
                Cliente.cd_cliente_oracle.label("cd_cliente_oracle"),
                Cliente.cnpj.label("cliente_cnpj"),
            )
            .join(Cliente, Cliente.id == Ligacao.cliente_id)
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(
                Ligacao.resultado == "comprou",
                Ligacao.data_hora >= inicio,
                Ligacao.data_hora < fim,
                Usuario.tipo == tipo_operador,
            )
            .distinct()
            .all()
        )

        cache_cd_por_cnpj = {}
        cache_total_oracle_cliente = {}
        for operador_id, cd_cliente_oracle, cliente_cnpj in compras_clientes:
            cd = str(cd_cliente_oracle or "").strip()
            if not cd:
                cnpj = str(cliente_cnpj or "").strip()
                if cnpj:
                    if cnpj not in cache_cd_por_cnpj:
                        try:
                            cli_oracle = get_cliente_oracle_por_cnpj(cnpj) or {}
                            cache_cd_por_cnpj[cnpj] = str(cli_oracle.get("cd_cliente") or "").strip()
                        except Exception:
                            cache_cd_por_cnpj[cnpj] = ""
                    cd = cache_cd_por_cnpj.get(cnpj, "")
            if not cd:
                continue
            if cd not in cache_total_oracle_cliente:
                pedidos = get_pedidos_cliente_periodo_oracle(
                    cd,
                    data_inicio=inicio,
                    data_fim=fim,
                ) or []
                cache_total_oracle_cliente[cd] = sum(
                    float(p.get("total_pedido") or 0.0) for p in pedidos
                )

            receita_oracle_por_operador[int(operador_id)] = (
                float(receita_oracle_por_operador.get(int(operador_id), 0.0))
                + float(cache_total_oracle_cliente.get(cd, 0.0))
            )
    except Exception as e:
        logger.warning(f"Falha ao calcular receita comprovada Oracle no fechamento: {e}")
        receita_oracle_por_operador = {}

    resultado = []
    contagem_90_150_oracle = _contagem_90_150_por_usuario_mesma_regra_lista_oracle(tipo_operador=tipo_operador)
    total_ligacoes_geral = 0
    total_vendas_geral = 0
    total_retornar_geral = 0
    total_receita_geral = 0.0
    total_receita_comprovada_oracle_geral = 0.0
    total_90_150_geral = 0
    total_proximos_geral = 0

    for uid, nome, total, vendas, retornar, receita, total_proximos in rows:
        total = int(total or 0)
        vendas = int(vendas or 0)
        retornar = int(retornar or 0)
        receita = float(receita or 0)
        receita_comprovada_oracle = float(receita_oracle_por_operador.get(int(uid), 0.0))
        total_90_150 = int(contagem_90_150_oracle.get(int(uid), 0))
        total_proximos = int(total_proximos or 0)
        conv = _percent(vendas, total) if total else 0.0

        total_ligacoes_geral += total
        total_vendas_geral += vendas
        total_retornar_geral += retornar
        total_receita_geral += receita
        total_receita_comprovada_oracle_geral += receita_comprovada_oracle
        total_90_150_geral += total_90_150
        total_proximos_geral += total_proximos

        resultado.append(
            {
                "id": uid,
                "nome": nome,
                "total_ligacoes": total,
                "vendas": vendas,
                "total_retornar": retornar,
                "conversao": round(conv, 1),
                "meta_conversao": float(meta_conversao),
                "atingiu_meta_conversao": bool(conv >= meta_conversao),
                "gap_meta_conversao": round(conv - float(meta_conversao), 1),
                "total_90_150": total_90_150,
                "total_proximos_inativacao": total_proximos,
                "receita": receita,
                "receita_fmt": formatar_dinheiro(receita),
                "receita_comprovada_oracle": receita_comprovada_oracle,
                "receita_comprovada_oracle_fmt": formatar_dinheiro(receita_comprovada_oracle),
            }
        )

    conversao_geral = _percent(total_vendas_geral, total_ligacoes_geral) if total_ligacoes_geral else 0.0
    totais = {
        "total_resultado_periodo": int(total_ligacoes_geral),
        "total_ligacoes": int(total_ligacoes_geral),
        "total_vendas": int(total_vendas_geral),
        "total_retornar": int(total_retornar_geral),
        # Totais da tabela seguem apenas operadores exibidos.
        "total_90_150": int(total_90_150_geral),
        "total_proximos_inativacao": int(total_proximos_geral),
        "total_90_150_geral_oracle": int(total_90_150_geral_oracle),
        "total_proximos_geral_oracle": int(total_proximos_geral_oracle),
        "conversao": round(conversao_geral, 1),
        "meta_conversao": float(meta_conversao),
        "receita": float(total_receita_geral),
        "receita_fmt": formatar_dinheiro(total_receita_geral),
        "receita_comprovada_oracle": float(total_receita_comprovada_oracle_geral),
        "receita_comprovada_oracle_fmt": formatar_dinheiro(total_receita_comprovada_oracle_geral),
    }

    return {"ok": True, "mes": mes, "ano": ano, "consultores": resultado, "totais": totais}, 200


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


def consultar_detalhe_conversao_operador_mes(operador_id, mes, ano, tipo_operador="consultor"):
    if mes < 1 or mes > 12:
        return {"ok": False, "erro": "Mes invalido"}, 400

    operador = Usuario.query.filter_by(id=operador_id, tipo=tipo_operador).first()
    if not operador:
        return {"ok": False, "erro": "Operador invalido"}, 404

    inicio = datetime(ano, mes, 1)
    fim = datetime(ano + (1 if mes == 12 else 0), (1 if mes == 12 else mes + 1), 1)
    agora_ref = datetime.now()

    def _classificar_carteira_cliente(origem, cd_oracle, ultimo_pedido_oracle, proxima_ligacao):
        origem_txt = str(origem or "").strip().lower()

        if ultimo_pedido_oracle:
            dias_sem = (agora_ref - ultimo_pedido_oracle).days
            if 90 <= dias_sem <= 150:
                return "Sem Pedido 90-150d"
            if 151 <= dias_sem <= 180:
                return "Prox. Inativacao"
            if 181 <= dias_sem <= 730:
                return "Clientes Inativos"

        if proxima_ligacao:
            return "Retornar"

        if origem_txt in ("manual", "importado_csv"):
            return "Clientes Especiais"

        return ""

    rows = (
        db.session.query(
            Cliente.id.label("cliente_id"),
            Cliente.nome.label("cliente_nome"),
            Cliente.cd_cliente_oracle.label("cd_cliente_oracle"),
            Cliente.cnpj.label("cliente_cnpj"),
            Cliente.origem.label("cliente_origem"),
            Cliente.ultimo_pedido_oracle.label("ultimo_pedido_oracle"),
            Cliente.proxima_ligacao.label("proxima_ligacao"),
            func.count(Ligacao.id).label("qtd_compras"),
            func.max(Ligacao.data_hora).label("ultima_compra_em"),
            func.sum(Ligacao.valor_venda).label("receita"),
        )
        .join(Ligacao, Ligacao.cliente_id == Cliente.id)
        .filter(
            Ligacao.consultor_id == operador_id,
            Ligacao.resultado == "comprou",
            Ligacao.data_hora >= inicio,
            Ligacao.data_hora < fim,
        )
        .group_by(
            Cliente.id,
            Cliente.nome,
            Cliente.cd_cliente_oracle,
            Cliente.cnpj,
            Cliente.origem,
            Cliente.ultimo_pedido_oracle,
            Cliente.proxima_ligacao,
        )
        .order_by(func.max(Ligacao.data_hora).desc())
        .all()
    )

    itens = []
    confirmados_oracle = 0
    cache_cd_por_cnpj = {}
    cache_pedidos_por_cd = {}
    for row in rows:
        cd = str(row.cd_cliente_oracle or "").strip()
        if not cd:
            cnpj = str(row.cliente_cnpj or "").strip()
            if cnpj:
                if cnpj not in cache_cd_por_cnpj:
                    try:
                        cli_oracle = get_cliente_oracle_por_cnpj(cnpj) or {}
                        cache_cd_por_cnpj[cnpj] = str(cli_oracle.get("cd_cliente") or "").strip()
                    except Exception:
                        cache_cd_por_cnpj[cnpj] = ""
                cd = cache_cd_por_cnpj.get(cnpj, "")
        pedidos_oracle = []
        if cd:
            if cd in cache_pedidos_por_cd:
                pedidos_oracle = cache_pedidos_por_cd[cd]
            else:
                try:
                    pedidos_oracle = get_pedidos_cliente_periodo_oracle(
                        cd,
                        data_inicio=inicio,
                        data_fim=fim,
                    ) or []
                except Exception:
                    pedidos_oracle = []
                cache_pedidos_por_cd[cd] = pedidos_oracle
        pedido_ref = pedidos_oracle[0] if pedidos_oracle else {}
        confirmado = bool(pedido_ref)
        if confirmado:
            confirmados_oracle += 1
        receita = float(row.receita or 0)
        pedido_total = float(pedido_ref.get("total_pedido") or 0) if pedido_ref else 0.0
        # Se a ligacao foi marcada como comprou sem valor local, usamos o valor confirmado do Oracle.
        receita_exibicao = receita
        if confirmado and receita_exibicao <= 0 and pedido_total > 0:
            receita_exibicao = pedido_total
        itens.append(
            {
                "cliente_id": int(row.cliente_id),
                "cliente_nome": row.cliente_nome or "-",
                "cd_cliente_oracle": cd,
                "qtd_compras": int(row.qtd_compras or 0),
                "ultima_compra_em": (
                    row.ultima_compra_em.strftime("%d/%m/%Y %H:%M")
                    if row.ultima_compra_em
                    else "-"
                ),
                "receita": receita_exibicao,
                "receita_fmt": formatar_dinheiro(receita_exibicao),
                "receita_local": receita,
                "receita_local_fmt": formatar_dinheiro(receita),
                "pedido_oracle_confirmado": confirmado,
                "pedido_oracle_codigo": str(pedido_ref.get("cd_pedido") or "").strip() if pedido_ref else "",
                "pedido_oracle_data": (
                    pedido_ref.get("dt_pedido").strftime("%d/%m/%Y")
                    if pedido_ref and pedido_ref.get("dt_pedido")
                    else ""
                ),
                "pedido_oracle_valor": pedido_total,
                "pedido_oracle_valor_fmt": (formatar_dinheiro(pedido_total) if pedido_ref else ""),
                "carteira_origem": _classificar_carteira_cliente(
                    row.cliente_origem,
                    cd,
                    row.ultimo_pedido_oracle,
                    row.proxima_ligacao,
                ),
            }
        )

    total_compradores = len(itens)
    payload = {
        "ok": True,
        "operador": {
            "id": int(operador.id),
            "nome": operador.nome,
            "tipo": operador.tipo,
        },
        "mes": int(mes),
        "ano": int(ano),
        "itens": itens,
        "resumo": {
            "compradores": int(total_compradores),
            "confirmados_oracle": int(confirmados_oracle),
            "nao_confirmados_oracle": int(max(0, total_compradores - confirmados_oracle)),
        },
    }
    return payload, 200
