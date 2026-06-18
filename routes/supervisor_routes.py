from calendar import monthrange
from datetime import datetime, timedelta
from io import BytesIO
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy

from flask import flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, desc, func, or_
from sqlalchemy.orm import joinedload
from werkzeug.security import generate_password_hash

from core.config import SUPERVISOR_DEV_PANEL_PASSWORD, SUPERVISOR_SECRET_HEALTH_KEY
from core.activity_tracker import get_active_users_recent
from core.extensions import db
from core.helpers import _percent, formatar_dinheiro, s
from core.models import Banner, Cliente, Ligacao, Nota, Usuario, SupervisorRepresentanteVinculo, SyncResumoDiario
from routes.clientes_ligacoes.badges import (
    _total_ativos_badge,
    _total_construtoras_badge,
    _total_proximos_badge,
    _total_inativos_badge,
    _total_oracle_badge_supervisor_lista_oracle,
)
from routes.clientes_ligacoes.access_control import (
    obter_chave_sessao_supervisor_dev,
    supervisor_dev_liberado,
)
from routes.clientes_ligacoes.analytics_api import (
    consultar_resultados_consultores_mes,
)
from services.banner_service import get_banners_ativos
from services.ativos_snapshot_service import carregar_snapshot_ativos_oracle_na_data_ou_anterior
from services.carteiras_movimento_service import carregar_movimento_carteira
from services.construtoras_snapshot_service import carregar_snapshot_construtoras_oracle_na_data_ou_anterior
from services.inativos_movimento_service import carregar_movimento_inativos
from services.inativos_snapshot_service import carregar_snapshot_inativos_oracle_na_data_ou_anterior
from services.oracle_snapshot_service import carregar_snapshot_oracle_90_150_na_data_ou_anterior


def _log_perf_supervisor(app, label, started_at, **extra):
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    partes = [f"[PERF supervisor] {label} {elapsed_ms:.1f}ms"]
    for chave, valor in extra.items():
        partes.append(f"{chave}={valor}")
    app.logger.info(" ".join(partes))


_SUPERVISOR_KPI_CACHE = {}
_SUPERVISOR_KPI_CACHE_TTL = timedelta(minutes=5)
_SUPERVISOR_DADOS_CACHE = {}
_SUPERVISOR_DADOS_CACHE_TTL = timedelta(minutes=5)


def _ultimos_meses(qtd=12):
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

    meses = []
    base = data_atual.year * 12 + (data_atual.month - 1)
    for i in range(qtd):
        atual = base - i
        ano = atual // 12
        mes = (atual % 12) + 1
        meses.append({"mes": mes, "ano": ano, "texto": f"{meses_nomes[mes]}/{ano}"})
    return meses


def _nome_mes_ptbr(mes: int) -> str:
    meses_nomes = {
        1: "Janeiro",
        2: "Fevereiro",
        3: "Marco",
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
    return meses_nomes.get(int(mes or 0), "")


def _titulo_dashboard_setor(dashboard_tipo: str) -> str:
    return "Consultores - Televendas/Construtoras" if str(dashboard_tipo or "").strip().lower() == "televendas" else "Consultores"


def register_supervisor_routes(app):
    tipos_usuario_validos = ("consultor", "supervisor", "televendas", "supervisor_repr", "representante")

    @app.context_processor
    def _inject_supervisor_dev_flag():
        return {"supervisor_dev_liberado": supervisor_dev_liberado()}

    def _parse_ano_dashboard(valor, padrao=None):
        ano_padrao = int(padrao or datetime.now().year)
        try:
            ano = int(valor)
        except (TypeError, ValueError):
            return ano_padrao
        return ano if 2020 <= ano <= 2100 else ano_padrao

    def _parse_mes_dashboard(valor, *, permitir_todos=False, padrao=None):
        if permitir_todos and str(valor or "").strip().lower() in ("", "0", "todos", "all"):
            return 0
        mes_padrao = int(padrao or datetime.now().month)
        try:
            mes = int(valor)
        except (TypeError, ValueError):
            return 0 if permitir_todos else mes_padrao
        if 1 <= mes <= 12:
            return mes
        return 0 if permitir_todos else mes_padrao

    def _anos_disponiveis_dashboard():
        ano_atual = datetime.now().year
        try:
            primeira_ligacao = db.session.query(func.min(Ligacao.data_hora)).scalar()
            ano_inicial = int(primeira_ligacao.year) if primeira_ligacao else ano_atual
        except Exception:
            db.session.rollback()
            ano_inicial = ano_atual
        ano_inicial = min(ano_inicial, ano_atual)
        return list(range(ano_atual, ano_inicial - 1, -1))

    def _meses_disponiveis_dashboard():
        return [{"valor": 0, "label": "Todos"}] + [
            {"valor": mes, "label": _nome_mes_ptbr(mes)}
            for mes in range(1, 13)
        ]

    def _resolver_periodo_dashboard(ano_filtro: int, mes_filtro: int) -> dict:
        ano = _parse_ano_dashboard(ano_filtro)
        mes = _parse_mes_dashboard(mes_filtro, permitir_todos=True, padrao=0)

        if mes:
            inicio = datetime(ano, mes, 1).date()
            if mes == 12:
                fim_exclusivo = datetime(ano + 1, 1, 1).date()
                prev_inicio = datetime(ano, 11, 1).date()
            else:
                fim_exclusivo = datetime(ano, mes + 1, 1).date()
                if mes == 1:
                    prev_inicio = datetime(ano - 1, 12, 1).date()
                else:
                    prev_inicio = datetime(ano, mes - 1, 1).date()
            prev_fim_exclusivo = inicio
            return {
                "modo": "mensal",
                "ano": ano,
                "mes": mes,
                "inicio": inicio,
                "fim_exclusivo": fim_exclusivo,
                "inicio_anterior": prev_inicio,
                "fim_exclusivo_anterior": prev_fim_exclusivo,
                "granularidade_grafico": "dia",
                "label": f"{_nome_mes_ptbr(mes)}/{ano}",
                "label_curto": f"{_nome_mes_ptbr(mes)}/{ano}",
                "label_comparativo": "mes anterior",
                "texto_curto": "mes",
                "titulo_grafico": "por dia",
            }

        return {
            "modo": "anual",
            "ano": ano,
            "mes": 0,
            "inicio": datetime(ano, 1, 1).date(),
            "fim_exclusivo": datetime(ano + 1, 1, 1).date(),
            "inicio_anterior": datetime(ano - 1, 1, 1).date(),
            "fim_exclusivo_anterior": datetime(ano, 1, 1).date(),
            "granularidade_grafico": "mes",
            "label": f"Ano {ano}",
            "label_curto": str(ano),
            "label_comparativo": "ano anterior",
            "texto_curto": "ano",
            "titulo_grafico": "por mes",
        }

    def _normalizar_payload_usuario(payload, incluir_senha=False):
        data = {
            "nome": s(payload.get("nome")),
            "email": s(payload.get("email")),
            "tipo": s(payload.get("tipo")),
            "meta_diaria": int(payload.get("meta_diaria") or 10),
            "codigo_supervisor_tg650": s(payload.get("codigo_supervisor_tg650")),
            "codigo_representante": _somente_digitos(s(payload.get("codigo_representante"))),
        }
        if incluir_senha:
            data["senha"] = payload.get("senha") or ""
        return data

    def _somente_digitos(valor: str) -> str:
        return "".join(ch for ch in str(valor or "") if ch.isdigit())

    def _normalizar_nome_chave(valor: str) -> str:
        base = str(valor or "").strip().lower()
        if not base:
            return ""
        base = unicodedata.normalize("NFD", base)
        base = "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
        base = " ".join(base.split())
        return base

    def _complementar_mensagem_sync_tg650(mensagem_base, usuario_id, tipo, codigo_supervisor_tg650):
        mensagem = mensagem_base
        if tipo == "supervisor_repr" and codigo_supervisor_tg650:
            try:
                sync_result = _sincronizar_vinculos_tg650_supervisor_repr(usuario_id, codigo_supervisor_tg650)
                if sync_result.get("ok"):
                    mensagem += (
                        f" TG650 sincronizada ({sync_result.get('novos', 0)} novos, "
                        f"{sync_result.get('atualizados', 0)} atualizados)."
                    )
                else:
                    mensagem += f" TG650 nao sincronizada: {sync_result.get('mensagem')}."
            except Exception as sync_err:
                mensagem += f" TG650 nao sincronizada: {str(sync_err)}."
        return mensagem

    def _calcular_kpis_dashboard_supervisor(
        dashboard_tipo: str,
        operadores_ids_query,
        filtrar_carteira_por_vinculo: bool,
        hoje,
        periodo: dict | None = None,
    ) -> dict:
        periodo = periodo or {}
        periodo_chave = f"{periodo.get('inicio')}:{periodo.get('fim_exclusivo')}:{periodo.get('modo')}"
        cache_key = f"{dashboard_tipo}:{hoje.isoformat()}:{int(bool(filtrar_carteira_por_vinculo))}:{periodo_chave}"
        cache_item = _SUPERVISOR_KPI_CACHE.get(cache_key)
        if cache_item and (datetime.now() - cache_item["ts"]) <= _SUPERVISOR_KPI_CACHE_TTL:
            return deepcopy(cache_item["data"])

        data_base = periodo.get("fim_exclusivo")
        if data_base:
            data_base = data_base - timedelta(days=1)
        else:
            data_base = hoje
        fim_data_base = datetime.combine(data_base, datetime.max.time())

        total_consultores = (
            Usuario.query
            .filter(
                Usuario.tipo == dashboard_tipo,
                Usuario.ativo == True,
                Usuario.data_cadastro <= fim_data_base,
            )
            .count()
        )
        snapshot_ativos = carregar_snapshot_ativos_oracle_na_data_ou_anterior(data_base)
        total_ativos = int((snapshot_ativos or {}).get("total") or 0)
        total_ligacoes = (
            db.session.query(func.count(Ligacao.id))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .scalar()
        ) or 0
        ligacoes_hoje = (
            db.session.query(func.count(Ligacao.id))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(
                Usuario.tipo == dashboard_tipo,
                Usuario.ativo == True,
                func.date(Ligacao.data_hora) == hoje,
            )
            .scalar()
        ) or 0

        agora = fim_data_base
        limite_90 = agora - timedelta(days=90)
        limite_150 = agora - timedelta(days=150)
        limite_151 = agora - timedelta(days=151)
        limite_180 = agora - timedelta(days=180)
        limite_181 = agora - timedelta(days=181)
        limite_1095 = agora - timedelta(days=1095)

        snapshot_90_150 = carregar_snapshot_oracle_90_150_na_data_ou_anterior(data_base)
        snapshot_inativos = carregar_snapshot_inativos_oracle_na_data_ou_anterior(data_base)
        snapshot_construtoras = carregar_snapshot_construtoras_oracle_na_data_ou_anterior(data_base)

        if dashboard_tipo == "televendas":
            total_sem_pedido_90_150 = 0
        else:
            total_sem_pedido_90_150 = int((snapshot_90_150 or {}).get("total") or 0)
            if not total_sem_pedido_90_150:
                total_sem_pedido_90_150 = (
                    Cliente.query
                    .filter(
                        Cliente.ativo == True,
                        Cliente.cd_cliente_oracle.isnot(None),
                        Cliente.ultimo_pedido_oracle.isnot(None),
                        Cliente.ultimo_pedido_oracle.between(limite_150, limite_90),
                    )
                    .count()
                )

        total_proximos_inativacao = (
            0
            if dashboard_tipo == "televendas"
            else (
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
                )
                .count()
            )
        )

        try:
            total_inativos = int(_total_inativos_badge() or 0)
        except Exception:
            total_inativos = int((snapshot_inativos or {}).get("total") or 0)
            if not total_inativos:
                total_inativos = (
                    Cliente.query
                    .filter(
                        Cliente.ativo == True,
                        Cliente.cd_cliente_oracle.isnot(None),
                        Cliente.ultimo_pedido_oracle.isnot(None),
                        Cliente.ultimo_pedido_oracle.between(limite_1095, limite_181),
                    )
                    .count()
                )

        total_construtoras = int((snapshot_construtoras or {}).get("total") or 0)

        if dashboard_tipo == "televendas":
            total_clientes = int((total_inativos or 0) + (total_construtoras or 0))
        else:
            total_clientes = int(
                (total_ativos or 0)
                + (total_sem_pedido_90_150 or 0)
                + (total_proximos_inativacao or 0)
                + (total_inativos or 0)
                + (total_construtoras or 0)
            )

        total_retorno_atrasado_query = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.proxima_ligacao.isnot(None),
                Cliente.proxima_ligacao < agora,
            )
        )
        if filtrar_carteira_por_vinculo:
            total_retorno_atrasado_query = total_retorno_atrasado_query.filter(
                Cliente.consultor_id.in_(operadores_ids_query)
            )
        total_retorno_atrasado = total_retorno_atrasado_query.count()

        limite_30d = agora - timedelta(days=30)
        ids_com_contato_recente = (
            db.session.query(Ligacao.cliente_id)
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Ligacao.data_hora >= limite_30d, Ligacao.data_hora <= agora)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .distinct()
            .subquery()
        )
        total_carteira_risco_query = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
                Cliente.id.notin_(ids_com_contato_recente),
            )
        )
        if filtrar_carteira_por_vinculo:
            total_carteira_risco_query = total_carteira_risco_query.filter(
                Cliente.consultor_id.in_(operadores_ids_query)
            )
        total_carteira_risco = total_carteira_risco_query.count()

        def _montar_comparativo_card(
            chave: str,
            serie_atual: list[int],
            serie_anterior: list[int],
            fluxo: bool = False,
        ) -> dict:
            valor_atual = int(sum(serie_atual or [])) if fluxo else int((serie_atual or [0])[-1] or 0)
            valor_anterior = int(sum(serie_anterior or [])) if fluxo else int((serie_anterior or [0])[-1] or 0)
            diferenca = valor_atual - valor_anterior
            if valor_anterior:
                percentual = round((diferenca / valor_anterior) * 100, 1)
                texto = f"{percentual:+.1f}% vs 30d ant."
            elif valor_atual:
                texto = "Novo nos 30d" if fluxo else "+ em 30d"
            else:
                texto = "Sem movimento 30d" if fluxo else "Estavel 30d"
            if diferenca == 0 and valor_atual == valor_anterior:
                texto = "Estavel vs 30d ant."
            return {
                "key": chave,
                "serie": [int(v or 0) for v in serie_atual],
                "anterior": [int(v or 0) for v in serie_anterior],
                "delta": diferenca,
                "texto": texto,
                "classe": "positive" if diferenca > 0 else ("negative" if diferenca < 0 else "neutral"),
            }

        def _datas_periodo(inicio, dias=30):
            return [inicio + timedelta(days=i) for i in range(dias)]

        inicio_atual = data_base - timedelta(days=29)
        inicio_anterior = inicio_atual - timedelta(days=30)
        dias_atual = _datas_periodo(inicio_atual, 30)
        dias_anterior = _datas_periodo(inicio_anterior, 30)
        dias_analise = dias_anterior + dias_atual
        data_inicio_analise = dias_analise[0]
        data_fim_analise = dias_analise[-1]

        clientes_oracle_local = [
            (str(cd or "").strip(), dt)
            for cd, dt in (
                db.session.query(Cliente.cd_cliente_oracle, Cliente.ultimo_pedido_oracle)
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                )
                .all()
            )
            if str(cd or "").strip() and dt
        ]

        fim_analise_exclusivo = datetime.combine(data_fim_analise + timedelta(days=1), datetime.min.time())
        inicio_analise_dt = datetime.combine(data_inicio_analise, datetime.min.time())

        usuarios_por_data = defaultdict(int)
        for data_cadastro, total in (
            db.session.query(
                func.date(Usuario.data_cadastro),
                func.count(Usuario.id),
            )
            .filter(
                Usuario.tipo == dashboard_tipo,
                Usuario.ativo == True,
                Usuario.data_cadastro < fim_analise_exclusivo,
            )
            .group_by(func.date(Usuario.data_cadastro))
            .all()
        ):
            if data_cadastro:
                usuarios_por_data[data_cadastro] += int(total or 0)

        ligacoes_por_data = defaultdict(int)
        for data_ligacao, total in (
            db.session.query(
                func.date(Ligacao.data_hora),
                func.count(Ligacao.id),
            )
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(
                Usuario.tipo == dashboard_tipo,
                Usuario.ativo == True,
                Ligacao.data_hora >= inicio_analise_dt,
                Ligacao.data_hora < fim_analise_exclusivo,
            )
            .group_by(func.date(Ligacao.data_hora))
            .all()
        ):
            if data_ligacao:
                ligacoes_por_data[data_ligacao] += int(total or 0)

        retornos_por_data = defaultdict(int)
        total_retorno_inicial = 0
        retorno_query = (
            db.session.query(Cliente.proxima_ligacao)
            .filter(
                Cliente.ativo == True,
                Cliente.proxima_ligacao.isnot(None),
            )
        )
        if filtrar_carteira_por_vinculo:
            retorno_query = retorno_query.filter(
                Cliente.consultor_id.in_(operadores_ids_query)
            )
        for (proxima_ligacao,) in retorno_query.all():
            if not proxima_ligacao:
                continue
            data_retorno = proxima_ligacao.date()
            if data_retorno < data_inicio_analise:
                total_retorno_inicial += 1
            elif data_inicio_analise <= data_retorno <= data_fim_analise:
                retornos_por_data[data_retorno] += 1

        total_usuarios_cumulativo = 0
        mapa_usuarios_ate = {}
        total_retornos_cumulativo = total_retorno_inicial
        mapa_retornos_ate = {}
        for data_ref in dias_analise:
            total_usuarios_cumulativo += usuarios_por_data.get(data_ref, 0)
            mapa_usuarios_ate[data_ref] = total_usuarios_cumulativo
            total_retornos_cumulativo += retornos_por_data.get(data_ref, 0)
            mapa_retornos_ate[data_ref] = total_retornos_cumulativo

        def _count_usuarios_ate(data_ref):
            return int(mapa_usuarios_ate.get(data_ref, 0))

        def _contagens_carteira_por_data(data_ref):
            fim = datetime.combine(data_ref, datetime.max.time())
            limite_180_ref = fim - timedelta(days=180)
            limite_151_ref = fim - timedelta(days=151)
            limite_181_ref = fim - timedelta(days=181)
            limite_1095_ref = fim - timedelta(days=1095)
            limite_150_ref = fim - timedelta(days=150)
            limite_90_ref = fim - timedelta(days=90)
            ativos_snapshot_ref = carregar_snapshot_ativos_oracle_na_data_ou_anterior(data_ref)
            inativos_snapshot_ref = carregar_snapshot_inativos_oracle_na_data_ou_anterior(data_ref)
            construtoras_snapshot_ref = carregar_snapshot_construtoras_oracle_na_data_ou_anterior(data_ref)
            oracle_snapshot_ref = carregar_snapshot_oracle_90_150_na_data_ou_anterior(data_ref)
            codigos_ativos_ref = set()
            codigos_90_150_ref = set()
            codigos_proximos_ref = set()
            codigos_inativos_ref = set()
            for cd_cliente, dt_pedido in clientes_oracle_local:
                if limite_180_ref <= dt_pedido <= fim:
                    codigos_ativos_ref.add(cd_cliente)
                if limite_150_ref <= dt_pedido <= limite_90_ref:
                    codigos_90_150_ref.add(cd_cliente)
                if limite_180_ref <= dt_pedido <= limite_151_ref:
                    codigos_proximos_ref.add(cd_cliente)
                if limite_1095_ref <= dt_pedido <= limite_181_ref:
                    codigos_inativos_ref.add(cd_cliente)
            total_ativos_ref = int((ativos_snapshot_ref or {}).get("total") or len(codigos_ativos_ref))
            total_90_150_ref = 0 if dashboard_tipo == "televendas" else int((oracle_snapshot_ref or {}).get("total") or len(codigos_90_150_ref))
            total_proximos_ref = 0 if dashboard_tipo == "televendas" else len(codigos_proximos_ref)
            total_inativos_ref = int((inativos_snapshot_ref or {}).get("total") or len(codigos_inativos_ref))
            total_construtoras_ref = int((construtoras_snapshot_ref or {}).get("total") or 0)
            return {
                "clientes": (
                    total_inativos_ref + total_construtoras_ref
                    if dashboard_tipo == "televendas"
                    else (
                        total_ativos_ref
                        + total_90_150_ref
                        + total_proximos_ref
                        + total_inativos_ref
                        + total_construtoras_ref
                    )
                ),
                "sem_pedido": total_90_150_ref,
                "proximos": total_proximos_ref,
                "inativos": total_inativos_ref,
                "construtoras": total_construtoras_ref,
            }

        contagens_atual = [_contagens_carteira_por_data(d) for d in dias_atual]
        contagens_anterior = [_contagens_carteira_por_data(d) for d in dias_anterior]

        def _count_clientes_ate(idx, atual=True):
            base = contagens_atual if atual else contagens_anterior
            return int((base[idx] or {}).get("clientes") or 0)

        def _count_sem_pedido_idx(idx, atual=True):
            base = contagens_atual if atual else contagens_anterior
            return int((base[idx] or {}).get("sem_pedido") or 0)

        def _count_proximos_idx(idx, atual=True):
            base = contagens_atual if atual else contagens_anterior
            return int((base[idx] or {}).get("proximos") or 0)

        def _count_inativos_idx(idx, atual=True):
            base = contagens_atual if atual else contagens_anterior
            return int((base[idx] or {}).get("inativos") or 0)

        def _count_construtoras_idx(idx, atual=True):
            base = contagens_atual if atual else contagens_anterior
            return int((base[idx] or {}).get("construtoras") or 0)

        def _count_ligacoes_dia(data_ref):
            return int(ligacoes_por_data.get(data_ref, 0))

        def _count_retornos_atrasados(data_ref):
            return int(mapa_retornos_ate.get(data_ref, 0))

        sparkline_cards = {
            "consultores": _montar_comparativo_card(
                "consultores",
                [_count_usuarios_ate(d) for d in dias_atual],
                [_count_usuarios_ate(d) for d in dias_anterior],
            ),
            "clientes": _montar_comparativo_card(
                "clientes",
                [_count_clientes_ate(i, True) for i, _ in enumerate(dias_atual)],
                [_count_clientes_ate(i, False) for i, _ in enumerate(dias_anterior)],
            ),
            "ligacoes": _montar_comparativo_card(
                "ligacoes",
                [_count_ligacoes_dia(d) for d in dias_atual],
                [_count_ligacoes_dia(d) for d in dias_anterior],
                fluxo=True,
            ),
            "hoje": _montar_comparativo_card(
                "hoje",
                [_count_ligacoes_dia(d) for d in dias_atual],
                [_count_ligacoes_dia(d) for d in dias_anterior],
                fluxo=True,
            ),
            "sem_pedido": _montar_comparativo_card(
                "sem_pedido",
                [_count_sem_pedido_idx(i, True) for i, _ in enumerate(dias_atual)],
                [_count_sem_pedido_idx(i, False) for i, _ in enumerate(dias_anterior)],
            ),
            "proximos": _montar_comparativo_card(
                "proximos",
                [_count_proximos_idx(i, True) for i, _ in enumerate(dias_atual)],
                [_count_proximos_idx(i, False) for i, _ in enumerate(dias_anterior)],
            ),
            "inativos": _montar_comparativo_card(
                "inativos",
                [_count_inativos_idx(i, True) for i, _ in enumerate(dias_atual)],
                [_count_inativos_idx(i, False) for i, _ in enumerate(dias_anterior)],
            ),
            "construtoras": _montar_comparativo_card(
                "construtoras",
                [_count_construtoras_idx(i, True) for i, _ in enumerate(dias_atual)],
                [_count_construtoras_idx(i, False) for i, _ in enumerate(dias_anterior)],
            ),
            "retornos": _montar_comparativo_card(
                "retornos",
                [_count_retornos_atrasados(d) for d in dias_atual],
                [_count_retornos_atrasados(d) for d in dias_anterior],
            ),
        }

        resultado = {
            "total_consultores": total_consultores,
            "total_clientes": total_clientes,
            "total_ligacoes": total_ligacoes,
            "ligacoes_hoje": ligacoes_hoje,
            "total_sem_pedido_90_150": total_sem_pedido_90_150,
            "total_proximos_inativacao": total_proximos_inativacao,
            "total_inativos": total_inativos,
            "total_ativos": total_ativos,
            "total_construtoras": total_construtoras,
            "total_retorno_atrasado": total_retorno_atrasado,
            "total_carteira_risco": total_carteira_risco,
            "sparkline_cards": sparkline_cards,
        }
        _SUPERVISOR_KPI_CACHE[cache_key] = {
            "ts": datetime.now(),
            "data": deepcopy(resultado),
        }
        return resultado

    def _carregar_dados_dashboard_supervisor(
        dashboard_tipo: str,
        periodo: dict,
    ) -> dict:
        periodo_chave = f"{periodo.get('inicio')}:{periodo.get('fim_exclusivo')}:{periodo.get('modo')}"
        cache_key = f"{dashboard_tipo}:{periodo_chave}"
        cache_item = _SUPERVISOR_DADOS_CACHE.get(cache_key)
        if cache_item and (datetime.now() - cache_item["ts"]) <= _SUPERVISOR_DADOS_CACHE_TTL:
            return deepcopy(cache_item["data"])

        inicio_periodo = datetime.combine(periodo["inicio"], datetime.min.time())
        fim_periodo = datetime.combine(periodo["fim_exclusivo"], datetime.min.time())
        inicio_periodo_anterior = datetime.combine(periodo["inicio_anterior"], datetime.min.time())
        fim_periodo_anterior = datetime.combine(periodo["fim_exclusivo_anterior"], datetime.min.time())
        granularidade = periodo.get("granularidade_grafico") or "dia"
        hoje = datetime.now().date()

        def _montar_comparativo_card(chave: str, serie_atual: list[int], serie_anterior: list[int], *, fluxo=False):
            valor_atual = int(sum(serie_atual or [])) if fluxo else int((serie_atual or [0])[-1] or 0)
            valor_anterior = int(sum(serie_anterior or [])) if fluxo else int((serie_anterior or [0])[-1] or 0)
            diferenca = valor_atual - valor_anterior
            label_comparativo = periodo.get("label_comparativo") or "periodo anterior"
            if valor_anterior:
                percentual = round((diferenca / valor_anterior) * 100, 1)
                texto = f"{percentual:+.1f}% vs {label_comparativo}"
            elif valor_atual:
                texto = f"Novo vs {label_comparativo}"
            else:
                texto = f"Estavel vs {label_comparativo}"
            if diferenca == 0 and valor_atual == valor_anterior:
                texto = f"Estavel vs {label_comparativo}"
            return {
                "key": chave,
                "serie": [int(v or 0) for v in serie_atual],
                "anterior": [int(v or 0) for v in serie_anterior],
                "delta": diferenca,
                "texto": texto,
                "classe": "positive" if diferenca > 0 else ("negative" if diferenca < 0 else "neutral"),
            }

        def _extrair_series_periodo(inicio, fim):
            linhas = (
                db.session.query(
                    func.date(Ligacao.data_hora).label("data_ref"),
                    func.count(Ligacao.id).label("total_ligacoes"),
                    func.sum(case((Ligacao.resultado == "comprou", 1), else_=0)).label("total_vendas"),
                )
                .join(Usuario, Usuario.id == Ligacao.consultor_id)
                .filter(
                    Ligacao.data_hora >= inicio,
                    Ligacao.data_hora < fim,
                    Usuario.tipo == dashboard_tipo,
                    Usuario.ativo == True,
                )
                .group_by(func.date(Ligacao.data_hora))
                .order_by(func.date(Ligacao.data_hora))
                .all()
            )

            if granularidade == "mes":
                labels = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
                ligacoes = [0] * 12
                vendas = [0] * 12
                for row in linhas:
                    data_ref = row.data_ref
                    if not data_ref:
                        continue
                    mes_idx = int(getattr(data_ref, "month", 0) or 0) - 1
                    if 0 <= mes_idx < 12:
                        ligacoes[mes_idx] += int(row.total_ligacoes or 0)
                        vendas[mes_idx] += int(row.total_vendas or 0)
                return labels, ligacoes, vendas

            dias_no_mes = monthrange(periodo["ano"], periodo["mes"])[1]
            labels = [f"{dia:02d}" for dia in range(1, dias_no_mes + 1)]
            ligacoes = [0] * dias_no_mes
            vendas = [0] * dias_no_mes
            for row in linhas:
                data_ref = row.data_ref
                if not data_ref:
                    continue
                dia_idx = int(getattr(data_ref, "day", 0) or 0) - 1
                if 0 <= dia_idx < dias_no_mes:
                    ligacoes[dia_idx] += int(row.total_ligacoes or 0)
                    vendas[dia_idx] += int(row.total_vendas or 0)
            return labels, ligacoes, vendas

        rows = (
            db.session.query(Usuario.nome, func.count(Ligacao.id))
            .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .filter(
                or_(
                    Ligacao.id == None,
                    (Ligacao.data_hora >= inicio_periodo) & (Ligacao.data_hora < fim_periodo),
                )
            )
            .group_by(Usuario.id, Usuario.nome)
            .order_by(desc(func.count(Ligacao.id)))
            .all()
        )
        ranking = [{"nome": n, "ligacoes": int(q or 0)} for n, q in rows]

        labels_atuais, serie_ligacoes_atuais, serie_vendas_atuais = _extrair_series_periodo(
            inicio_periodo, fim_periodo
        )
        _, serie_ligacoes_anteriores, serie_vendas_anteriores = _extrair_series_periodo(
            inicio_periodo_anterior, fim_periodo_anterior
        )
        lig_por_dia = [
            {
                "data": labels_atuais[idx],
                "data_iso": labels_atuais[idx],
                "total": int(serie_ligacoes_atuais[idx] or 0),
            }
            for idx in range(len(labels_atuais))
        ]
        chart_periodo_label = periodo.get("label") or "Periodo"

        res = (
            db.session.query(Ligacao.resultado, func.count(Ligacao.id))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Ligacao.data_hora >= inicio_periodo, Ligacao.data_hora < fim_periodo)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .group_by(Ligacao.resultado)
            .all()
        )
        resultados_chart = {(r or "nao_comprou"): int(c) for r, c in res}
        total_resultados_periodo = sum(int(v or 0) for v in resultados_chart.values())
        total_vendas_periodo = int(resultados_chart.get("comprou", 0))
        taxa_conversao_geral_periodo = (
            round(_percent(total_vendas_periodo, total_resultados_periodo), 1) if total_resultados_periodo else 0.0
        )

        consultores = Usuario.query.filter_by(tipo=dashboard_tipo, ativo=True).order_by(Usuario.nome).all()
        ligacoes_hoje_por_operador = {
            int(uid): int(total or 0)
            for uid, total in (
                db.session.query(Ligacao.consultor_id, func.count(Ligacao.id))
                .join(Usuario, Usuario.id == Ligacao.consultor_id)
                .filter(
                    Usuario.tipo == dashboard_tipo,
                    Usuario.ativo == True,
                    func.date(Ligacao.data_hora) == hoje,
                )
                .group_by(Ligacao.consultor_id)
                .all()
            )
            if uid
        }
        progresso = []
        for u in consultores:
            feitas = ligacoes_hoje_por_operador.get(int(u.id), 0)
            meta = u.meta_diaria or 0
            perc = round(_percent(feitas, meta), 1) if meta else 0.0
            progresso.append({"id": u.id, "nome": u.nome, "meta": meta, "feitas": int(feitas), "percentual": perc})

        conv_rows = (
            db.session.query(
                Usuario.id,
                Usuario.nome,
                func.count(Ligacao.id).label("ligacoes"),
                func.sum(case((Ligacao.resultado == "comprou", 1), else_=0)).label("vendas"),
                func.sum(case((Ligacao.resultado == "comprou", Ligacao.valor_venda), else_=0)).label("receita"),
            )
            .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
            .filter(
                or_(
                    Ligacao.id == None,
                    (Ligacao.data_hora >= inicio_periodo) & (Ligacao.data_hora < fim_periodo),
                )
            )
            .group_by(Usuario.id, Usuario.nome)
            .order_by(desc("receita"))
            .all()
        )

        conversao = []
        for _, nome, ligs, vend, rec in conv_rows:
            ligs = int(ligs or 0)
            vend = int(vend or 0)
            receita_val = float(rec or 0)
            conv_pct = (vend / ligs * 100) if ligs else 0.0
            conversao.append(
                {
                    "nome": nome,
                    "ligacoes": ligs,
                    "vendas": vend,
                    "conversao": round(conv_pct, 1),
                    "receita": receita_val,
                    "receita_fmt": formatar_dinheiro(receita_val),
                }
            )

        resultado = {
            "ranking": ranking,
            "ligacoes_por_dia": lig_por_dia,
            "chart_periodo_label": chart_periodo_label,
            "chart_titulo_periodo": periodo.get("titulo_grafico") or "por dia",
            "resultados_chart": resultados_chart,
            "total_ligacoes_periodo": int(sum(serie_ligacoes_atuais or [])),
            "total_vendas_periodo": total_vendas_periodo,
            "taxa_conversao_periodo": taxa_conversao_geral_periodo,
            "sparkline_cards_periodo": {
                "ligacoes_periodo": _montar_comparativo_card(
                    "ligacoes_periodo",
                    serie_ligacoes_atuais,
                    serie_ligacoes_anteriores,
                    fluxo=True,
                ),
                "vendas_periodo": _montar_comparativo_card(
                    "vendas_periodo",
                    serie_vendas_atuais,
                    serie_vendas_anteriores,
                    fluxo=True,
                ),
            },
            "progresso": progresso,
            "consultores": consultores,
            "conversao": conversao,
            "meses_disponiveis": _ultimos_meses(12),
        }
        _SUPERVISOR_DADOS_CACHE[cache_key] = {
            "ts": datetime.now(),
            "data": deepcopy(resultado),
        }
        return resultado

    def _montar_contexto_supervisor_dashboard(
        dashboard_tipo: str,
        dashboard_titulo: str,
        mes_filtro: int,
        ano_filtro: int,
        mostrar_novidades: bool,
        secao_atual: str = "dashboard",
    ) -> dict:
        perf_total = time.perf_counter()
        periodo_dashboard = _resolver_periodo_dashboard(ano_filtro, mes_filtro)
        hoje = datetime.now().date()
        operadores_ids_query = (
            db.session.query(Usuario.id)
            .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
        )
        # Regra operacional: tudo por vinculados do tipo ativo,
        # exceto inativos (global).
        filtrar_carteira_por_vinculo = True
        perf_step = time.perf_counter()
        kpis = _calcular_kpis_dashboard_supervisor(
            dashboard_tipo=dashboard_tipo,
            operadores_ids_query=operadores_ids_query,
            filtrar_carteira_por_vinculo=filtrar_carteira_por_vinculo,
            hoje=hoje,
            periodo=periodo_dashboard,
        )
        _log_perf_supervisor(app, "kpis", perf_step, tipo=dashboard_tipo, secao=secao_atual)
        perf_step = time.perf_counter()
        dados_dashboard = _carregar_dados_dashboard_supervisor(
            dashboard_tipo=dashboard_tipo,
            periodo=periodo_dashboard,
        )
        _log_perf_supervisor(app, "dados_dashboard", perf_step, tipo=dashboard_tipo, secao=secao_atual)
        sparkline_cards = dict(kpis.get("sparkline_cards") or {})
        sparkline_cards.update(dados_dashboard.get("sparkline_cards_periodo") or {})
        movimento_inativos_hoje = {}
        movimento_inativos_detalhes = {}
        movimento_carteiras_hoje = {}
        if secao_atual != "dashboard":
            perf_step = time.perf_counter()
            resumo_sync_hoje = SyncResumoDiario.query.filter_by(data_ref=datetime.now().date()).first()
            movimento_inativos_hoje = {
                "entraram": int(resumo_sync_hoje.inativos_entraram) if resumo_sync_hoje else 0,
                "sairam": int(resumo_sync_hoje.inativos_sairam) if resumo_sync_hoje else 0,
                "total": int(resumo_sync_hoje.total_inativos) if resumo_sync_hoje else 0,
                "atualizado_em": (resumo_sync_hoje.atualizado_em if resumo_sync_hoje else None),
            }
            movimento_inativos_detalhes_raw = carregar_movimento_inativos(datetime.now().date()) or {}
            _log_perf_supervisor(app, "movimento_base", perf_step, tipo=dashboard_tipo, secao=secao_atual)
        else:
            movimento_inativos_detalhes_raw = {}
            _log_perf_supervisor(app, "movimento_base", time.perf_counter(), tipo=dashboard_tipo, secao=secao_atual, skipped=True)
        if secao_atual == "dashboard":
            _log_perf_supervisor(app, "total_contexto", perf_total, tipo=dashboard_tipo, secao=secao_atual)
            return {
                **kpis,
                **dados_dashboard,
                "sparkline_cards": sparkline_cards,
                "movimento_inativos_hoje": movimento_inativos_hoje,
                "movimento_inativos_detalhes": movimento_inativos_detalhes,
                "movimento_carteiras_hoje": movimento_carteiras_hoje,
                "dashboard_tipo": dashboard_tipo,
                "dashboard_titulo": dashboard_titulo,
                "dashboard_periodo_modo": periodo_dashboard["modo"],
                "dashboard_periodo_label": periodo_dashboard["label"],
                "dashboard_periodo_texto_curto": periodo_dashboard["texto_curto"],
                "dashboard_anos_disponiveis": _anos_disponiveis_dashboard(),
                "dashboard_meses_disponiveis": _meses_disponiveis_dashboard(),
                "mes_filtro": mes_filtro,
                "ano_filtro": ano_filtro,
                "mostrar_novidades": mostrar_novidades,
                "banners_ativos": get_banners_ativos(),
            }
        def _parse_dt_iso(valor):
            if not valor:
                return None
            try:
                return datetime.fromisoformat(str(valor))
            except Exception:
                return None

        mov_90_150_raw = carregar_movimento_carteira("oracle_90_150", datetime.now().date()) or {}
        mov_proximos_raw = carregar_movimento_carteira("proximos_inativacao", datetime.now().date()) or {}

        limite_90 = datetime.now() - timedelta(days=90)
        limite_150 = datetime.now() - timedelta(days=150)
        limite_151 = datetime.now() - timedelta(days=151)
        limite_180 = datetime.now() - timedelta(days=180)
        limite_181 = datetime.now() - timedelta(days=181)
        limite_1095 = datetime.now() - timedelta(days=1095)

        base_filtro_cliente = [
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
        ]

        faixa_90_150_atual = {
            str(cd or "").strip()
            for (cd,) in (
                db.session.query(Cliente.cd_cliente_oracle)
                .join(Usuario, Usuario.id == Cliente.consultor_id)
                .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
                .filter(*base_filtro_cliente)
                .filter(Cliente.ultimo_pedido_oracle.between(limite_150, limite_90))
                .all()
            )
            if str(cd or "").strip()
        }
        faixa_proximos_atual = {
            str(cd or "").strip()
            for (cd,) in (
                db.session.query(Cliente.cd_cliente_oracle)
                .join(Usuario, Usuario.id == Cliente.consultor_id)
                .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
                .filter(*base_filtro_cliente)
                .filter(Cliente.ultimo_pedido_oracle.between(limite_180, limite_151))
                .all()
            )
            if str(cd or "").strip()
        }
        faixa_inativos_atual = {
            str(cd or "").strip()
            for (cd,) in (
                db.session.query(Cliente.cd_cliente_oracle)
                .join(Usuario, Usuario.id == Cliente.consultor_id)
                .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
                .filter(*base_filtro_cliente)
                .filter(Cliente.ultimo_pedido_oracle.between(limite_1095, limite_181))
                .all()
            )
            if str(cd or "").strip()
        }

        detalhes_cliente_por_cd = {}
        codigos_mov = {
            str((it or {}).get("cd_cliente") or "").strip()
            for it in (
                list(mov_90_150_raw.get("sairam") or [])
                + list(mov_proximos_raw.get("sairam") or [])
                + list(movimento_inativos_detalhes_raw.get("sairam") or [])
            )
        }
        codigos_mov = {cd for cd in codigos_mov if cd}
        if codigos_mov:
            rows_cli = (
                Cliente.query
                .filter(Cliente.cd_cliente_oracle.in_(list(codigos_mov)))
                .all()
            )
            for cli in rows_cli:
                cd = str(cli.cd_cliente_oracle or "").strip()
                if cd and cd not in detalhes_cliente_por_cd:
                    detalhes_cliente_por_cd[cd] = cli

        def _motivo_detalhado_saida(cd: str, origem: str):
            cd_norm = str(cd or "").strip()
            if not cd_norm:
                return ("Sem codigo do cliente", "")
            cli = detalhes_cliente_por_cd.get(cd_norm)
            if not cli:
                return ("Nao encontrado na base apos sincronizacao", "")

            dt_ult = getattr(cli, "ultimo_pedido_oracle", None)
            dias_sem = (datetime.now() - dt_ult).days if dt_ult else None

            if origem == "oracle_90_150":
                if dias_sem is not None and dias_sem < 90:
                    return ("Fez pedido recente", f"{dias_sem} dias sem pedido")
                if cd_norm in faixa_proximos_atual:
                    return ("Foi para Proximos Inativacao", f"{dias_sem or '-'} dias sem pedido")
                if cd_norm in faixa_inativos_atual:
                    return ("Passou de 180 dias e virou Inativo", f"{dias_sem or '-'} dias sem pedido")
                if dias_sem is not None and dias_sem > 1095:
                    return ("Saiu da janela (>1095 dias sem pedido)", f"{dias_sem} dias sem pedido")
                if not bool(getattr(cli, "ativo", True)):
                    return ("Fora da base local (validar Oracle)", f"{dias_sem or '-'} dias sem pedido")
                if dias_sem is None:
                    return ("Sem data valida de ultimo pedido", "")
                return ("Mudanca de base/carteira", f"{dias_sem} dias sem pedido")

            if origem == "proximos_inativacao":
                if dias_sem is not None and dias_sem < 151:
                    return ("Fez pedido recente", f"{dias_sem} dias sem pedido")
                if cd_norm in faixa_inativos_atual:
                    return ("Foi para carteira de Inativos", f"{dias_sem or '-'} dias sem pedido")
                if cd_norm in faixa_90_150_atual:
                    return ("Voltou para Sem Pedido 90-150", f"{dias_sem or '-'} dias sem pedido")
                if dias_sem is not None and dias_sem > 1095:
                    return ("Saiu da janela (>1095 dias sem pedido)", f"{dias_sem} dias sem pedido")
                if not bool(getattr(cli, "ativo", True)):
                    return ("Fora da base local (validar Oracle)", f"{dias_sem or '-'} dias sem pedido")
                if dias_sem is None:
                    return ("Sem data valida de ultimo pedido", "")
                return ("Mudanca de base/carteira", f"{dias_sem} dias sem pedido")

            if dias_sem is None:
                return ("Movimento de carteira", "")
            return ("Movimento de carteira", f"{dias_sem} dias sem pedido")

        def _anotar_saidas_oracle_90_150(itens):
            out = []
            for item in list(itens or []):
                d = dict(item or {})
                cd = str(d.get("cd_cliente") or "").strip()
                motivo, detalhe = _motivo_detalhado_saida(cd, "oracle_90_150")
                d["motivo_movimento"] = motivo
                d["motivo_detalhe"] = detalhe
                out.append(d)
            return out

        def _anotar_saidas_proximos(itens):
            out = []
            for item in list(itens or []):
                d = dict(item or {})
                cd = str(d.get("cd_cliente") or "").strip()
                motivo, detalhe = _motivo_detalhado_saida(cd, "proximos_inativacao")
                d["motivo_movimento"] = motivo
                d["motivo_detalhe"] = detalhe
                out.append(d)
            return out

        def _anotar_saidas_inativos(itens):
            out = []
            for item in list(itens or []):
                d = dict(item or {})
                cd = str(d.get("cd_cliente") or "").strip()
                cli = detalhes_cliente_por_cd.get(cd)
                dt_ult = getattr(cli, "ultimo_pedido_oracle", None) if cli else None
                dias_sem = (datetime.now() - dt_ult).days if dt_ult else None

                if dias_sem is not None and dias_sem < 181:
                    if cd in faixa_90_150_atual:
                        motivo = "Fez pedido e voltou para Sem Pedido 90-150"
                    elif cd in faixa_proximos_atual:
                        motivo = "Fez pedido e foi para Proximos Inativacao"
                    else:
                        motivo = "Fez pedido recente"
                    detalhe = f"{dias_sem} dias sem pedido"
                elif dias_sem is not None and dias_sem > 1095:
                    motivo = "Saiu da janela (>1095 dias sem pedido)"
                    detalhe = f"{dias_sem} dias sem pedido"
                elif not cli or not bool(getattr(cli, "ativo", True)):
                    motivo = "Fora da base local (validar Oracle)"
                    detalhe = f"{dias_sem or '-'} dias sem pedido"
                elif dias_sem is None:
                    motivo = "Sem data valida de ultimo pedido"
                    detalhe = ""
                else:
                    motivo = "Mudanca de base/carteira"
                    detalhe = f"{dias_sem} dias sem pedido"

                d["motivo_movimento"] = motivo
                d["motivo_detalhe"] = detalhe
                out.append(d)
            return out

        movimento_inativos_detalhes = dict(movimento_inativos_detalhes_raw or {})
        movimento_inativos_detalhes["sairam"] = _anotar_saidas_inativos(
            movimento_inativos_detalhes_raw.get("sairam") or []
        )

        movimento_carteiras_hoje = {
            "oracle_90_150": {
                "entraram": list(mov_90_150_raw.get("entraram") or []),
                "sairam": _anotar_saidas_oracle_90_150(mov_90_150_raw.get("sairam") or []),
                "total": int(mov_90_150_raw.get("total") or kpis.get("total_sem_pedido_90_150") or 0),
                "atualizado_em": _parse_dt_iso(mov_90_150_raw.get("atualizado_em")),
            },
            "proximos_inativacao": {
                "entraram": list(mov_proximos_raw.get("entraram") or []),
                "sairam": _anotar_saidas_proximos(mov_proximos_raw.get("sairam") or []),
                "total": int(mov_proximos_raw.get("total") or kpis.get("total_proximos_inativacao") or 0),
                "atualizado_em": _parse_dt_iso(mov_proximos_raw.get("atualizado_em")),
            },
        }

        _log_perf_supervisor(app, "total_contexto", perf_total, tipo=dashboard_tipo, secao=secao_atual)
        return {
            **kpis,
            **dados_dashboard,
            "sparkline_cards": sparkline_cards,
            "movimento_inativos_hoje": movimento_inativos_hoje,
            "movimento_inativos_detalhes": movimento_inativos_detalhes,
            "movimento_carteiras_hoje": movimento_carteiras_hoje,
            "dashboard_tipo": dashboard_tipo,
            "dashboard_titulo": dashboard_titulo,
            "dashboard_periodo_modo": periodo_dashboard["modo"],
            "dashboard_periodo_label": periodo_dashboard["label"],
            "dashboard_periodo_texto_curto": periodo_dashboard["texto_curto"],
            "dashboard_anos_disponiveis": _anos_disponiveis_dashboard(),
            "dashboard_meses_disponiveis": _meses_disponiveis_dashboard(),
            "mes_filtro": mes_filtro,
            "ano_filtro": ano_filtro,
            "mostrar_novidades": mostrar_novidades,
            "banners_ativos": get_banners_ativos(),
        }

    def _consultar_ligacoes_mes_supervisor(
        *,
        mes: int,
        ano: int,
        tipo_operador: str,
        consultor_id: int | None,
    ):
        inicio = datetime(ano, mes, 1)
        fim = datetime(ano + (1 if mes == 12 else 0), (1 if mes == 12 else mes + 1), 1)

        consultor_nome = "Todos os operadores"
        if consultor_id:
            consultor = Usuario.query.filter_by(id=consultor_id, tipo=tipo_operador, ativo=True).first()
            if not consultor:
                return {"ok": False, "erro": "Operador invalido"}, 400
            consultor_nome = consultor.nome

        query = (
            Ligacao.query.options(joinedload(Ligacao.consultor), joinedload(Ligacao.cliente))
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Ligacao.data_hora >= inicio, Ligacao.data_hora < fim)
            .filter(Usuario.tipo == tipo_operador, Usuario.ativo == True)
        )
        if consultor_id:
            query = query.filter(Ligacao.consultor_id == consultor_id)

        ligacoes = query.order_by(Ligacao.data_hora.desc()).all()

        itens = []
        vendas = 0
        receita = 0.0
        for lig in ligacoes:
            resultado = lig.resultado or "nao_comprou"
            valor = float(lig.valor_venda or 0)
            if resultado == "comprou":
                vendas += 1
                receita += valor

            itens.append(
                {
                    "id": lig.id,
                    "data_hora": lig.data_hora.strftime("%d/%m/%Y %H:%M"),
                    "consultor": lig.consultor.nome if lig.consultor else "-",
                    "cliente": lig.cliente.nome if lig.cliente else "-",
                    "contato": lig.contato_nome or "-",
                    "resultado": resultado,
                    "valor": valor,
                    "valor_fmt": formatar_dinheiro(valor),
                    "observacao": lig.observacao or "",
                }
            )

        total = len(itens)
        conversao = _percent(vendas, total) if total else 0.0

        payload = {
            "ok": True,
            "mes": mes,
            "ano": ano,
            "consultor_id": consultor_id,
            "consultor_nome": consultor_nome,
            "ligacoes": itens,
            "estatisticas": {
                "total_ligacoes": total,
                "vendas": vendas,
                "conversao": round(conversao, 1),
                "receita": receita,
                "receita_fmt": formatar_dinheiro(receita),
            },
        }
        return payload, 200

    def _analisar_observacoes_mes_supervisor(
        *,
        mes: int,
        ano: int,
        tipo_operador: str,
    ):
        inicio = datetime(ano, mes, 1)
        fim = datetime(ano + (1 if mes == 12 else 0), (1 if mes == 12 else mes + 1), 1)

        linhas = (
            db.session.query(Usuario.nome, Ligacao.observacao)
            .join(Usuario, Usuario.id == Ligacao.consultor_id)
            .filter(Ligacao.data_hora >= inicio, Ligacao.data_hora < fim)
            .filter(Usuario.tipo == tipo_operador, Usuario.ativo == True)
            .filter(Ligacao.observacao.isnot(None))
            .all()
        )

        def _norm(txt: str) -> str:
            base = str(txt or "").strip().lower()
            base = unicodedata.normalize("NFD", base)
            base = "".join(ch for ch in base if unicodedata.category(ch) != "Mn")
            return base

        categorias_regras = {
            "Preço": ("preco", "caro", "desconto", "valor", "custo", "orcamento"),
            "Estoque/Prazo": ("estoque", "falta", "prazo", "entrega", "demora", "aguardando"),
            "Concorrência": ("concorrente", "concorrencia", "outra marca", "outra loja"),
            "Timing/Retorno": ("retornar", "retorno", "depois", "proximo mes", "sem tempo"),
            "Contato": ("nao atende", "nao atendeu", "telefone", "whatsapp", "wats", "enviado", "catalogo"),
            "Crédito": ("credito", "limite", "inadimpl", "boleto", "pagamento"),
        }
        stopwords = {
            "de", "da", "do", "e", "a", "o", "em", "no", "na", "para", "com", "sem", "por",
            "um", "uma", "que", "mais", "ja", "foi", "ser", "ao", "as", "os", "dos", "das",
            "cliente", "contato", "ligacao", "hoje", "amanha", "ontem",
            "nao", "sim", "rep", "nosso", "nossa", "dele", "dela", "ele", "ela",
            "watts", "wats", "zap", "enviado", "catalogo", "coloquei", "falei",
        }

        total_obs = 0
        categorias_count = Counter()
        palavras_count = Counter()
        amostras_categoria = defaultdict(list)
        por_operador = defaultdict(lambda: Counter())

        for operador, obs in linhas:
            txt = str(obs or "").strip()
            if not txt:
                continue
            total_obs += 1
            texto = _norm(txt)
            cats = []
            for nome_cat, termos in categorias_regras.items():
                if any(termo in texto for termo in termos):
                    cats.append(nome_cat)
            if not cats:
                cats = ["Outros"]

            for c in cats:
                categorias_count[c] += 1
                por_operador[operador or "-"][c] += 1
                if len(amostras_categoria[c]) < 3:
                    amostras_categoria[c].append(txt[:180])

            tokens = re.findall(r"[a-zA-Z0-9]{3,}", texto)
            for t in tokens:
                if t in stopwords:
                    continue
                palavras_count[t] += 1

        top_categorias = [
            {"categoria": cat, "qtd": int(qtd), "amostras": amostras_categoria.get(cat, [])}
            for cat, qtd in categorias_count.most_common(6)
        ]
        top_categorias_sem_outros = [
            c for c in top_categorias if c.get("categoria") != "Outros"
        ] or top_categorias
        top_palavras = [
            {"palavra": p, "qtd": int(q)}
            for p, q in palavras_count.most_common(12)
        ]
        operadores = []
        for nome, cnt in sorted(por_operador.items(), key=lambda kv: sum(kv[1].values()), reverse=True):
            total = int(sum(cnt.values()))
            principal = cnt.most_common(1)[0][0] if total else "Outros"
            operadores.append(
                {"operador": nome, "total_observacoes": total, "principal": principal}
            )

        return {
            "ok": True,
            "mes": mes,
            "ano": ano,
            "tipo": tipo_operador,
            "total_observacoes": int(total_obs),
            "top_categorias": top_categorias,
            "top_categorias_sem_outros": top_categorias_sem_outros,
            "top_palavras": top_palavras,
            "operadores": operadores,
        }, 200

    def _sincronizar_vinculos_tg650_supervisor_repr(supervisor_id: int, codigo_supervisor_tg650: str):
        codigo_base = s(codigo_supervisor_tg650)
        if not codigo_base:
            return {
                "ok": False,
                "mensagem": "Código TG650 não configurado para este supervisor",
                "novos": 0,
                "atualizados": 0,
            }

        from oracle_service import get_vinculos_supervisor_representante_oracle

        codigos_teste = [codigo_base]
        if codigo_base.isdigit():
            codigo_sem_zero = str(int(codigo_base))
            codigo_3 = codigo_sem_zero.zfill(3)
            for cand in (codigo_sem_zero, codigo_3):
                if cand and cand not in codigos_teste:
                    codigos_teste.append(cand)

        vinculos_oracle = []
        codigo_utilizado = codigo_base
        for codigo_teste in codigos_teste:
            dados = get_vinculos_supervisor_representante_oracle(codigo_teste)
            if dados:
                vinculos_oracle = dados
                codigo_utilizado = codigo_teste
                break

        if not vinculos_oracle:
            return {
                "ok": False,
                "mensagem": "Nenhum vínculo encontrado na TG 650",
                "novos": 0,
                "atualizados": 0,
            }

        novos = 0
        atualizados = 0

        for vinculo_oracle in vinculos_oracle:
            cd_representante = str(vinculo_oracle.get("cd_representante") or "").strip()
            if not cd_representante:
                continue

            nome_representante = vinculo_oracle.get("nome_representante")

            vinculo_local = SupervisorRepresentanteVinculo.query.filter_by(
                supervisor_id=supervisor_id,
                codigo_representante=cd_representante,
            ).first()

            if vinculo_local:
                vinculo_local.nome_representante = nome_representante
                vinculo_local.sincronizado_tg650 = True
                vinculo_local.ativo = True
                vinculo_local.codigo_supervisor_tg650 = codigo_utilizado
                atualizados += 1
            else:
                db.session.add(
                    SupervisorRepresentanteVinculo(
                        supervisor_id=supervisor_id,
                        codigo_representante=cd_representante,
                        nome_representante=nome_representante,
                        ativo=True,
                        sincronizado_tg650=True,
                        codigo_supervisor_tg650=codigo_utilizado,
                    )
                )
                novos += 1

        db.session.commit()
        return {
            "ok": True,
            "mensagem": f"Sincronização concluída! {novos} novos, {atualizados} atualizados.",
            "novos": novos,
            "atualizados": atualizados,
        }

    @app.route("/supervisor/televendas", endpoint="dashboard_supervisor_televendas")
    @app.route("/supervisor", endpoint="dashboard_supervisor")
    @login_required
    def supervisor_dashboard():
        if current_user.tipo != "supervisor":
            return redirect(url_for("meus_clientes"))
        dashboard_tipo = "televendas" if request.path.endswith("/televendas") else "consultor"
        dashboard_titulo = _titulo_dashboard_setor(dashboard_tipo)
        secao_atual = str(request.args.get("secao") or "dashboard").strip().lower()
        ano_filtro = _parse_ano_dashboard(request.args.get("ano"), padrao=datetime.now().year)
        if secao_atual == "fechamento":
            mes_filtro = _parse_mes_dashboard(
                request.args.get("mes"),
                permitir_todos=False,
                padrao=datetime.now().month,
            )
        else:
            mes_filtro = _parse_mes_dashboard(
                request.args.get("mes"),
                permitir_todos=True,
                padrao=0,
            )
        contexto = _montar_contexto_supervisor_dashboard(
            dashboard_tipo=dashboard_tipo,
            dashboard_titulo=dashboard_titulo,
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            mostrar_novidades=not current_user.viu_novidades,
            secao_atual=secao_atual,
        )
        return render_template("supervisor.html", **contexto)

    @app.route("/supervisor/monitoramento/<string:chave>", methods=["GET", "POST"])
    @login_required
    def supervisor_monitoramento_oculto(chave: str):
        if current_user.tipo != "supervisor":
            return redirect(url_for("meus_clientes"))

        chave_esperada = str(SUPERVISOR_SECRET_HEALTH_KEY or "").strip()
        if (not chave_esperada) or (str(chave or "").strip() != chave_esperada):
            return "Nao encontrado", 404

        senha_painel = str(SUPERVISOR_DEV_PANEL_PASSWORD or "").strip()
        if not senha_painel:
            return "Painel dev sem senha configurada no ambiente.", 403

        sess_key = obter_chave_sessao_supervisor_dev() or f"dev_panel_ok::{chave_esperada}"
        if request.method == "POST":
            senha_digitada = str(request.form.get("senha_painel") or "").strip()
            if senha_digitada == senha_painel:
                session[sess_key] = True
                flash("Acesso liberado ao Painel Desenvolvedor.", "success")
                return redirect(url_for("supervisor_monitoramento_oculto", chave=chave_esperada))
            flash("Senha do painel invalida.", "danger")

        acesso_liberado = bool(session.get(sess_key))
        if not acesso_liberado:
            return render_template(
                "supervisor_monitoramento_oculto.html",
                bloqueado=True,
                atualizado_em=datetime.now(),
            )

        agora = datetime.now()
        limite_90 = agora - timedelta(days=90)
        limite_150 = agora - timedelta(days=150)
        limite_151 = agora - timedelta(days=151)
        limite_180 = agora - timedelta(days=180)
        limite_181 = agora - timedelta(days=181)
        limite_1095 = agora - timedelta(days=1095)

        totais = {
            "especiais": int(
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.origem.in_(("manual", "importado_csv")),
                )
                .count()
            ),
            "especiais_sem_sync": int(
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.origem.in_(("manual", "importado_csv")),
                    or_(
                        Cliente.cd_cliente_oracle.is_(None),
                        Cliente.data_ultima_sincronizacao.is_(None),
                    ),
                )
                .count()
            ),
            "sem_pedido_90_150": int(
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_150, limite_90),
                )
                .count()
            ),
            "proximos_inativacao": int(
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_180, limite_151),
                )
                .count()
            ),
            "inativos": int(
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_1095, limite_181),
                )
                .count()
            ),
        }

        totais["especiais_sync"] = max(int(totais["especiais"]) - int(totais["especiais_sem_sync"]), 0)
        totais["taxa_sync_especiais"] = (
            round((totais["especiais_sync"] / totais["especiais"]) * 100, 1)
            if totais["especiais"] > 0 else 0.0
        )

        especiais_pendentes = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.origem.in_(("manual", "importado_csv")),
                or_(
                    Cliente.cd_cliente_oracle.is_(None),
                    Cliente.data_ultima_sincronizacao.is_(None),
                ),
            )
            .all()
        )
        pendentes_criticos = sorted(
            especiais_pendentes,
            key=lambda c: (
                0 if (str(c.origem or "") == "manual") else 1,
                0 if (c.cd_cliente_oracle is None) else 1,
                -(int(c.id or 0)),
            ),
        )[:20]

        pendencias_por_consultor = defaultdict(int)
        for c in especiais_pendentes:
            pendencias_por_consultor[int(c.consultor_id or 0)] += 1

        top_pendencias = []
        if pendencias_por_consultor:
            usuarios = Usuario.query.filter(
                Usuario.id.in_(list(pendencias_por_consultor.keys()))
            ).all()
            mapa_nomes = {u.id: u.nome for u in usuarios}
            top_pendencias = sorted(
                [
                    {
                        "consultor_id": cid,
                        "consultor_nome": mapa_nomes.get(cid, f"ID {cid}"),
                        "pendentes": qtd,
                    }
                    for cid, qtd in pendencias_por_consultor.items()
                ],
                key=lambda x: x["pendentes"],
                reverse=True,
            )[:20]

        duplicados_por_raiz = []
        duplicados_cnpj_completo = []
        grupos_raiz = defaultdict(list)
        grupos_cnpj_completo = defaultdict(list)
        especiais = (
            Cliente.query
            .filter(
                Cliente.ativo == True,
                Cliente.origem.in_(("manual", "importado_csv")),
            )
            .all()
        )
        especiais_cnpj_incompleto = 0
        for c in especiais:
            cnpj_digits = _somente_digitos(c.cnpj)
            nome_normalizado = _normalizar_nome_chave(c.nome)
            if cnpj_digits and nome_normalizado:
                grupos_cnpj_completo[(cnpj_digits, nome_normalizado)].append(c)
            if cnpj_digits and len(cnpj_digits) < 14:
                especiais_cnpj_incompleto += 1
            raiz = cnpj_digits[:8]
            if len(raiz) == 8 and nome_normalizado:
                grupos_raiz[(raiz, nome_normalizado)].append(c)
        for (raiz, _nome_normalizado), itens in grupos_raiz.items():
            if len(itens) <= 1:
                continue
            # Evita falso positivo de rede/filial:
            # so entra como duplicado por raiz quando existir repeticao
            # de CNPJ completo dentro do mesmo grupo (raiz + razao social).
            cnpj_completo_counts = Counter(
                _somente_digitos(c.cnpj) for c in itens if len(_somente_digitos(c.cnpj)) == 14
            )
            cnpjs_repetidos = {cnpj for cnpj, qtd in cnpj_completo_counts.items() if qtd > 1}
            if not cnpjs_repetidos:
                continue

            itens_duplicados_reais = [
                c for c in itens
                if _somente_digitos(c.cnpj) in cnpjs_repetidos
            ]
            cnpjs_completos_distintos = {
                _somente_digitos(c.cnpj)
                for c in itens_duplicados_reais
                if len(_somente_digitos(c.cnpj)) == 14
            }
            # Evita repetir exatamente a mesma visao do "CNPJ completo":
            # so mantem no painel de raiz se houver mais de um CNPJ completo distinto.
            if len(cnpjs_completos_distintos) < 2:
                continue
            duplicados_por_raiz.append(
                {
                    "cnpj_raiz": raiz,
                    "quantidade": len(itens_duplicados_reais),
                    "clientes": [
                        {
                            "id": c.id,
                            "nome": c.nome,
                            "origem": str(c.origem or ""),
                            "consultor_id": c.consultor_id,
                            "cd_cliente_oracle": c.cd_cliente_oracle,
                        }
                        for c in sorted(itens_duplicados_reais, key=lambda x: x.id, reverse=True)[:5]
                    ],
                }
            )
        duplicados_por_raiz = sorted(
            duplicados_por_raiz,
            key=lambda x: x["quantidade"],
            reverse=True,
        )[:30]
        for (cnpj, _nome_normalizado), itens in grupos_cnpj_completo.items():
            if len(itens) <= 1:
                continue
            duplicados_cnpj_completo.append(
                {
                    "cnpj": cnpj,
                    "quantidade": len(itens),
                    "clientes": [
                        {
                            "id": c.id,
                            "nome": c.nome,
                            "origem": str(c.origem or ""),
                            "consultor_id": c.consultor_id,
                            "cd_cliente_oracle": c.cd_cliente_oracle,
                        }
                        for c in sorted(itens, key=lambda x: x.id, reverse=True)[:5]
                    ],
                }
            )
        duplicados_cnpj_completo = sorted(
            duplicados_cnpj_completo,
            key=lambda x: x["quantidade"],
            reverse=True,
        )[:30]

        ultimos_resumos = (
            SyncResumoDiario.query
            .order_by(SyncResumoDiario.data_ref.desc())
            .limit(7)
            .all()
        )
        tendencia_sync = [
            {
                "data_ref": r.data_ref.strftime("%d/%m"),
                "inativos_entraram": int(r.inativos_entraram or 0),
                "inativos_sairam": int(r.inativos_sairam or 0),
                "total_inativos": int(r.total_inativos or 0),
            }
            for r in reversed(ultimos_resumos)
        ]

        usuarios_online = get_active_users_recent(minutes=20)

        ligacoes_recentes = (
            Ligacao.query
            .options(joinedload(Ligacao.consultor), joinedload(Ligacao.cliente))
            .order_by(Ligacao.data_hora.desc())
            .limit(20)
            .all()
        )
        notas_recentes = (
            Nota.query
            .options(joinedload(Nota.usuario), joinedload(Nota.cliente))
            .order_by(Nota.data_criacao.desc())
            .limit(20)
            .all()
        )
        atividade_recente = []
        for l in ligacoes_recentes:
            atividade_recente.append(
                {
                    "tipo": "ligacao",
                    "quando": l.data_hora,
                    "usuario": (l.consultor.nome if l.consultor else "N/A"),
                    "usuario_tipo": (l.consultor.tipo if l.consultor else ""),
                    "cliente": (l.cliente.nome if l.cliente else "N/A"),
                    "detalhe": f"resultado={l.resultado or '-'}",
                }
            )
        for n in notas_recentes:
            atividade_recente.append(
                {
                    "tipo": "nota",
                    "quando": n.data_criacao,
                    "usuario": (n.usuario.nome if n.usuario else "N/A"),
                    "usuario_tipo": (n.usuario.tipo if n.usuario else ""),
                    "cliente": (n.cliente.nome if n.cliente else "N/A"),
                    "detalhe": (str(n.texto or "")[:90]),
                }
            )
        atividade_recente.sort(key=lambda x: (x.get("quando") or datetime.min), reverse=True)
        atividade_recente = atividade_recente[:30]

        return render_template(
            "supervisor_monitoramento_oculto.html",
            bloqueado=False,
            atualizado_em=agora,
            totais=totais,
            especiais_cnpj_incompleto=int(especiais_cnpj_incompleto),
            top_pendencias=top_pendencias,
            pendentes_criticos=pendentes_criticos,
            duplicados_por_raiz=duplicados_por_raiz,
            duplicados_cnpj_completo=duplicados_cnpj_completo,
            tendencia_sync=tendencia_sync,
            usuarios_online=usuarios_online,
            atividade_recente=atividade_recente,
        )

    @app.route("/api/supervisor/ligacoes-por-mes")
    @login_required
    def api_supervisor_ligacoes_por_mes():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "erro": "Acesso negado"}), 403

        try:
            mes = int(request.args.get("mes", datetime.now().month))
            ano = int(request.args.get("ano", datetime.now().year))
            consultor_id = request.args.get("consultor_id", type=int)

            if mes < 1 or mes > 12:
                return jsonify({"ok": False, "erro": "Mes invalido"}), 400

            tipo_operador = (request.args.get("tipo") or "consultor").strip().lower()
            if tipo_operador not in ("consultor", "televendas"):
                return jsonify({"ok": False, "erro": "Tipo de dashboard invalido"}), 400

            payload, status = _consultar_ligacoes_mes_supervisor(
                mes=mes,
                ano=ano,
                tipo_operador=tipo_operador,
                consultor_id=consultor_id,
            )
            return jsonify(payload), status
        except ValueError:
            return jsonify({"ok": False, "erro": "Parametros invalidos"}), 400
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    @app.route("/api/supervisor/observacoes-insights")
    @login_required
    def api_supervisor_observacoes_insights():
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "erro": "Acesso negado"}), 403
        try:
            mes = int(request.args.get("mes", datetime.now().month))
            ano = int(request.args.get("ano", datetime.now().year))
            if mes < 1 or mes > 12:
                return jsonify({"ok": False, "erro": "Mes invalido"}), 400
            tipo_operador = (request.args.get("tipo") or "consultor").strip().lower()
            if tipo_operador not in ("consultor", "televendas"):
                return jsonify({"ok": False, "erro": "Tipo de dashboard invalido"}), 400
            payload, status = _analisar_observacoes_mes_supervisor(
                mes=mes,
                ano=ano,
                tipo_operador=tipo_operador,
            )
            return jsonify(payload), status
        except ValueError:
            return jsonify({"ok": False, "erro": "Parametros invalidos"}), 400
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    @app.route("/supervisor/fechamento/pdf")
    @login_required
    def supervisor_fechamento_pdf():
        if current_user.tipo != "supervisor":
            return redirect(url_for("meus_clientes"))
        try:
            mes = int(request.args.get("mes", datetime.now().month))
            ano = int(request.args.get("ano", datetime.now().year))
            tipo = (request.args.get("tipo") or "consultor").strip().lower()
            if tipo not in ("consultor", "televendas"):
                tipo = "consultor"
            meta_conversao = float(request.args.get("meta_conversao", 10) or 10)
            payload, status = consultar_resultados_consultores_mes(
                mes,
                ano,
                meta_conversao=meta_conversao,
                tipo_operador=tipo,
            )
            if status != 200 or not payload.get("ok"):
                raise RuntimeError(payload.get("erro") or "Falha ao carregar dados de fechamento")

            dashboard_titulo = _titulo_dashboard_setor(tipo)
            pdf_bytes = _gerar_pdf_fechamento(payload, dashboard_titulo)
            filename = f"fechamento_{tipo}_{ano}_{str(mes).zfill(2)}.pdf"
            return send_file(
                BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name=filename,
            )
        except Exception as e:
            flash(f"Não foi possível gerar PDF: {str(e)}", "danger")
            endpoint = "dashboard_supervisor_televendas" if (request.args.get("tipo") or "") == "televendas" else "dashboard_supervisor"
            return redirect(url_for(endpoint, secao="fechamento"))

    @app.route("/supervisor/usuarios")
    @login_required
    def gerenciar_usuarios():
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            flash("Acesso negado.", "danger")
            return redirect(url_for("index"))

        usuarios = Usuario.query.order_by(Usuario.nome.asc()).all()

        usuarios_data = []
        for u in usuarios:
            total_clientes = Cliente.query.filter_by(consultor_id=u.id, ativo=True).count() if u.tipo == "consultor" else 0
            usuarios_data.append(
                {
                    "id": u.id,
                    "nome": u.nome,
                    "email": u.email,
                    "tipo": u.tipo,
                    "ativo": u.ativo,
                    "meta_diaria": u.meta_diaria or 0,
                    "codigo_supervisor_tg650": u.codigo_supervisor_tg650,
                    "codigo_representante": u.codigo_representante,
                    "data_cadastro": u.data_cadastro,
                    "total_clientes": total_clientes,
                }
            )

        return render_template("gerenciar_usuarios.html", usuarios=usuarios_data)

    @app.route("/supervisor/usuarios/criar", methods=["POST"])
    @login_required
    def criar_usuario():
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            payload = request.get_json(silent=True) or {}
            data = _normalizar_payload_usuario(payload, incluir_senha=True)
            nome = data["nome"]
            email = data["email"]
            senha = data["senha"]
            tipo = data["tipo"]
            meta_diaria = data["meta_diaria"]
            codigo_supervisor_tg650 = data["codigo_supervisor_tg650"]
            codigo_representante = data["codigo_representante"]

            if not nome or not email or not senha:
                return jsonify({"ok": False, "mensagem": "Nome, email e senha sao obrigatorios"}), 400

            if tipo not in tipos_usuario_validos:
                return jsonify({"ok": False, "mensagem": "Tipo invalido"}), 400

            if tipo == "representante" and not codigo_representante:
                return jsonify({"ok": False, "mensagem": "Codigo do representante e obrigatorio"}), 400

            if Usuario.query.filter_by(email=email).first():
                return jsonify({"ok": False, "mensagem": "Email ja cadastrado"}), 400

            if tipo == "representante":
                existente_codigo = Usuario.query.filter(
                    Usuario.tipo == "representante",
                    Usuario.codigo_representante == codigo_representante,
                ).first()
                if existente_codigo:
                    return jsonify({"ok": False, "mensagem": "Ja existe usuario para este codigo de representante"}), 400

            novo_usuario = Usuario(
                nome=nome,
                email=email,
                senha_hash=generate_password_hash(senha),
                tipo=tipo,
                meta_diaria=meta_diaria,
                codigo_supervisor_tg650=codigo_supervisor_tg650 if tipo == "supervisor_repr" else None,
                codigo_representante=codigo_representante if tipo == "representante" else None,
                ativo=True,
            )

            db.session.add(novo_usuario)
            db.session.commit()

            mensagem = _complementar_mensagem_sync_tg650(
                mensagem_base=f"Usuario {nome} criado com sucesso!",
                usuario_id=novo_usuario.id,
                tipo=tipo,
                codigo_supervisor_tg650=codigo_supervisor_tg650,
            )

            return jsonify({"ok": True, "mensagem": mensagem})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500
    @app.route("/supervisor/usuarios/<int:usuario_id>/editar", methods=["POST"])
    @login_required
    def editar_usuario(usuario_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            usuario = db.session.get(Usuario, usuario_id)
            if not usuario:
                return jsonify({"ok": False, "mensagem": "Usuario nao encontrado"}), 404

            payload = request.get_json(silent=True) or {}
            data = _normalizar_payload_usuario(payload)
            nome = data["nome"]
            email = data["email"]
            tipo = data["tipo"]
            meta_diaria = data["meta_diaria"]
            codigo_supervisor_tg650 = data["codigo_supervisor_tg650"]
            codigo_representante = data["codigo_representante"]

            if not nome or not email:
                return jsonify({"ok": False, "mensagem": "Nome e email sao obrigatorios"}), 400

            if tipo not in tipos_usuario_validos:
                return jsonify({"ok": False, "mensagem": "Tipo invalido"}), 400

            if tipo == "representante" and not codigo_representante:
                return jsonify({"ok": False, "mensagem": "Codigo do representante e obrigatorio"}), 400

            email_existe = Usuario.query.filter(Usuario.email == email, Usuario.id != usuario_id).first()
            if email_existe:
                return jsonify({"ok": False, "mensagem": "Email ja cadastrado por outro usuario"}), 400

            if tipo == "representante":
                existente_codigo = Usuario.query.filter(
                    Usuario.tipo == "representante",
                    Usuario.codigo_representante == codigo_representante,
                    Usuario.id != usuario_id,
                ).first()
                if existente_codigo:
                    return jsonify({"ok": False, "mensagem": "Ja existe usuario para este codigo de representante"}), 400

            usuario.nome = nome
            usuario.email = email
            usuario.tipo = tipo
            usuario.meta_diaria = meta_diaria
            usuario.codigo_supervisor_tg650 = codigo_supervisor_tg650 if tipo == "supervisor_repr" else None
            usuario.codigo_representante = codigo_representante if tipo == "representante" else None

            db.session.commit()

            mensagem = _complementar_mensagem_sync_tg650(
                mensagem_base=f"Usuario {nome} atualizado com sucesso!",
                usuario_id=usuario.id,
                tipo=tipo,
                codigo_supervisor_tg650=codigo_supervisor_tg650,
            )

            return jsonify({"ok": True, "mensagem": mensagem})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500
    @app.route("/supervisor/usuarios/<int:usuario_id>/toggle-status", methods=["POST"])
    @login_required
    def toggle_status_usuario(usuario_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            usuario = db.session.get(Usuario, usuario_id)
            if not usuario:
                return jsonify({"ok": False, "mensagem": "Usuario nao encontrado"}), 404

            if usuario.id == current_user.id:
                return jsonify({"ok": False, "mensagem": "Voce nao pode inativar sua propria conta"}), 400

            usuario.ativo = not usuario.ativo
            db.session.commit()

            status_texto = "ativado" if usuario.ativo else "inativado"
            return jsonify({"ok": True, "mensagem": f"Usuario {usuario.nome} {status_texto} com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500
    @app.route("/supervisor/usuarios/<int:usuario_id>/redefinir-senha", methods=["POST"])
    @login_required
    def redefinir_senha_usuario(usuario_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            usuario = db.session.get(Usuario, usuario_id)
            if not usuario:
                return jsonify({"ok": False, "mensagem": "Usuario nao encontrado"}), 404

            payload = request.get_json(silent=True) or {}
            nova_senha = payload.get("nova_senha") or ""

            if not nova_senha or len(nova_senha) < 6:
                return jsonify({"ok": False, "mensagem": "Senha deve ter no minimo 6 caracteres"}), 400

            usuario.senha_hash = generate_password_hash(nova_senha)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": f"Senha de {usuario.nome} redefinida com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500
    @app.route("/supervisor/banners")
    @login_required
    def gerenciar_banners():
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return redirect(url_for("meus_clientes"))

        banners = Banner.query.options(joinedload(Banner.criador)).order_by(Banner.data_criacao.desc()).all()
        return render_template("gerenciar_banners.html", banners=banners)

    @app.route("/supervisor/banners/criar", methods=["POST"])
    @login_required
    def criar_banner():
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            payload = request.get_json(silent=True) or {}
            titulo = s(payload.get("titulo"))
            mensagem = s(payload.get("mensagem"))
            tipo = s(payload.get("tipo")) or "info"
            data_expiracao = payload.get("data_expiracao")

            if not titulo or not mensagem:
                return jsonify({"ok": False, "mensagem": "Título e mensagem são obrigatórios"}), 400

            if tipo not in ["info", "warning", "success", "danger"]:
                tipo = "info"

            expiracao_dt = None
            if data_expiracao:
                try:
                    expiracao_dt = datetime.strptime(data_expiracao, "%Y-%m-%d")
                    expiracao_dt = expiracao_dt.replace(hour=23, minute=59, second=59)
                except Exception:
                    return jsonify({"ok": False, "mensagem": "Data de expiração inválida"}), 400

            banner = Banner(
                titulo=titulo,
                mensagem=mensagem,
                tipo=tipo,
                criado_por=current_user.id,
                data_expiracao=expiracao_dt,
            )
            db.session.add(banner)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Banner criado com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/banners/<int:banner_id>/toggle-status", methods=["POST"])
    @login_required
    def toggle_banner_status(banner_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            banner = db.session.get(Banner, banner_id)
            if not banner:
                return jsonify({"ok": False, "mensagem": "Banner não encontrado"}), 404

            banner.ativo = not banner.ativo
            db.session.commit()

            status_texto = "ativado" if banner.ativo else "desativado"
            return jsonify({"ok": True, "mensagem": f"Banner {status_texto} com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/banners/<int:banner_id>/excluir", methods=["POST"])
    @login_required
    def excluir_banner(banner_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            banner = db.session.get(Banner, banner_id)
            if not banner:
                return jsonify({"ok": False, "mensagem": "Banner não encontrado"}), 404

            db.session.delete(banner)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Banner excluído com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/supervisores-representante")
    @login_required
    def gerenciar_supervisores_representante():
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            flash("Acesso negado.", "danger")
            return redirect(url_for("index"))

        supervisores_repr = Usuario.query.filter_by(tipo="supervisor_repr").order_by(Usuario.nome.asc()).all()

        supervisores_data = []
        for sup in supervisores_repr:
            vinculos_ativos = SupervisorRepresentanteVinculo.query.filter_by(
                supervisor_id=sup.id, 
                ativo=True
            ).count()
            
            supervisores_data.append({
                "id": sup.id,
                "nome": sup.nome,
                "email": sup.email,
                "ativo": sup.ativo,
                "codigo_supervisor_tg650": sup.codigo_supervisor_tg650,
                "data_cadastro": sup.data_cadastro,
                "total_vinculos": vinculos_ativos,
            })

        return render_template("gerenciar_supervisores_representante.html", supervisores=supervisores_data)

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/vinculos")
    @login_required
    def listar_vinculos_supervisor_repr(supervisor_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        supervisor = db.session.get(Usuario, supervisor_id)
        if not supervisor or supervisor.tipo != "supervisor_repr":
            return jsonify({"ok": False, "mensagem": "Supervisor de representante não encontrado"}), 404

        vinculos = SupervisorRepresentanteVinculo.query.filter_by(supervisor_id=supervisor_id).all()

        vinculos_data = [{
            "id": v.id,
            "codigo_representante": v.codigo_representante,
            "nome_representante": v.nome_representante,
            "ativo": v.ativo,
            "sincronizado_tg650": v.sincronizado_tg650,
            "data_cadastro": v.data_cadastro.strftime("%d/%m/%Y %H:%M") if v.data_cadastro else None,
        } for v in vinculos]

        return jsonify({
            "ok": True,
            "supervisor": {
                "id": supervisor.id,
                "nome": supervisor.nome,
                "codigo_supervisor_tg650": supervisor.codigo_supervisor_tg650,
            },
            "vinculos": vinculos_data
        })

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/vinculos/adicionar", methods=["POST"])
    @login_required
    def adicionar_vinculo_supervisor_repr(supervisor_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            supervisor = db.session.get(Usuario, supervisor_id)
            if not supervisor or supervisor.tipo != "supervisor_repr":
                return jsonify({"ok": False, "mensagem": "Supervisor de representante não encontrado"}), 404

            payload = request.get_json(silent=True) or {}
            codigo_representante = s(payload.get("codigo_representante"))
            nome_representante = s(payload.get("nome_representante"))

            if not codigo_representante:
                return jsonify({"ok": False, "mensagem": "Código do representante é obrigatório"}), 400

            vinculo_existente = SupervisorRepresentanteVinculo.query.filter_by(
                supervisor_id=supervisor_id,
                codigo_representante=codigo_representante
            ).first()

            if vinculo_existente:
                if not vinculo_existente.ativo:
                    vinculo_existente.ativo = True
                    db.session.commit()
                    return jsonify({"ok": True, "mensagem": "Vínculo reativado com sucesso!"})
                return jsonify({"ok": False, "mensagem": "Vínculo já existe"}), 400

            novo_vinculo = SupervisorRepresentanteVinculo(
                supervisor_id=supervisor_id,
                codigo_representante=codigo_representante,
                nome_representante=nome_representante,
                ativo=True,
                sincronizado_tg650=False
            )

            db.session.add(novo_vinculo)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Vínculo adicionado com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/vinculos/<int:vinculo_id>/remover", methods=["POST"])
    @login_required
    def remover_vinculo_supervisor_repr(supervisor_id, vinculo_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            vinculo = db.session.get(SupervisorRepresentanteVinculo, vinculo_id)
            if not vinculo or vinculo.supervisor_id != supervisor_id:
                return jsonify({"ok": False, "mensagem": "Vínculo não encontrado"}), 404

            vinculo.ativo = False
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Vínculo removido com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/supervisor/supervisores-representante/<int:supervisor_id>/sincronizar-tg650", methods=["POST"])
    @login_required
    def sincronizar_vinculos_tg650(supervisor_id):
        if current_user.tipo != "supervisor" or not supervisor_dev_liberado():
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            supervisor = db.session.get(Usuario, supervisor_id)
            if not supervisor or supervisor.tipo != "supervisor_repr":
                return jsonify({"ok": False, "mensagem": "Supervisor de representante não encontrado"}), 404

            if not supervisor.codigo_supervisor_tg650:
                return jsonify({"ok": False, "mensagem": "Código TG650 não configurado para este supervisor"}), 400

            sync_result = _sincronizar_vinculos_tg650_supervisor_repr(supervisor_id, supervisor.codigo_supervisor_tg650)
            if not sync_result.get("ok"):
                return jsonify({"ok": False, "mensagem": sync_result.get("mensagem")}), 404

            return jsonify(sync_result)

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro ao sincronizar: {str(e)}"}), 500

    def _gerar_pdf_fechamento(payload: dict, dashboard_titulo: str) -> bytes:
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        except Exception as e:
            raise RuntimeError(
                "Biblioteca de PDF não instalada. Instale com: pip install reportlab"
            ) from e

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=12 * mm,
            rightMargin=12 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
        )
        styles = getSampleStyleSheet()
        normal = styles["Normal"]

        mes = int(payload.get("mes") or datetime.now().month)
        ano = int(payload.get("ano") or datetime.now().year)
        meses_nomes = {
            1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
            5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
            9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
        }
        periodo_txt = f"{meses_nomes.get(mes, str(mes))}/{ano}"
        consultores = payload.get("consultores") or []
        totais = payload.get("totais") or {}

        logo_path = os.path.join(app.root_path, "static", "img", "bakof-logo.png")
        logo_cell = ""
        if os.path.exists(logo_path):
            try:
                logo_cell = Image(logo_path, width=34 * mm, height=10 * mm)
            except Exception:
                logo_cell = ""

        titulo_html = (
            f"<b>Bakof CRM - Fechamento Mensal</b><br/>"
            f"<font size='10'>Setor: {dashboard_titulo} | Período: {periodo_txt}<br/>"
            f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} - "
            f"Operadores no relatório: {len(consultores)}</font>"
        )
        cabecalho = Table(
            [[logo_cell, Paragraph(titulo_html, normal)]],
            colWidths=[38 * mm, 220 * mm],
        )
        cabecalho.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, 0), "LEFT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef4ff")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ]
            )
        )

        story = [cabecalho, Spacer(1, 8)]

        resumo = [
            ["Ligações", str(int(totais.get("total_ligacoes") or 0))],
            ["Vendas", str(int(totais.get("total_vendas") or 0))],
            ["Retornar", str(int(totais.get("total_retornar") or 0))],
            ["ReativaÃ§Ãµes Rep.", str(int(totais.get("total_reativacoes") or 0))],
            ["Conversão", f"{totais.get('conversao') or 0}%"],
            ["Meta", f"{totais.get('meta_conversao') or 0}%"],
            ["Receita", str(totais.get("receita_fmt") or "R$ 0,00")],
            ["Receita Comprovada (Oracle)", str(totais.get("receita_comprovada_oracle_fmt") or "R$ 0,00")],
        ]
        tb_resumo = Table(resumo, colWidths=[65 * mm, 70 * mm])
        tb_resumo.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e2e8f0")),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.extend([tb_resumo, Spacer(1, 10)])

        fechamento_televendas = "televendas" in str(dashboard_titulo or "").strip().lower()
        cabecalho = [
            "Operador",
            "Ligações",
            "Vendas",
            "Retornar",
            "Reat. Rep.",
            "Conv.%",
            "Meta%",
            "Ativos",
            "90-150",
            "Próx.",
            "Receita",
            "Rec. Oracle",
        ]
        if fechamento_televendas:
            cabecalho[7] = "Construtoras"
            cabecalho.pop(8)
            cabecalho.pop(8)
        linhas = [cabecalho]
        for c in consultores:
            linhas.append(
                [
                    str(c.get("nome") or "-"),
                    str(int(c.get("total_ligacoes") or 0)),
                    str(int(c.get("vendas") or 0)),
                    str(int(c.get("total_retornar") or 0)),
                    str(int(c.get("reativacoes") or 0)),
                    f"{c.get('conversao') or 0}%",
                    f"{c.get('meta_conversao') or 0}%",
                    str(c.get("total_ativos_display") or c.get("total_ativos") or "Sem historico"),
                    str(c.get("total_90_150_display") or c.get("total_90_150") or "Sem histórico"),
                    str(
                        c.get("total_proximos_inativacao_display")
                        or c.get("total_proximos_inativacao")
                        or "Sem histórico"
                    ),
                    str(c.get("receita_fmt") or "R$ 0,00"),
                    str(c.get("receita_comprovada_oracle_fmt") or "R$ 0,00"),
                ]
            )
            if fechamento_televendas:
                linhas[-1][7] = str(c.get("total_construtoras_display") or "-")
                linhas[-1].pop(8)
                linhas[-1].pop(8)

        linhas.append(
            [
                "Total resultado do período",
                str(int(totais.get("total_ligacoes") or 0)),
                str(int(totais.get("total_vendas") or 0)),
                str(int(totais.get("total_retornar") or 0)),
                str(int(totais.get("total_reativacoes") or 0)),
                f"{totais.get('conversao') or 0}%",
                f"{totais.get('meta_conversao') or 0}%",
                str(totais.get("total_ativos_display") or totais.get("total_ativos") or "Sem historico"),
                str(totais.get("total_90_150_display") or totais.get("total_90_150") or "Sem histórico"),
                str(
                    totais.get("total_proximos_inativacao_display")
                    or totais.get("total_proximos_inativacao")
                    or "Sem histórico"
                ),
                str(totais.get("receita_fmt") or "R$ 0,00"),
                str(totais.get("receita_comprovada_oracle_fmt") or "R$ 0,00"),
            ]
        )

        if fechamento_televendas:
            linhas[-1][7] = str(totais.get("total_construtoras_display") or totais.get("total_construtoras") or "Sem historico")
            linhas[-1].pop(8)
            linhas[-1].pop(8)

        col_widths = [56 * mm, 17 * mm, 14 * mm, 16 * mm, 14 * mm, 15 * mm, 15 * mm, 15 * mm, 15 * mm, 15 * mm, 26 * mm, 30 * mm]
        if fechamento_televendas:
            col_widths.pop(8)
            col_widths.pop(8)
        tabela = Table(linhas, colWidths=col_widths, repeatRows=1)
        last_row = len(linhas) - 1
        tabela.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d4ed8")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                    ("ALIGN", (0, 1), (0, -1), "LEFT"),
                    ("ALIGN", (9, 1), (10, -1), "RIGHT"),
                    ("BACKGROUND", (0, 1), (-1, -2), colors.HexColor("#f8fafc")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.HexColor("#ffffff"), colors.HexColor("#f8fafc")]),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                    ("FONTNAME", (0, last_row), (-1, last_row), "Helvetica-Bold"),
                    ("BACKGROUND", (0, last_row), (-1, last_row), colors.HexColor("#e2e8f0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(tabela)

        doc.build(story)
        return buffer.getvalue()
