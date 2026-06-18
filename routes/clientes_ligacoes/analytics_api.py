import logging
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import case, desc, extract, func

from core.extensions import db
from core.helpers import _percent, formatar_dinheiro
from core.models import Cliente, Ligacao, Usuario
from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.domain_utils import _resolver_consultor_id_por_categoria
from routes.clientes_ligacoes.local_client_dedup import escolher_melhor_cliente_por_codigo
from routes.clientes_ligacoes.listagem_permissions import consultor_categoria_permitido_para_usuario
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados
from services.carteiras_movimento_service import (
    carregar_movimentos_carteira_mes,
    carregar_movimentos_carteira_todos,
)
from services.ativos_snapshot_service import carregar_primeiro_snapshot_ativos_oracle_mes
from services.construtoras_snapshot_service import (
    carregar_primeiro_snapshot_construtoras_oracle_mes,
    rows_snapshot_construtoras,
)
from services.fechamento_snapshot_service import (
    carregar_snapshot_fechamento,
    salvar_snapshot_fechamento,
)
from oracle_service import (
    get_clientes_ativos_oracle,
    get_cliente_oracle_por_cnpj,
    get_pedidos_cliente_periodo_oracle,
    get_pedidos_reativacao_oracle,
)

logger = logging.getLogger(__name__)
_RESULTADOS_MES_CACHE = {}
_RESULTADOS_MES_CACHE_TTL = timedelta(minutes=5)


def _deduplicar_rows_oracle_por_cd(rows):
    por_cd = {}
    for row in list(rows or []):
        cd = str((row or {}).get("cd_cliente") or "").strip()
        if not cd:
            continue
        atual = por_cd.get(cd)
        if not atual:
            por_cd[cd] = row
            continue
        dt_novo = (row or {}).get("dt_pedido")
        dt_atual = (atual or {}).get("dt_pedido")
        if dt_novo and (not dt_atual or dt_novo > dt_atual):
            por_cd[cd] = row
    return list(por_cd.values())


def _parse_iso_date(valor):
    txt = str(valor or "").strip()
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt).date()
    except Exception:
        return None


def _ultimo_dia_mes(ano, mes):
    if int(mes) == 12:
        return datetime(int(ano) + 1, 1, 1).date() - timedelta(days=1)
    return datetime(int(ano), int(mes) + 1, 1).date() - timedelta(days=1)


def _carregar_mapa_usuarios_ativos_por_tipo(tipo_operador):
    usuarios = (
        Usuario.query
        .filter(Usuario.ativo == True, Usuario.tipo == tipo_operador)
        .order_by(Usuario.id)
        .all()
    )
    ids = [int(u.id) for u in usuarios]
    mapa_nome_para_id = {}
    mapa_codigo_para_id = {}
    if tipo_operador == "consultor":
        _, mapa_nome_para_id = carregar_mapa_nome_para_id_usuarios_ativos()
        mapa_codigo_para_id = construir_mapa_codigo_para_id(mapa_nome_para_id)
    return usuarios, ids, mapa_nome_para_id, mapa_codigo_para_id


def _resolver_operador_movimento(item, tipo_operador, mapa_nome_para_id, mapa_codigo_para_id):
    consultor_txt = str((item or {}).get("consultor") or "").strip()
    if not consultor_txt:
        return None
    return _resolver_consultor_id_por_categoria(
        consultor_txt,
        mapa_codigo_para_id,
        mapa_nome_para_id,
    )


def _contagem_proximos_por_usuario_atual(tipo_operador):
    rows = (
        db.session.query(
            Cliente.consultor_id,
            func.count(Cliente.id),
        )
        .join(Usuario, Usuario.id == Cliente.consultor_id)
        .filter(
            Usuario.ativo == True,
            Usuario.tipo == tipo_operador,
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(
                datetime.now() - timedelta(days=180),
                datetime.now() - timedelta(days=151),
            ),
        )
        .group_by(Cliente.consultor_id)
        .all()
    )
    return {int(uid): int(total or 0) for uid, total in rows if uid}


def _contagem_construtoras_por_usuario_snapshot(ano, mes, tipo_operador):
    snapshot = carregar_primeiro_snapshot_construtoras_oracle_mes(ano, mes)
    if not snapshot:
        return {"disponivel": False}
    total_snapshot = int((snapshot or {}).get("total") or 0)

    clientes_oracle = rows_snapshot_construtoras(snapshot)
    if not clientes_oracle:
        return {
            "disponivel": True,
            "data_ref": snapshot.get("data_ref"),
            "primeira_data": snapshot.get("data_ref"),
            "contagem_por_usuario": {},
            "total": 0,
            "total_snapshot": total_snapshot,
        }

    codigos_oracle = {
        str(c.get("cd_cliente") or "").strip()
        for c in clientes_oracle
        if c.get("cd_cliente")
    }
    if tipo_operador == "televendas":
        return {
            "disponivel": True,
            "data_ref": snapshot.get("data_ref"),
            "primeira_data": snapshot.get("data_ref"),
            "contagem_por_usuario": {},
            "total": total_snapshot,
            "total_snapshot": total_snapshot,
        }
    if not codigos_oracle:
        return {
            "disponivel": True,
            "data_ref": snapshot.get("data_ref"),
            "primeira_data": snapshot.get("data_ref"),
            "contagem_por_usuario": {},
            "total": 0,
            "total_snapshot": total_snapshot,
        }

    _, ids_ativos, mapa_nome_para_id, mapa_codigo_para_id = _carregar_mapa_usuarios_ativos_por_tipo(
        tipo_operador
    )
    ids_ativos_set = {int(uid) for uid in ids_ativos}
    contagem_por_usuario = {}

    for row in clientes_oracle:
        consultor_txt = str((row or {}).get("consultor") or "").strip()
        if not consultor_txt:
            continue
        uid = _resolver_consultor_id_por_categoria(
            consultor_txt,
            mapa_codigo_para_id,
            mapa_nome_para_id,
        )
        if not uid or int(uid) not in ids_ativos_set:
            continue
        uid = int(uid)
        contagem_por_usuario[uid] = contagem_por_usuario.get(uid, 0) + 1

    return {
        "disponivel": True,
        "data_ref": snapshot.get("data_ref"),
        "primeira_data": snapshot.get("data_ref"),
        "contagem_por_usuario": contagem_por_usuario,
        "total": int(sum(contagem_por_usuario.values())),
        "total_snapshot": total_snapshot,
    }


def _contagem_ativos_por_usuario_snapshot(ano, mes, tipo_operador):
    snapshot = carregar_primeiro_snapshot_ativos_oracle_mes(ano, mes)
    if not snapshot:
        return {"disponivel": False}
    total_snapshot = int((snapshot or {}).get("total") or 0)

    itens = list((snapshot or {}).get("itens") or [])
    _, ids_ativos, mapa_nome_para_id, mapa_codigo_para_id = _carregar_mapa_usuarios_ativos_por_tipo(
        tipo_operador
    )
    ids_ativos_set = {int(uid) for uid in ids_ativos}

    if tipo_operador == "consultor":
        itens_com_consultor = [
            item
            for item in itens
            if str((item or {}).get("consultor") or "").strip()
        ]
        usar_rows_oracle = list(itens_com_consultor)

        snapshot_data_ref = _parse_iso_date(snapshot.get("data_ref"))
        if not usar_rows_oracle and snapshot_data_ref == datetime.now().date():
            try:
                usar_rows_oracle = _deduplicar_rows_oracle_por_cd(
                    get_clientes_ativos_oracle() or []
                )
            except Exception as e:
                logger.warning(
                    "Falha ao complementar snapshot de ativos com consultor Oracle: %s",
                    e,
                )
                usar_rows_oracle = []

        if usar_rows_oracle:
            contagem_por_usuario = {}
            for item in usar_rows_oracle:
                consultor_txt = str((item or {}).get("consultor") or "").strip()
                if not consultor_txt:
                    continue
                uid = _resolver_consultor_id_por_categoria(
                    consultor_txt,
                    mapa_codigo_para_id,
                    mapa_nome_para_id,
                )
                if not uid or int(uid) not in ids_ativos_set:
                    continue
                uid = int(uid)
                contagem_por_usuario[uid] = contagem_por_usuario.get(uid, 0) + 1

            return {
                "disponivel": True,
                "data_ref": snapshot.get("data_ref"),
                "primeira_data": snapshot.get("data_ref"),
                "contagem_por_usuario": contagem_por_usuario,
                "total": int(sum(contagem_por_usuario.values())),
                "total_snapshot": total_snapshot,
            }

    codigos_oracle = {
        str((item or {}).get("cd_cliente") or "").strip()
        for item in itens
        if str((item or {}).get("cd_cliente") or "").strip()
    }
    if not codigos_oracle:
        return {
            "disponivel": True,
            "data_ref": snapshot.get("data_ref"),
            "primeira_data": snapshot.get("data_ref"),
            "contagem_por_usuario": {},
            "total": 0,
            "total_snapshot": total_snapshot,
        }

    clientes_locais = (
        Cliente.query
        .join(Usuario, Usuario.id == Cliente.consultor_id)
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.in_(list(codigos_oracle)),
            Usuario.ativo == True,
            Usuario.tipo == tipo_operador,
        )
        .all()
    )
    clientes_locais_por_cd = escolher_melhor_cliente_por_codigo(clientes_locais)
    contagem_por_usuario = {}
    for cliente in clientes_locais_por_cd.values():
        if not cliente.consultor_id:
            continue
        uid = int(cliente.consultor_id)
        contagem_por_usuario[uid] = contagem_por_usuario.get(uid, 0) + 1

    return {
        "disponivel": True,
        "data_ref": snapshot.get("data_ref"),
        "primeira_data": snapshot.get("data_ref"),
        "contagem_por_usuario": contagem_por_usuario,
        "total": int(sum(contagem_por_usuario.values())),
        "total_snapshot": total_snapshot,
    }


def _contagem_reativacoes_por_usuario(mes, ano, tipo_operador):
    inicio = datetime(int(ano), int(mes), 1)
    fim = datetime(
        int(ano) + (1 if int(mes) == 12 else 0),
        (1 if int(mes) == 12 else int(mes) + 1),
        1,
    )
    try:
        pedidos = get_pedidos_reativacao_oracle(
            inicio,
            fim,
            151,
            180,
        ) or []
    except Exception as e:
        logger.warning("Falha ao buscar reativacoes do fechamento: %s", e)
        return {}

    codigos_clientes = sorted(
        {
            str(p.get("cd_cliente") or "").strip()
            for p in pedidos
            if str(p.get("cd_cliente") or "").strip()
        }
    )
    if not codigos_clientes:
        return {}

    clientes = (
        Cliente.query
        .join(Usuario, Usuario.id == Cliente.consultor_id)
        .filter(
            Cliente.cd_cliente_oracle.in_(codigos_clientes),
            Usuario.ativo == True,
            Usuario.tipo == tipo_operador,
        )
        .all()
    )
    cliente_por_codigo = {
        str(c.cd_cliente_oracle).strip(): c
        for c in clientes
        if c.cd_cliente_oracle and c.consultor_id
    }

    contagem = {}
    for pedido in pedidos:
        cd_cliente = str(pedido.get("cd_cliente") or "").strip()
        cli = cliente_por_codigo.get(cd_cliente)
        if not cli or not cli.consultor_id:
            continue
        uid = int(cli.consultor_id)
        contagem[uid] = int(contagem.get(uid, 0)) + 1
    return contagem


def _reconstruir_snapshot_historico_carteira(carteira, tipo_operador, contagem_atual, ano, mes):
    movimentos = carregar_movimentos_carteira_todos(carteira)
    if not movimentos:
        return {"disponivel": False}

    alvo = _ultimo_dia_mes(ano, mes)
    movimentos_validos = []
    for mov in movimentos:
        data_ref = _parse_iso_date((mov or {}).get("data_ref"))
        if data_ref:
            movimentos_validos.append((data_ref, mov))
    if not movimentos_validos:
        return {"disponivel": False}

    primeira_data = movimentos_validos[0][0]
    datas_no_mes = [data_ref for data_ref, _ in movimentos_validos if data_ref.year == int(ano) and data_ref.month == int(mes)]
    if not datas_no_mes:
        return {
            "disponivel": False,
            "primeira_data": primeira_data.isoformat(),
        }

    # Regra do fechamento: usar a primeira foto disponivel do mes
    # como base historica para comparacao mensal.
    data_alvo = min(datas_no_mes)
    data_mais_recente = movimentos_validos[-1][0]

    _, _, mapa_nome_para_id, mapa_codigo_para_id = _carregar_mapa_usuarios_ativos_por_tipo(tipo_operador)
    snapshot = {int(uid): int(total or 0) for uid, total in (contagem_atual or {}).items()}

    if data_alvo < data_mais_recente:
        for data_ref, mov in reversed(movimentos_validos):
            if data_ref <= data_alvo:
                break
            for item in list((mov or {}).get("entraram") or []):
                uid = _resolver_operador_movimento(item, tipo_operador, mapa_nome_para_id, mapa_codigo_para_id)
                if uid:
                    snapshot[uid] = int(snapshot.get(uid, 0)) - 1
            for item in list((mov or {}).get("sairam") or []):
                uid = _resolver_operador_movimento(item, tipo_operador, mapa_nome_para_id, mapa_codigo_para_id)
                if uid:
                    snapshot[uid] = int(snapshot.get(uid, 0)) + 1

    snapshot = {uid: max(0, int(total or 0)) for uid, total in snapshot.items()}
    return {
        "disponivel": True,
        "data_ref": data_alvo.isoformat(),
        "contagem_por_usuario": snapshot,
        "total": int(sum(snapshot.values())),
        "primeira_data": primeira_data.isoformat(),
    }


def _mes_ano_anterior(mes, ano):
    mes = int(mes)
    ano = int(ano)
    if mes == 1:
        return 12, ano - 1
    return mes - 1, ano


def _texto_periodo_curto(mes, ano):
    nomes = {
        1: "Jan",
        2: "Fev",
        3: "Mar",
        4: "Abr",
        5: "Mai",
        6: "Jun",
        7: "Jul",
        8: "Ago",
        9: "Set",
        10: "Out",
        11: "Nov",
        12: "Dez",
    }
    return f"{nomes.get(int(mes), str(mes))}/{int(ano)}"


def _fmt_delta_numero(atual, anterior):
    if atual is None or anterior is None:
        return "Sem historico"
    delta = int(atual) - int(anterior)
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def _fmt_delta_percentual(atual, anterior):
    if atual is None or anterior is None:
        return "Sem historico"
    delta = round(float(atual) - float(anterior), 1)
    if delta > 0:
        return f"+{delta:.1f} p.p."
    return f"{delta:.1f} p.p."


def _fmt_delta_receita(atual, anterior):
    if atual is None or anterior is None:
        return "Sem historico"
    delta = float(atual) - float(anterior)
    sinal = "+" if delta > 0 else ""
    return f"{sinal}{formatar_dinheiro(delta)}"


def _classe_variacao(delta, *, menor_melhor=False):
    if delta is None:
        return "neutral"
    if delta == 0:
        return "neutral"
    positivo = delta < 0 if menor_melhor else delta > 0
    return "positive" if positivo else "negative"


def _icone_variacao(delta):
    if delta is None or delta == 0:
        return "bi-dash-lg"
    return "bi-arrow-up-right" if delta > 0 else "bi-arrow-down-right"


def _montar_card_comparativo(titulo, atual, anterior, *, formato="numero", menor_melhor=False):
    if formato == "percentual":
        atual_txt = f"{round(float(atual or 0), 1)}%"
        anterior_txt = f"{round(float(anterior or 0), 1)}%" if anterior is not None else "Sem historico"
        delta_txt = _fmt_delta_percentual(atual, anterior)
        delta_raw = None if anterior is None else round(float(atual or 0) - float(anterior or 0), 1)
    elif formato == "receita":
        atual_txt = formatar_dinheiro(float(atual or 0))
        anterior_txt = formatar_dinheiro(float(anterior or 0)) if anterior is not None else "Sem historico"
        delta_txt = _fmt_delta_receita(atual, anterior)
        delta_raw = None if anterior is None else round(float(atual or 0) - float(anterior or 0), 2)
    else:
        atual_txt = str(int(atual or 0))
        anterior_txt = str(int(anterior or 0)) if anterior is not None else "Sem historico"
        delta_txt = _fmt_delta_numero(atual, anterior)
        delta_raw = None if anterior is None else int(atual or 0) - int(anterior or 0)
    return {
        "titulo": titulo,
        "atual": atual_txt,
        "anterior": anterior_txt,
        "delta": delta_txt,
        "classe": _classe_variacao(delta_raw, menor_melhor=menor_melhor),
        "icone": _icone_variacao(delta_raw),
    }


def _montar_movimento_carteiras_mes(ano, mes, tipo_operador, consultores):
    mapa_consultores = {int(c.get("id")): str(c.get("nome") or "-") for c in list(consultores or []) if c.get("id")}
    _, _, mapa_nome_para_id, mapa_codigo_para_id = _carregar_mapa_usuarios_ativos_por_tipo(tipo_operador)
    acumulado = {}
    houve_dados = False

    def _garantir(uid):
        if not uid:
            return None
        obj = acumulado.get(int(uid))
        if obj:
            return obj
        obj = {
            "id": int(uid),
            "nome": mapa_consultores.get(int(uid), f"Operador #{int(uid)}"),
            "entraram_90_150": 0,
            "sairam_90_150": 0,
            "entraram_proximos": 0,
            "sairam_proximos": 0,
        }
        acumulado[int(uid)] = obj
        return obj

    def _processar(carteira_key, campo_entrada, campo_saida):
        nonlocal houve_dados
        movimentos = carregar_movimentos_carteira_mes(carteira_key, ano, mes)
        if movimentos:
            houve_dados = True
        for mov in movimentos:
            for item in list((mov or {}).get("entraram") or []):
                uid = _resolver_operador_movimento(item, tipo_operador, mapa_nome_para_id, mapa_codigo_para_id)
                alvo = _garantir(uid)
                if alvo:
                    alvo[campo_entrada] += 1
            for item in list((mov or {}).get("sairam") or []):
                uid = _resolver_operador_movimento(item, tipo_operador, mapa_nome_para_id, mapa_codigo_para_id)
                alvo = _garantir(uid)
                if alvo:
                    alvo[campo_saida] += 1

    _processar("oracle_90_150", "entraram_90_150", "sairam_90_150")
    _processar("proximos_inativacao", "entraram_proximos", "sairam_proximos")

    linhas = []
    for c in list(consultores or []):
        uid = int(c.get("id"))
        row = _garantir(uid)
        row["saldo_90_150"] = int(row["entraram_90_150"]) - int(row["sairam_90_150"])
        row["saldo_proximos"] = int(row["entraram_proximos"]) - int(row["sairam_proximos"])
        row["saldo_total"] = int(row["saldo_90_150"]) + int(row["saldo_proximos"])
        linhas.append(row)

    linhas.sort(
        key=lambda x: (
            -abs(int(x.get("saldo_total") or 0)),
            -abs(int(x.get("saldo_90_150") or 0)),
            str(x.get("nome") or ""),
        )
    )
    totais = {
        "entraram_90_150": sum(int(x.get("entraram_90_150") or 0) for x in linhas),
        "sairam_90_150": sum(int(x.get("sairam_90_150") or 0) for x in linhas),
        "entraram_proximos": sum(int(x.get("entraram_proximos") or 0) for x in linhas),
        "sairam_proximos": sum(int(x.get("sairam_proximos") or 0) for x in linhas),
    }
    totais["saldo_90_150"] = int(totais["entraram_90_150"]) - int(totais["sairam_90_150"])
    totais["saldo_proximos"] = int(totais["entraram_proximos"]) - int(totais["sairam_proximos"])
    totais["saldo_total"] = int(totais["saldo_90_150"]) + int(totais["saldo_proximos"])
    return {
        "disponivel": bool(houve_dados),
        "linhas": linhas,
        "totais": totais,
    }


def _validar_payload_fechamento(payload):
    consultores = list((payload or {}).get("consultores") or [])
    totais = (payload or {}).get("totais") or {}
    tipo_operador = str((payload or {}).get("tipo_operador") or "").strip().lower()
    fechamento_televendas = tipo_operador == "televendas"
    avisos = []

    soma_ligacoes = sum(int(c.get("total_ligacoes") or 0) for c in consultores)
    soma_vendas = sum(int(c.get("vendas") or 0) for c in consultores)
    soma_retornar = sum(int(c.get("total_retornar") or 0) for c in consultores)
    soma_receita = round(sum(float(c.get("receita") or 0.0) for c in consultores), 2)
    soma_receita_oracle = round(sum(float(c.get("receita_comprovada_oracle") or 0.0) for c in consultores), 2)

    if int(totais.get("total_ligacoes") or 0) != soma_ligacoes:
        avisos.append("Total de ligacoes diverge da soma dos consultores.")
    if int(totais.get("total_vendas") or 0) != soma_vendas:
        avisos.append("Total de vendas diverge da soma dos consultores.")
    if int(totais.get("total_retornar") or 0) != soma_retornar:
        avisos.append("Total de retornar diverge da soma dos consultores.")
    if round(float(totais.get("receita") or 0.0), 2) != soma_receita:
        avisos.append("Receita total diverge da soma dos consultores.")
    if round(float(totais.get("receita_comprovada_oracle") or 0.0), 2) != soma_receita_oracle:
        avisos.append("Receita comprovada Oracle diverge da soma dos consultores.")

    soma_reativacoes = sum(int(c.get("reativacoes") or 0) for c in consultores)
    if int(totais.get("total_reativacoes") or 0) != soma_reativacoes:
        avisos.append("Total de reativacoes diverge da soma dos consultores.")

    hist_constr = bool(totais.get("historico_construtoras_disponivel"))
    if hist_constr and not fechamento_televendas:
        soma_constr = sum(int(c.get("total_construtoras") or 0) for c in consultores)
        if int(totais.get("total_construtoras") or 0) != soma_constr:
            avisos.append("Total construtoras diverge da soma historica por consultor.")
    elif not hist_constr:
        if totais.get("total_construtoras") is not None:
            avisos.append("Total construtoras deveria estar vazio sem historico disponivel.")
        if any(c.get("total_construtoras") is not None for c in consultores):
            avisos.append("Consultores exibem construtoras numerico sem historico disponivel.")

    hist_ativos = bool(totais.get("historico_ativos_disponivel"))
    if hist_ativos and not fechamento_televendas:
        soma_ativos = sum(int(c.get("total_ativos") or 0) for c in consultores)
        if int(totais.get("total_ativos") or 0) != soma_ativos:
            avisos.append("Total ativos diverge da soma historica por consultor.")
    elif not hist_ativos and not fechamento_televendas:
        if totais.get("total_ativos") is not None:
            avisos.append("Total ativos deveria estar vazio sem historico disponivel.")
        if any(c.get("total_ativos") is not None for c in consultores):
            avisos.append("Consultores exibem ativos numerico sem historico disponivel.")

    hist_90 = bool(totais.get("historico_90_150_disponivel"))
    hist_px = bool(totais.get("historico_proximos_disponivel"))

    if hist_90:
        soma_90 = sum(int(c.get("total_90_150") or 0) for c in consultores)
        if int(totais.get("total_90_150") or 0) != soma_90:
            avisos.append("Total 90-150 diverge da soma historica por consultor.")
    else:
        if totais.get("total_90_150") is not None:
            avisos.append("Total 90-150 deveria estar vazio sem historico disponivel.")
        if any(c.get("total_90_150") is not None for c in consultores):
            avisos.append("Consultores exibem 90-150 numerico sem historico disponivel.")

    if hist_px:
        soma_px = sum(int(c.get("total_proximos_inativacao") or 0) for c in consultores)
        if int(totais.get("total_proximos_inativacao") or 0) != soma_px:
            avisos.append("Total proximos da inativacao diverge da soma historica por consultor.")
    else:
        if totais.get("total_proximos_inativacao") is not None:
            avisos.append("Total proximos deveria estar vazio sem historico disponivel.")
        if any(c.get("total_proximos_inativacao") is not None for c in consultores):
            avisos.append("Consultores exibem proximos numerico sem historico disponivel.")

    consistencia = {
        "ok": not avisos,
        "avisos": avisos,
        "resumo": {
            "consultores": len(consultores),
            "total_ligacoes_somado": soma_ligacoes,
            "total_vendas_somado": soma_vendas,
            "total_retornar_somado": soma_retornar,
        },
    }
    if avisos:
        logger.warning("Inconsistencias no fechamento %s/%s: %s", payload.get("mes"), payload.get("ano"), " | ".join(avisos))
    return consistencia


def _snapshot_fechamento_fechado(mes, ano):
    agora = datetime.now()
    return (int(ano), int(mes)) < (int(agora.year), int(agora.month))


def _assinar_snapshot_fechamento(snapshot):
    base = {
        "tipo_operador": str((snapshot or {}).get("tipo_operador") or ""),
        "ano": int((snapshot or {}).get("ano") or 0),
        "mes": int((snapshot or {}).get("mes") or 0),
        "fechado": bool((snapshot or {}).get("fechado")),
        "historico": dict((snapshot or {}).get("historico") or {}),
        "totais": dict((snapshot or {}).get("totais") or {}),
        "consultores": list((snapshot or {}).get("consultores") or []),
    }
    return hashlib.sha1(
        json.dumps(base, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _montar_snapshot_fechamento_payload(payload, tipo_operador):
    totais = (payload or {}).get("totais") or {}
    consultores = list((payload or {}).get("consultores") or [])
    snapshot = {
        "tipo_operador": str(tipo_operador or "").strip().lower(),
        "ano": int(payload.get("ano") or 0),
        "mes": int(payload.get("mes") or 0),
        "gerado_em": datetime.now().isoformat(),
        "fechado": bool(_snapshot_fechamento_fechado(payload.get("mes"), payload.get("ano"))),
        "historico": {
            "ref_ativos": totais.get("historico_ativos_data_ref"),
            "inicio_ativos": totais.get("historico_ativos_inicio"),
            "ref_90_150": totais.get("historico_90_150_data_ref"),
            "inicio_90_150": totais.get("historico_90_150_inicio"),
            "ref_proximos": totais.get("historico_proximos_data_ref"),
            "inicio_proximos": totais.get("historico_proximos_inicio"),
            "ref_construtoras": totais.get("historico_construtoras_data_ref"),
            "inicio_construtoras": totais.get("historico_construtoras_inicio"),
        },
        "consistencia": dict((payload or {}).get("consistencia") or {}),
        "totais": {
            "total_ligacoes": int(totais.get("total_ligacoes") or 0),
            "total_vendas": int(totais.get("total_vendas") or 0),
            "total_retornar": int(totais.get("total_retornar") or 0),
            "conversao": float(totais.get("conversao") or 0.0),
            "receita": round(float(totais.get("receita") or 0.0), 2),
            "receita_comprovada_oracle": round(float(totais.get("receita_comprovada_oracle") or 0.0), 2),
            "total_reativacoes": int(totais.get("total_reativacoes") or 0),
            "total_ativos": totais.get("total_ativos"),
            "total_90_150": totais.get("total_90_150"),
            "total_proximos_inativacao": totais.get("total_proximos_inativacao"),
            "total_construtoras": totais.get("total_construtoras"),
        },
        "consultores": [
            {
                "id": int(c.get("id") or 0),
                "nome": str(c.get("nome") or "-"),
                "total_ligacoes": int(c.get("total_ligacoes") or 0),
                "vendas": int(c.get("vendas") or 0),
                "total_retornar": int(c.get("total_retornar") or 0),
                "conversao": float(c.get("conversao") or 0.0),
                "receita": round(float(c.get("receita") or 0.0), 2),
                "receita_comprovada_oracle": round(float(c.get("receita_comprovada_oracle") or 0.0), 2),
                "reativacoes": int(c.get("reativacoes") or 0),
                "total_ativos": c.get("total_ativos"),
                "total_90_150": c.get("total_90_150"),
                "total_proximos_inativacao": c.get("total_proximos_inativacao"),
                "total_construtoras": c.get("total_construtoras"),
            }
            for c in consultores
        ],
    }
    snapshot["assinatura"] = _assinar_snapshot_fechamento(snapshot)
    return snapshot


def _persistir_snapshot_fechamento(payload, tipo_operador):
    mes = int(payload.get("mes") or 0)
    ano = int(payload.get("ano") or 0)
    if not mes or not ano:
        return {"salvo": False, "motivo": "periodo_invalido"}

    snapshot_atual = _montar_snapshot_fechamento_payload(payload, tipo_operador)
    existente = carregar_snapshot_fechamento(tipo_operador, ano, mes) or {}
    ultimo = existente.get("ultimo_snapshot") if isinstance(existente, dict) else None
    assinatura_anterior = str((ultimo or {}).get("assinatura") or "")
    divergiu = bool(assinatura_anterior and assinatura_anterior != snapshot_atual["assinatura"])

    if not assinatura_anterior or divergiu:
        salvar_snapshot_fechamento(tipo_operador, ano, mes, snapshot_atual)

    fechado = bool(snapshot_atual.get("fechado"))
    divergencia_pendente = bool(divergiu and fechado)

    return {
        "salvo": bool(not assinatura_anterior or divergiu),
        "fechado": fechado,
        "assinatura": snapshot_atual.get("assinatura"),
        "assinatura_anterior": assinatura_anterior,
        "divergiu_do_ultimo": divergencia_pendente,
        "atualizado_do_ultimo": bool(divergiu and not fechado),
        "versao_existente": bool(assinatura_anterior),
        "gerado_em": snapshot_atual.get("gerado_em"),
    }


def _texto_evolucao(delta, *, unidade="", menor_melhor=False):
    if delta is None:
        return "sem base comparativa"
    if delta == 0:
        return "ficou estavel"
    melhorou = delta < 0 if menor_melhor else delta > 0
    base_txt = "melhorou" if melhorou else "piorou"
    if unidade == "p.p.":
        return f"{base_txt} {abs(delta):.1f} p.p."
    return f"{base_txt} {abs(int(delta))} {unidade}".strip()


def _montar_destaques_consultores(consultores_atual, consultores_anterior):
    prev_map = {int(c.get("id")): c for c in list(consultores_anterior or []) if c.get("id")}
    melhoraram = []
    pioraram = []
    em_alerta = []

    for atual in list(consultores_atual or []):
        uid = int(atual.get("id") or 0)
        prev = prev_map.get(uid, {})
        conv_atual = float(atual.get("conversao") or 0)
        conv_prev = float(prev.get("conversao") or 0) if prev else 0.0
        receita_atual = float(atual.get("receita") or 0)
        receita_prev = float(prev.get("receita") or 0) if prev else 0.0
        risco_atual = atual.get("total_90_150")
        risco_prev = prev.get("total_90_150")
        delta_conv = round(conv_atual - conv_prev, 1)
        delta_receita = receita_atual - receita_prev
        delta_risco = None if risco_atual is None or risco_prev is None else int(risco_atual) - int(risco_prev)

        score_pos = 0.0
        score_neg = 0.0
        if delta_conv > 0:
            score_pos += delta_conv * 2
        elif delta_conv < 0:
            score_neg += abs(delta_conv) * 2
        if delta_receita > 0:
            score_pos += min(delta_receita / 1000.0, 40)
        elif delta_receita < 0:
            score_neg += min(abs(delta_receita) / 1000.0, 40)
        if delta_risco is not None:
            if delta_risco < 0:
                score_pos += min(abs(delta_risco), 20)
            elif delta_risco > 0:
                score_neg += min(abs(delta_risco), 20)

        nome = str(atual.get("nome") or "-")
        if score_pos > 0:
            melhoraram.append(
                {
                    "nome": nome,
                    "score": round(score_pos, 2),
                    "texto": (
                        f"Conversao {conv_prev:.1f}% -> {conv_atual:.1f}%"
                        if delta_conv > 0
                        else f"Receita {formatar_dinheiro(receita_prev)} -> {formatar_dinheiro(receita_atual)}"
                    ),
                }
            )
        if score_neg > 0:
            pioraram.append(
                {
                    "nome": nome,
                    "score": round(score_neg, 2),
                    "texto": (
                        f"Conversao {conv_prev:.1f}% -> {conv_atual:.1f}%"
                        if delta_conv < 0
                        else (
                            f"Carteira 90-150 {int(risco_prev or 0)} -> {int(risco_atual or 0)}"
                            if delta_risco is not None and delta_risco > 0
                            else f"Receita {formatar_dinheiro(receita_prev)} -> {formatar_dinheiro(receita_atual)}"
                        )
                    ),
                }
            )

        if int(atual.get("total_ligacoes") or 0) >= 40 and int(atual.get("vendas") or 0) == 0:
            em_alerta.append({"nome": nome, "texto": "Muitas ligacoes no periodo sem vendas registradas."})
        elif (atual.get("total_90_150") or 0) >= 100 and float(atual.get("conversao") or 0) < float(atual.get("meta_conversao") or 0):
            em_alerta.append({"nome": nome, "texto": "Carteira 90-150 alta com conversao abaixo da meta."})
        elif (atual.get("total_proximos_inativacao") or 0) >= 40 and int(atual.get("vendas") or 0) <= 1:
            em_alerta.append({"nome": nome, "texto": "Proximos da inativacao elevados com baixa resposta comercial."})

    melhoraram.sort(key=lambda x: (-float(x.get("score") or 0), str(x.get("nome") or "")))
    pioraram.sort(key=lambda x: (-float(x.get("score") or 0), str(x.get("nome") or "")))
    return {
        "melhoraram": melhoraram[:3],
        "pioraram": pioraram[:3],
        "em_alerta": em_alerta[:4],
    }


def _montar_alertas_gerais(totais_atual, totais_anterior, consultores_atual):
    alertas = []
    total_90 = totais_atual.get("total_90_150")
    prev_90 = totais_anterior.get("total_90_150") if totais_anterior else None
    total_px = totais_atual.get("total_proximos_inativacao")
    prev_px = totais_anterior.get("total_proximos_inativacao") if totais_anterior else None

    if total_90 is not None and prev_90 is not None and int(total_90) > int(prev_90):
        alertas.append({
            "nivel": "warning",
            "titulo": "Carteira 90-150 cresceu no periodo",
            "texto": f"Subiu de {int(prev_90)} para {int(total_90)} clientes no fechamento.",
        })
    if total_px is not None and prev_px is not None and int(total_px) > int(prev_px):
        alertas.append({
            "nivel": "danger",
            "titulo": "Proximos da inativacao aumentaram",
            "texto": f"O volume saiu de {int(prev_px)} para {int(total_px)} no comparativo mensal.",
        })

    for c in list(consultores_atual or []):
        if len(alertas) >= 4:
            break
        if (c.get("total_90_150") or 0) >= 120 and float(c.get("conversao") or 0) < float(c.get("meta_conversao") or 0):
            alertas.append({
                "nivel": "warning",
                "titulo": f"{c.get('nome')}: carteira alta com conversao baixa",
                "texto": f"Tem {int(c.get('total_90_150') or 0)} clientes em 90-150 e conversao de {float(c.get('conversao') or 0):.1f}%.",
            })
        elif int(c.get("total_ligacoes") or 0) >= 50 and int(c.get("vendas") or 0) == 0:
            alertas.append({
                "nivel": "info",
                "titulo": f"{c.get('nome')}: alto esforco sem venda",
                "texto": f"Registrou {int(c.get('total_ligacoes') or 0)} ligacoes sem vendas no periodo.",
            })

    if not alertas:
        alertas.append({
            "nivel": "success",
            "titulo": "Periodo sem alertas criticos",
            "texto": "Nao foram detectados desvios fortes nas carteiras ou na conversao do fechamento.",
        })
    return alertas[:4]


def _montar_analise_fechamento(payload_atual, payload_anterior, *, mes, ano, tipo_operador):
    totais_atual = payload_atual.get("totais") or {}
    totais_anterior = (payload_anterior or {}).get("totais") or {}
    consultores_atual = payload_atual.get("consultores") or []
    consultores_anterior = (payload_anterior or {}).get("consultores") or []
    mes_ant, ano_ant = _mes_ano_anterior(mes, ano)
    movimento = _montar_movimento_carteiras_mes(ano, mes, tipo_operador, consultores_atual)

    comparativos = [
        _montar_card_comparativo(
            "Conversao",
            totais_atual.get("conversao"),
            totais_anterior.get("conversao"),
            formato="percentual",
        ),
        _montar_card_comparativo(
            "Receita",
            totais_atual.get("receita"),
            totais_anterior.get("receita"),
            formato="receita",
        ),
        _montar_card_comparativo(
            "Vendas",
            totais_atual.get("total_vendas"),
            totais_anterior.get("total_vendas"),
            formato="numero",
        ),
        _montar_card_comparativo(
            "90-150",
            totais_atual.get("total_90_150"),
            totais_anterior.get("total_90_150"),
            formato="numero",
            menor_melhor=True,
        ),
        _montar_card_comparativo(
            "Prox. Inativacao",
            totais_atual.get("total_proximos_inativacao"),
            totais_anterior.get("total_proximos_inativacao"),
            formato="numero",
            menor_melhor=True,
        ),
    ]

    return {
        "periodo_atual": {"mes": int(mes), "ano": int(ano), "texto": _texto_periodo_curto(mes, ano)},
        "periodo_anterior": {"mes": int(mes_ant), "ano": int(ano_ant), "texto": _texto_periodo_curto(mes_ant, ano_ant)},
        "comparativos": comparativos,
        "destaques": _montar_destaques_consultores(consultores_atual, consultores_anterior),
        "movimento_carteiras": movimento,
        "alertas": _montar_alertas_gerais(totais_atual, totais_anterior, consultores_atual),
    }


def _consultar_resultados_consultores_mes_historico(
    mes,
    ano,
    meta_conversao=10.0,
    tipo_operador="consultor",
    incluir_analise=True,
):
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

    rows = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            func.coalesce(subq_lig.c.total, 0).label("total"),
            func.coalesce(subq_lig.c.vendas, 0).label("vendas"),
            func.coalesce(subq_lig.c.retornar, 0).label("retornar"),
            func.coalesce(subq_lig.c.receita, 0.0).label("receita"),
        )
        .outerjoin(subq_lig, subq_lig.c.cid == Usuario.id)
        .filter(Usuario.tipo == tipo_operador, Usuario.ativo == True)
        .order_by(desc("receita"))
        .all()
    )

    receita_oracle_por_operador = {}
    reativacoes_por_operador = _contagem_reativacoes_por_usuario(mes, ano, tipo_operador)
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

    snapshot_90_150 = _reconstruir_snapshot_historico_carteira(
        "oracle_90_150",
        tipo_operador,
        _contagem_90_150_por_usuario_mesma_regra_lista_oracle(tipo_operador=tipo_operador),
        ano,
        mes,
    ) or {"disponivel": False}
    snapshot_ativos = _contagem_ativos_por_usuario_snapshot(
        ano,
        mes,
        tipo_operador,
    ) or {"disponivel": False}
    snapshot_proximos = _reconstruir_snapshot_historico_carteira(
        "proximos_inativacao",
        tipo_operador,
        _contagem_proximos_por_usuario_atual(tipo_operador=tipo_operador),
        ano,
        mes,
    ) or {"disponivel": False}
    snapshot_construtoras = _contagem_construtoras_por_usuario_snapshot(
        ano,
        mes,
        tipo_operador,
    ) or {"disponivel": False}

    resultado = []
    total_ligacoes_geral = 0
    total_vendas_geral = 0
    total_retornar_geral = 0
    total_receita_geral = 0.0
    total_receita_comprovada_oracle_geral = 0.0
    total_ativos_geral = None
    total_90_150_geral = None
    total_proximos_geral = None
    total_construtoras_geral = None

    for uid, nome, total, vendas, retornar, receita in rows:
        total = int(total or 0)
        vendas = int(vendas or 0)
        retornar = int(retornar or 0)
        receita = float(receita or 0)
        receita_comprovada_oracle = float(receita_oracle_por_operador.get(int(uid), 0.0))
        reativacoes = int(reativacoes_por_operador.get(int(uid), 0))
        total_ativos = None
        if snapshot_ativos.get("disponivel"):
            total_ativos = int((snapshot_ativos.get("contagem_por_usuario") or {}).get(int(uid), 0))
        total_90_150 = None
        if snapshot_90_150.get("disponivel"):
            total_90_150 = int((snapshot_90_150.get("contagem_por_usuario") or {}).get(int(uid), 0))
        total_proximos = None
        if snapshot_proximos.get("disponivel"):
            total_proximos = int((snapshot_proximos.get("contagem_por_usuario") or {}).get(int(uid), 0))
        total_construtoras = None
        if snapshot_construtoras.get("disponivel") and tipo_operador != "televendas":
            total_construtoras = int((snapshot_construtoras.get("contagem_por_usuario") or {}).get(int(uid), 0))
        conv = _percent(vendas, total) if total else 0.0

        total_ligacoes_geral += total
        total_vendas_geral += vendas
        total_retornar_geral += retornar
        total_receita_geral += receita
        total_receita_comprovada_oracle_geral += receita_comprovada_oracle
        if total_ativos is not None:
            total_ativos_geral = int((total_ativos_geral or 0) + total_ativos)
        if total_90_150 is not None:
            total_90_150_geral = int((total_90_150_geral or 0) + total_90_150)
        if total_proximos is not None:
            total_proximos_geral = int((total_proximos_geral or 0) + total_proximos)
        if total_construtoras is not None:
            total_construtoras_geral = int((total_construtoras_geral or 0) + total_construtoras)

        if tipo_operador == "televendas":
            total_90_150 = None
            total_proximos = None
            total_construtoras = None

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
                "reativacoes": reativacoes,
                "total_ativos": total_ativos,
                "total_ativos_display": (
                    str(total_ativos) if total_ativos is not None else "Sem historico"
                ),
                "total_90_150": total_90_150,
                "total_90_150_display": (str(total_90_150) if total_90_150 is not None else "Sem historico"),
                "total_proximos_inativacao": total_proximos,
                "total_proximos_inativacao_display": (
                    str(total_proximos) if total_proximos is not None else "Sem historico"
                ),
                "total_construtoras": total_construtoras,
                "total_construtoras_display": (
                    str(total_construtoras) if total_construtoras is not None else "-"
                ),
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
        "total_reativacoes": int(sum(reativacoes_por_operador.values())),
        "total_ativos": (
            None if tipo_operador == "televendas"
            else (int(total_ativos_geral) if total_ativos_geral is not None else None)
        ),
        "total_ativos_display": (
            "Nao se aplica"
            if tipo_operador == "televendas"
            else (str(int(total_ativos_geral)) if total_ativos_geral is not None else "Sem historico")
        ),
        "total_90_150": (
            None if tipo_operador == "televendas"
            else (int(total_90_150_geral) if total_90_150_geral is not None else None)
        ),
        "historico_ativos_disponivel": bool(snapshot_ativos.get("disponivel")) and tipo_operador != "televendas",
        "historico_ativos_data_ref": snapshot_ativos.get("data_ref"),
        "historico_ativos_inicio": snapshot_ativos.get("primeira_data"),
        "total_90_150_display": (
            "Nao se aplica"
            if tipo_operador == "televendas"
            else (str(int(total_90_150_geral)) if total_90_150_geral is not None else "Sem historico")
        ),
        "total_proximos_inativacao": (
            None if tipo_operador == "televendas"
            else (int(total_proximos_geral) if total_proximos_geral is not None else None)
        ),
        "total_proximos_inativacao_display": (
            "Nao se aplica"
            if tipo_operador == "televendas"
            else (str(int(total_proximos_geral)) if total_proximos_geral is not None else "Sem historico")
        ),
        "total_construtoras": (
            int(snapshot_construtoras.get("total_snapshot") or snapshot_construtoras.get("total") or 0)
            if tipo_operador == "televendas"
            else (int(total_construtoras_geral) if total_construtoras_geral is not None else None)
        ),
        "total_construtoras_display": (
            str(int(snapshot_construtoras.get("total_snapshot") or snapshot_construtoras.get("total") or 0))
            if tipo_operador == "televendas"
            else (str(int(total_construtoras_geral)) if total_construtoras_geral is not None else "Sem historico")
        ),
        "historico_90_150_disponivel": bool(snapshot_90_150.get("disponivel")) and tipo_operador != "televendas",
        "historico_90_150_data_ref": snapshot_90_150.get("data_ref"),
        "historico_90_150_inicio": snapshot_90_150.get("primeira_data"),
        "historico_proximos_disponivel": bool(snapshot_proximos.get("disponivel")) and tipo_operador != "televendas",
        "historico_proximos_data_ref": snapshot_proximos.get("data_ref"),
        "historico_proximos_inicio": snapshot_proximos.get("primeira_data"),
        "historico_construtoras_disponivel": bool(snapshot_construtoras.get("disponivel")),
        "historico_construtoras_data_ref": snapshot_construtoras.get("data_ref"),
        "historico_construtoras_inicio": snapshot_construtoras.get("primeira_data"),
        "conversao": round(conversao_geral, 1),
        "meta_conversao": float(meta_conversao),
        "receita": float(total_receita_geral),
        "receita_fmt": formatar_dinheiro(total_receita_geral),
        "receita_comprovada_oracle": float(total_receita_comprovada_oracle_geral),
        "receita_comprovada_oracle_fmt": formatar_dinheiro(total_receita_comprovada_oracle_geral),
    }

    payload = {
        "ok": True,
        "mes": mes,
        "ano": ano,
        "tipo_operador": tipo_operador,
        "consultores": resultado,
        "totais": totais,
    }
    if incluir_analise and tipo_operador == "consultor":
        mes_ant, ano_ant = _mes_ano_anterior(mes, ano)
        payload_anterior, status_anterior = _consultar_resultados_consultores_mes_historico(
            mes_ant,
            ano_ant,
            meta_conversao=meta_conversao,
            tipo_operador=tipo_operador,
            incluir_analise=False,
        )
        if status_anterior == 200 and payload_anterior.get("ok"):
            payload["analise_fechamento"] = _montar_analise_fechamento(
                payload,
                payload_anterior,
                mes=mes,
                ano=ano,
                tipo_operador=tipo_operador,
            )
    payload["consistencia"] = _validar_payload_fechamento(payload)
    payload["snapshot_info"] = _persistir_snapshot_fechamento(payload, tipo_operador)
    if payload["snapshot_info"].get("divergiu_do_ultimo"):
        avisos = list((payload.get("consistencia") or {}).get("avisos") or [])
        avisos.append("Snapshot atual divergiu da ultima versao salva para este mes.")
        payload["consistencia"]["avisos"] = avisos
        payload["consistencia"]["ok"] = False
    return payload, 200


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


def _consultar_resultados_consultores_mes_legado_completo(mes, ano, meta_conversao=10.0, tipo_operador="consultor"):
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


def consultar_resultados_consultores_mes(mes, ano, meta_conversao=10.0, tipo_operador="consultor"):
    if mes < 1 or mes > 12:
        return {"ok": False, "erro": "Mes invalido"}, 400
    cache_key = f"{tipo_operador}:{int(ano):04d}-{int(mes):02d}:{float(meta_conversao):.2f}"
    cache_item = _RESULTADOS_MES_CACHE.get(cache_key)
    if cache_item and (datetime.now() - cache_item["ts"]) <= _RESULTADOS_MES_CACHE_TTL:
        return deepcopy(cache_item["payload"]), int(cache_item["status"])

    payload, status = _consultar_resultados_consultores_mes_historico(
        mes,
        ano,
        meta_conversao=meta_conversao,
        tipo_operador=tipo_operador,
    )
    if int(status) == 200 and payload.get("ok"):
        _RESULTADOS_MES_CACHE[cache_key] = {
            "ts": datetime.now(),
            "payload": deepcopy(payload),
            "status": int(status),
        }
    return payload, status


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
            if 181 <= dias_sem <= 1095:
                return "Lista Publica"

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
