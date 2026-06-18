import threading
import time
from datetime import datetime, timedelta

from flask import render_template
from sqlalchemy import and_, or_
from sqlalchemy.orm import load_only, selectinload

from core.extensions import db
from core.models import Cliente, Ligacao, SyncResumoDiario, Usuario
from routes.clientes_ligacoes.client_metrics import carregar_stats_e_locks_por_cliente_id
from routes.clientes_ligacoes.continuidade_compra import (
    enriquecer_payloads_com_continuidade_compra,
    obter_total_meses_periodo,
)
from routes.clientes_ligacoes.listagem_client_payload import montar_payload_cliente_oracle
from routes.clientes_ligacoes.listagem_filters import (
    corresponde_conceito_filtro,
    corresponde_consultor_filtro,
    corresponde_termo_busca,
    extrair_filtros_listagem,
)
from routes.clientes_ligacoes.listagem_permissions import (
    consultor_categoria_permitido_para_usuario,
    representante_oracle_permitido_para_usuario,
)
from routes.clientes_ligacoes.listagem_base_filters import aplicar_filtro_carteira_especial_consultor
from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.domain_utils import (
    normalizar_conceito,
)
from routes.clientes_ligacoes.listagem_grouping_utils import (
    consolidar_dados_grupos,
    NOME_GRUPO_RECENCIA_LIVRE,
    ordenar_clientes_recencia_frequencia,
)
from routes.clientes_ligacoes.inativos_tab import carregar_clientes_inativos_enriquecidos
from routes.clientes_ligacoes.inativos_cnpj_raiz_filter import (
    carregar_cnpjs_raiz_ocultos_ativos,
    filtrar_clientes_inativos_por_cnpj_raiz_oculto,
)
from routes.clientes_ligacoes.representante_snapshot_readers import (
    carregar_clientes_inativos_snapshot_representante,
)
from routes.clientes_ligacoes.representante_projection_utils import (
    filtrar_projecao_agrupada,
)
from routes.clientes_ligacoes.local_client_dedup import escolher_melhor_cliente_por_codigo
from routes.clientes_ligacoes.pedido_andamento_helper import marcar_pedido_em_andamento_payloads
from routes.clientes_ligacoes.perf_logger import log_perf
from routes.clientes_ligacoes.dashboard_operacional import (
    montar_meses_disponiveis,
    montar_stats_consultor_televendas,
    parse_filtro_mes_ano,
)
from routes.clientes_ligacoes.televendas_stats import montar_stats_produtividade_televendas
from routes.clientes_ligacoes.proximos_totais import calcular_totais_abas_proximos
from services.representante_metricas_cache_service import (
    carregar_meses_compra_representante,
    carregar_pagamento_medio_representante,
)
from services.representante_projection_cache_service import (
    carregar_ou_gerar_projecao_representante,
)
from services.inativos_movimento_service import carregar_movimento_inativos

_INATIVOS_LOCAIS_CACHE = {}
_INATIVOS_LOCAIS_CACHE_TTL = 180
_INATIVOS_LOCAIS_CACHE_LOCK = threading.Lock()


def _log_perf(app, label, started_at, **extra):
    log_perf(app, "meus-clientes/inativos", label, started_at, **extra)


def _carregar_base_inativos_representante(
    *,
    app,
    current_user,
    codigos_representantes_vinculados,
    agrupar_por_ativo,
    periodo_recencia,
):
    perf_total = time.perf_counter()
    clientes_oracle_inativos = carregar_clientes_inativos_snapshot_representante()
    cnpjs_raiz_ocultos = carregar_cnpjs_raiz_ocultos_ativos()
    clientes_oracle_inativos = filtrar_clientes_inativos_por_cnpj_raiz_oculto(
        clientes_oracle_inativos,
        cnpjs_raiz_ocultos=cnpjs_raiz_ocultos,
    )
    codigos_inativos = [
        str(c.get("cd_cliente")).strip()
        for c in clientes_oracle_inativos
        if c.get("cd_cliente")
    ]
    pagamento_medio_por_cd = carregar_pagamento_medio_representante(codigos_inativos)
    mapa_meses_compra = carregar_meses_compra_representante(
        codigos_inativos,
        periodo=periodo_recencia,
    )
    meses_total_periodo = obter_total_meses_periodo(periodo_recencia)

    clientes_locais = (
        Cliente.query
        .options(
            load_only(
                Cliente.id,
                Cliente.ativo,
                Cliente.cd_cliente_oracle,
                Cliente.telefone,
                Cliente.telefone2,
                Cliente.consultor_id,
                Cliente.proxima_ligacao,
                Cliente.origem,
                Cliente.ultimo_pedido_oracle,
                Cliente.data_ultima_sincronizacao,
                Cliente.valor_total_365dias,
                Cliente.em_atendimento_por,
                Cliente.em_atendimento_ate,
            ),
            selectinload(Cliente.consultor).load_only(Usuario.id, Usuario.nome),
        )
        .filter(Cliente.cd_cliente_oracle.in_(codigos_inativos) if codigos_inativos else False)
        .all()
    )
    ids_locais = [c.id for c in clientes_locais if c.id]
    locks_por_cd_oracle = {}
    stats_ligacoes_por_cliente_id = {}
    locks_por_cliente_id = {}
    clientes_locais_por_cd = escolher_melhor_cliente_por_codigo(clientes_locais)
    if ids_locais:
        locks_rows = (
            db.session.query(
                Cliente.id.label("cliente_id"),
                Cliente.cd_cliente_oracle.label("cd_cliente_oracle"),
                Cliente.em_atendimento_ate.label("em_atendimento_ate"),
                Usuario.nome.label("usuario_nome"),
            )
            .outerjoin(Usuario, Usuario.id == Cliente.em_atendimento_por)
            .filter(
                Cliente.id.in_(ids_locais),
                Cliente.em_atendimento_por.isnot(None),
            )
            .all()
        )
        locks_por_cliente_id, stats_ligacoes_por_cliente_id = carregar_stats_e_locks_por_cliente_id(ids_locais)
        clientes_locais_por_cd = escolher_melhor_cliente_por_codigo(
            clientes_locais,
            stats_ligacoes_por_cliente_id,
        )
        for row in locks_rows:
            cd_lock = str(row.cd_cliente_oracle or "").strip()
            if not cd_lock or not row.em_atendimento_ate or row.em_atendimento_ate <= datetime.now():
                continue
            if cd_lock not in locks_por_cd_oracle:
                locks_por_cd_oracle[cd_lock] = {
                    "ativo": True,
                    "por_nome": (row.usuario_nome or "Outro usuario"),
                    "ate": row.em_atendimento_ate.strftime("%d/%m/%Y %H:%M"),
                }

    representantes_data = {}
    for cliente_oracle in clientes_oracle_inativos:
        cd_cliente = str(cliente_oracle.get("cd_cliente") or "").strip()
        cliente_local = clientes_locais_por_cd.get(cd_cliente) if cd_cliente else None
        if not representante_oracle_permitido_para_usuario(
            tipo_usuario=current_user.tipo,
            representante_texto=str(cliente_oracle.get("representante") or ""),
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        ):
            continue

        stats_lig = (
            stats_ligacoes_por_cliente_id.get(cliente_local.id, {})
            if cliente_local and cliente_local.id else {}
        )
        lock_info = locks_por_cd_oracle.get(cd_cliente, {})
        if (not lock_info) and cliente_local and cliente_local.id:
            lock_info = locks_por_cliente_id.get(cliente_local.id, {})
        meses_com_compra = len({str(m) for m in (mapa_meses_compra.get(cd_cliente) or []) if m not in (None, "")})
        consultor_cliente = str(cliente_oracle.get("consultor") or "").strip()
        conceito_cliente = normalizar_conceito(cliente_oracle.get("conceito"))

        if agrupar_por_ativo == "representante":
            nome_grupo = str(cliente_oracle.get("representante") or "").strip() or "SEM REPRESENTANTE"
        elif agrupar_por_ativo == "consultor":
            nome_grupo = consultor_cliente or (cliente_local.consultor.nome if (cliente_local and cliente_local.consultor) else "") or "SEM CONSULTOR"
        elif agrupar_por_ativo == "recencia":
            nome_grupo = NOME_GRUPO_RECENCIA_LIVRE
        else:
            nome_grupo = str(cliente_oracle.get("uf") or "").strip().upper() or "SEM UF"

        if nome_grupo not in representantes_data:
            representantes_data[nome_grupo] = {
                "nome": nome_grupo,
                "clientes": [],
                "total_clientes": 0,
                "liberados": 0,
                "inadimplentes": 0,
                "sem_conceito": 0,
                "ticket_medio": 0,
                "dias_medio": 0,
                "consultores_internos": {},
            }

        cliente_oracle_enriquecido = dict(cliente_oracle or {})
        cliente_oracle_enriquecido["pagamento_medio_dias"] = pagamento_medio_por_cd.get(cd_cliente)
        cliente_oracle_enriquecido["meses_com_compra"] = meses_com_compra
        cliente_oracle_enriquecido["meses_total_periodo"] = meses_total_periodo
        dados_cliente = montar_payload_cliente_oracle(
            cliente_oracle=cliente_oracle_enriquecido,
            cliente_local=cliente_local,
            stats_lig=stats_lig,
            lock_info=lock_info,
            conceito=conceito_cliente,
            origem_padrao="oracle_inativos",
        )
        representantes_data[nome_grupo]["clientes"].append(dados_cliente)
        if consultor_cliente:
            reps = representantes_data[nome_grupo]["consultores_internos"]
            reps[consultor_cliente] = reps.get(consultor_cliente, 0) + 1

    representantes_ordenados, consultores_inativos, total_inativos, stats_inativos = consolidar_dados_grupos(
        representantes_data=representantes_data,
        chave_sem_grupo=(
            "SEM REPRESENTANTE"
            if agrupar_por_ativo == "representante"
            else ("SEM CONSULTOR" if agrupar_por_ativo == "consultor" else ("SEM UF" if agrupar_por_ativo == "uf" else ""))
        ),
        conceitos_sem_conceito=("", "SEM CONCEITO", None),
    )
    enriquecer_payloads_com_continuidade_compra(
        [cliente for _, dados in representantes_ordenados for cliente in dados.get("clientes", [])],
        periodo=periodo_recencia,
        mapa_meses=mapa_meses_compra,
    )
    if agrupar_por_ativo == "recencia":
        representantes_ordenados = [
            (
                nome_grupo,
                {
                    **dados_grupo,
                    "clientes": ordenar_clientes_recencia_frequencia(dados_grupo.get("clientes", [])),
                },
            )
            for nome_grupo, dados_grupo in representantes_ordenados
        ]
    _log_perf(app, "projecao_representante_build", perf_total, total=total_inativos)
    return {
        "representantes_ordenados": representantes_ordenados,
        "consultores": consultores_inativos,
        "total": total_inativos,
        "stats": stats_inativos,
    }


def render_aba_inativos(
    *,
    app,
    aba: str,
    request,
    current_user,
    codigos_representantes_vinculados,
    apenas_meus: bool,
    total_oracle_badge: int,
    total_ativos_badge: int,
    total_inativos_badge: int,
    total_proximos_badge: int,
    cache_store: dict,
    total_construtoras_badge: int = 0,
    total_retornos_atrasados_badge: int = 0,
    dashboard_tipo=None,
    visao=None,
    agrupar_por="uf",
    periodo_recencia="ano_atual",
    lazy_grupo_nome=None,
    lazy_offset=0,
    lazy_limit=150,
):
    # REGRA VALIDADA (2026-03): lista de inativos vem da base local sincronizada diariamente.
    perf_total = time.perf_counter()
    app.logger.info("=== INICIANDO TRATAMENTO ABA INATIVOS ===")
    app.logger.info(f"Usuario: {current_user.nome} ({current_user.tipo})")
    agrupar_por_ativo = agrupar_por if agrupar_por in ("representante", "uf", "consultor", "recencia") else "uf"
    conceito_filtro, consultor_filtro, termo = extrair_filtros_listagem(request)
    if current_user.tipo == "representante":
        perf_step = time.perf_counter()
        projecao = carregar_ou_gerar_projecao_representante(
            codigo_representante=str(current_user.codigo_representante or ""),
            carteira="inativos",
            agrupar_por=agrupar_por_ativo,
            periodo_recencia=periodo_recencia,
            gerador=lambda: _carregar_base_inativos_representante(
                app=app,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                agrupar_por_ativo=agrupar_por_ativo,
                periodo_recencia=periodo_recencia,
            ),
        )
        _log_perf(app, "projecao_representante_cache", perf_step, grupos=len(projecao.get("representantes_ordenados") or []))
        representantes_ordenados, consultores_inativos, total_inativos, stats_inativos = filtrar_projecao_agrupada(
            projecao.get("representantes_ordenados") or [],
            conceito_filtro=conceito_filtro,
            consultor_filtro=consultor_filtro,
            termo=termo,
            campos_busca=(
                "nome",
                "cnpj",
                "telefone",
                "telefone2",
                "representante_nome",
                "categoria_consultor",
                "conceito",
                "municipio",
                "uf",
                "contato",
                "cd_cliente_oracle",
            ),
            chave_sem_grupo=(
                "SEM REPRESENTANTE"
                if agrupar_por_ativo == "representante"
                else ("SEM CONSULTOR" if agrupar_por_ativo == "consultor" else ("SEM UF" if agrupar_por_ativo == "uf" else ""))
            ),
            conceitos_sem_conceito=("", "SEM CONCEITO", None),
        )
        if lazy_grupo_nome:
            grupo_nome = str(lazy_grupo_nome or "").strip()
            for nome_grupo, dados_grupo in representantes_ordenados:
                if str(nome_grupo or "").strip() != grupo_nome:
                    continue
                clientes_grupo = dados_grupo.get("clientes") or []
                try:
                    offset = max(0, int(lazy_offset or 0))
                except (TypeError, ValueError):
                    offset = 0
                try:
                    limit = int(lazy_limit or 150)
                except (TypeError, ValueError):
                    limit = 150
                limit = min(max(limit, 50), 500)
                clientes_pagina = clientes_grupo[offset:offset + limit]
                next_offset = offset + len(clientes_pagina)
                marcar_pedido_em_andamento_payloads(clientes_pagina)
                return render_template(
                    "meus_clientes/_lista_agrupada.html",
                    representantes=[(nome_grupo, {**dados_grupo, "clientes": clientes_pagina})],
                    usar_lazy_grupos=False,
                    usar_vista_agrupada=True,
                    aba=aba,
                    is_supervisor=current_user.tipo == "supervisor",
                    now=datetime.now,
                    dashboard_tipo=dashboard_tipo,
                    visao=visao,
                    agrupar_por=agrupar_por_ativo,
                    ano_recencia=datetime.now().year,
                    periodo_recencia=periodo_recencia,
                    lazy_next_offset=next_offset,
                    lazy_has_more=next_offset < len(clientes_grupo),
                    lazy_total=len(clientes_grupo),
                )
            return ""

        total_pendentes, total_retornar = calcular_totais_abas_proximos(
            current_user,
            codigos_representantes_vinculados,
        )
        resumo_sync_hoje = SyncResumoDiario.query.filter_by(data_ref=datetime.now().date()).first()
        movimento_inativos_hoje = {
            "entraram": int(resumo_sync_hoje.inativos_entraram) if resumo_sync_hoje else 0,
            "sairam": int(resumo_sync_hoje.inativos_sairam) if resumo_sync_hoje else 0,
            "total": int(resumo_sync_hoje.total_inativos) if resumo_sync_hoje else int(total_inativos),
            "atualizado_em": (resumo_sync_hoje.atualizado_em if resumo_sync_hoje else None),
        }
        movimento_inativos_detalhes = carregar_movimento_inativos(datetime.now().date()) or {}
        response = render_template(
            "meus_clientes.html",
            representantes=representantes_ordenados,
            aba=aba,
            total_pendentes=total_pendentes,
            total_retornar=total_retornar,
            total_oracle=total_oracle_badge,
            total_ativos=total_ativos_badge,
            total_inativos=total_inativos_badge,
            total_proximos=total_proximos_badge,
            total_construtoras=total_construtoras_badge,
            total_retornos_atrasados=total_retornos_atrasados_badge,
            usar_vista_agrupada=True,
            is_supervisor=current_user.tipo == "supervisor",
            stats={},
            stats_inativos=stats_inativos,
            movimento_inativos_hoje=movimento_inativos_hoje,
            movimento_inativos_detalhes=movimento_inativos_detalhes,
            stats_televendas={},
            consultores_inativos=consultores_inativos,
            q=request.args.get("q", ""),
            meses_disponiveis_consultor=[],
            mes_filtro=None,
            ano_filtro=None,
            ano_recencia=datetime.now().year,
            periodo_recencia=periodo_recencia,
            dashboard_tipo=dashboard_tipo,
            visao=visao,
            agrupar_por=agrupar_por_ativo,
            usar_lazy_grupos=True,
        )
        _log_perf(app, "total", perf_total, total=total_inativos)
        return response

    perf_step = time.perf_counter()
    if current_user.tipo == "representante":
        clientes_oracle_inativos = carregar_clientes_inativos_snapshot_representante()
    else:
        clientes_oracle_inativos = carregar_clientes_inativos_enriquecidos(app.logger)
    _log_perf(app, "carregar_inativos_enriquecidos", perf_step, total=len(clientes_oracle_inativos or []))
    perf_step = time.perf_counter()
    cnpjs_raiz_ocultos = carregar_cnpjs_raiz_ocultos_ativos()
    clientes_oracle_inativos = filtrar_clientes_inativos_por_cnpj_raiz_oculto(
        clientes_oracle_inativos,
        cnpjs_raiz_ocultos=cnpjs_raiz_ocultos,
    )
    _log_perf(
        app,
        "filtrar_cnpj_raiz_oculto",
        perf_step,
        total=len(clientes_oracle_inativos or []),
        ocultos=len(cnpjs_raiz_ocultos or []),
    )
    # Lista Publica: consultores veem todos os clientes inativos sem filtro por categoria.
    filtrar_inativos_por_categoria = False
    mapa_nome_para_id_inativos = {}
    mapa_codigo_para_id_inativos = {}
    if filtrar_inativos_por_categoria:
        _, mapa_nome_para_id_inativos = carregar_mapa_nome_para_id_usuarios_ativos()
        mapa_codigo_para_id_inativos = construir_mapa_codigo_para_id(mapa_nome_para_id_inativos)

    conceito_filtro, consultor_filtro, termo = extrair_filtros_listagem(request)

    codigos_inativos = [
        str(c.get("cd_cliente")).strip()
        for c in clientes_oracle_inativos
        if c.get("cd_cliente")
    ]
    perf_step = time.perf_counter()
    pagamento_medio_por_cd = carregar_pagamento_medio_representante(codigos_inativos) if codigos_inativos else {}
    _log_perf(app, "pagamento_medio", perf_step, codigos=len(codigos_inativos))
    perf_step = time.perf_counter()
    mapa_meses_compra = (
        carregar_meses_compra_representante(codigos_inativos, periodo=periodo_recencia)
        if codigos_inativos else {}
    )
    _log_perf(app, "meses_compra", perf_step, codigos=len(codigos_inativos))
    meses_total_periodo = obter_total_meses_periodo(periodo_recencia)
    filtrar_por_vinculo_dashboard = (dashboard_tipo == "consultor")
    operadores_ids_tipo = set()
    if filtrar_por_vinculo_dashboard:
        operadores_ids_tipo = {
            int(uid)
            for (uid,) in (
                db.session.query(Usuario.id)
                .filter(Usuario.tipo == dashboard_tipo, Usuario.ativo == True)
                .all()
            )
            if uid
        }

    clientes_locais_por_cd = {}
    stats_ligacoes_por_cliente_id = {}
    locks_por_cliente_id = {}
    locks_por_cd_oracle = {}
    if codigos_inativos:
        perf_step = time.perf_counter()
        _ck = (frozenset(codigos_inativos), frozenset(operadores_ids_tipo))
        _ci = _INATIVOS_LOCAIS_CACHE.get(_ck)
        if _ci and (time.perf_counter() - _ci["ts"]) <= _INATIVOS_LOCAIS_CACHE_TTL:
            clientes_locais = _ci["data"]
        else:
            clientes_locais = (
                Cliente.query
                .options(
                    load_only(
                        Cliente.id,
                        Cliente.ativo,
                        Cliente.cd_cliente_oracle,
                        Cliente.telefone,
                        Cliente.telefone2,
                        Cliente.consultor_id,
                        Cliente.proxima_ligacao,
                        Cliente.origem,
                        Cliente.ultimo_pedido_oracle,
                        Cliente.data_ultima_sincronizacao,
                        Cliente.valor_total_365dias,
                        Cliente.em_atendimento_por,
                        Cliente.em_atendimento_ate,
                    ),
                    selectinload(Cliente.consultor).load_only(Usuario.id, Usuario.nome),
                )
                .filter(
                    Cliente.cd_cliente_oracle.in_(codigos_inativos),
                )
                .filter(Cliente.consultor_id.in_(operadores_ids_tipo) if filtrar_por_vinculo_dashboard else True)
                .all()
            )
            with _INATIVOS_LOCAIS_CACHE_LOCK:
                _INATIVOS_LOCAIS_CACHE[_ck] = {"ts": time.perf_counter(), "data": clientes_locais}
                if len(_INATIVOS_LOCAIS_CACHE) > 8:
                    itens = sorted(_INATIVOS_LOCAIS_CACHE.items(), key=lambda x: x[1]["ts"])
                    _INATIVOS_LOCAIS_CACHE.clear()
                    _INATIVOS_LOCAIS_CACHE.update(dict(itens[-6:]))
        _log_perf(app, "clientes_locais", perf_step, total=len(clientes_locais or []))
        ids_locais = [c.id for c in clientes_locais if c.id]
        if ids_locais:
            perf_step = time.perf_counter()
            locks_rows = (
                db.session.query(
                    Cliente.id.label("cliente_id"),
                    Cliente.cd_cliente_oracle.label("cd_cliente_oracle"),
                    Cliente.em_atendimento_ate.label("em_atendimento_ate"),
                    Usuario.nome.label("usuario_nome"),
                )
                .outerjoin(Usuario, Usuario.id == Cliente.em_atendimento_por)
                .filter(
                    Cliente.id.in_(ids_locais),
                    Cliente.em_atendimento_por.isnot(None),
                )
                .all()
            )
            locks_por_cliente_id, stats_ligacoes_por_cliente_id = carregar_stats_e_locks_por_cliente_id(
                ids_locais
            )
            clientes_locais_por_cd = escolher_melhor_cliente_por_codigo(
                clientes_locais,
                stats_ligacoes_por_cliente_id,
            )
            for row in locks_rows:
                cd_lock = str(row.cd_cliente_oracle or "").strip()
                if not cd_lock:
                    continue
                if not row.em_atendimento_ate or row.em_atendimento_ate <= datetime.now():
                    continue
                if cd_lock not in locks_por_cd_oracle:
                    locks_por_cd_oracle[cd_lock] = {
                        "ativo": True,
                        "por_nome": (row.usuario_nome or "Outro usuario"),
                        "ate": row.em_atendimento_ate.strftime("%d/%m/%Y %H:%M"),
                    }
            _log_perf(app, "stats_locks", perf_step, ids=len(ids_locais))
        else:
            clientes_locais_por_cd = escolher_melhor_cliente_por_codigo(clientes_locais)

    agrupar_por_ativo = agrupar_por if agrupar_por in ("representante", "uf", "consultor", "recencia") else "uf"
    representantes_data = {}
    perf_step = time.perf_counter()
    for cliente_oracle in clientes_oracle_inativos:
        conceito_cliente = normalizar_conceito(cliente_oracle.get("conceito"))
        consultor_cliente = str(cliente_oracle.get("consultor") or "").strip()

        if not corresponde_conceito_filtro(conceito_filtro, conceito_cliente):
            continue

        if not corresponde_consultor_filtro(consultor_filtro, consultor_cliente):
            continue
        if filtrar_inativos_por_categoria and not consultor_categoria_permitido_para_usuario(
            tipo_usuario=current_user.tipo,
            consultor_cliente=consultor_cliente,
            current_user_id=current_user.id,
            mapa_codigo_para_id=mapa_codigo_para_id_inativos,
            mapa_nome_para_id=mapa_nome_para_id_inativos,
        ):
            continue

        if not corresponde_termo_busca(
            termo,
            cliente_oracle,
            (
                "cliente",
                "cnpj",
                "telefone1",
                "telefone2",
                "representante",
                "consultor",
                "conceito",
                "municipio",
                "uf",
            ),
        ):
            continue

        cd_cliente = str(cliente_oracle.get("cd_cliente") or "").strip()
        cliente_local = clientes_locais_por_cd.get(cd_cliente) if cd_cliente else None
        if filtrar_por_vinculo_dashboard and not cliente_local:
            continue

        if not representante_oracle_permitido_para_usuario(
            tipo_usuario=current_user.tipo,
            representante_texto=str(cliente_oracle.get("representante") or ""),
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        ):
            continue

        stats_lig = (
            stats_ligacoes_por_cliente_id.get(cliente_local.id, {})
            if cliente_local and cliente_local.id else {}
        )
        lock_info = {}
        if cd_cliente:
            lock_info = locks_por_cd_oracle.get(cd_cliente, {})
        if (not lock_info) and cliente_local and cliente_local.id:
            lock_info = locks_por_cliente_id.get(cliente_local.id, {})
        meses_com_compra = len({str(m) for m in (mapa_meses_compra.get(cd_cliente) or []) if m not in (None, "")})
        if agrupar_por_ativo == "representante":
            nome_grupo = (
                str(cliente_oracle.get("representante") or "").strip() or "SEM REPRESENTANTE"
            )
        elif agrupar_por_ativo == "consultor":
            nome_grupo = (
                str(cliente_oracle.get("consultor") or "").strip()
                or (cliente_local.consultor.nome if (cliente_local and cliente_local.consultor) else "")
                or "SEM CONSULTOR"
            )
        elif agrupar_por_ativo == "recencia":
            nome_grupo = NOME_GRUPO_RECENCIA_LIVRE
        else:
            nome_grupo = str(cliente_oracle.get("uf") or "").strip().upper() or "SEM UF"

        if nome_grupo not in representantes_data:
            representantes_data[nome_grupo] = {
                "nome": nome_grupo,
                "clientes": [],
                "total_clientes": 0,
                "liberados": 0,
                "inadimplentes": 0,
                "sem_conceito": 0,
                "ticket_medio": 0,
                "dias_medio": 0,
                "consultores_internos": {},
            }
        cliente_oracle_enriquecido = dict(cliente_oracle or {})
        cliente_oracle_enriquecido["pagamento_medio_dias"] = pagamento_medio_por_cd.get(cd_cliente)
        cliente_oracle_enriquecido["meses_com_compra"] = meses_com_compra
        cliente_oracle_enriquecido["meses_total_periodo"] = meses_total_periodo

        dados_cliente = montar_payload_cliente_oracle(
            cliente_oracle=cliente_oracle_enriquecido,
            cliente_local=cliente_local,
            stats_lig=stats_lig,
            lock_info=lock_info,
            conceito=conceito_cliente,
            origem_padrao="oracle_inativos",
        )

        representantes_data[nome_grupo]["clientes"].append(dados_cliente)
        if consultor_cliente:
            consultores_uf = representantes_data[nome_grupo]["consultores_internos"]
            consultores_uf[consultor_cliente] = consultores_uf.get(consultor_cliente, 0) + 1
    _log_perf(app, "montar_payloads", perf_step, grupos=len(representantes_data))

    perf_step = time.perf_counter()
    representantes_ordenados, consultores_inativos, total_inativos, stats_inativos = consolidar_dados_grupos(
        representantes_data=representantes_data,
        chave_sem_grupo=(
            "SEM REPRESENTANTE"
            if agrupar_por_ativo == "representante"
            else ("SEM CONSULTOR" if agrupar_por_ativo == "consultor" else ("SEM UF" if agrupar_por_ativo == "uf" else ""))
        ),
        conceitos_sem_conceito=("", "SEM CONCEITO", None),
    )
    _log_perf(app, "consolidar_grupos", perf_step, total=total_inativos)
    perf_step = time.perf_counter()
    periodo_continuidade = periodo_recencia
    mapa_meses_continuidade = (
        mapa_meses_compra
        if periodo_continuidade == periodo_recencia
        else None
    )
    enriquecer_payloads_com_continuidade_compra(
        [cliente for _, dados in representantes_ordenados for cliente in dados.get("clientes", [])],
        periodo=periodo_continuidade,
        mapa_meses=mapa_meses_continuidade,
    )
    _log_perf(app, "continuidade_compra", perf_step, total=total_inativos)
    if agrupar_por_ativo == "recencia":
        representantes_ordenados = [
            (
                nome_grupo,
                {
                    **dados_grupo,
                    "clientes": ordenar_clientes_recencia_frequencia(dados_grupo.get("clientes", [])),
                },
            )
            for nome_grupo, dados_grupo in representantes_ordenados
        ]
    if lazy_grupo_nome:
        grupo_nome = str(lazy_grupo_nome or "").strip()
        for nome_grupo, dados_grupo in representantes_ordenados:
            if str(nome_grupo or "").strip() != grupo_nome:
                continue
            clientes_grupo = dados_grupo.get("clientes") or []
            try:
                offset = max(0, int(lazy_offset or 0))
            except (TypeError, ValueError):
                offset = 0
            try:
                limit = int(lazy_limit or 150)
            except (TypeError, ValueError):
                limit = 150
            limit = min(max(limit, 50), 500)
            clientes_pagina = clientes_grupo[offset:offset + limit]
            next_offset = offset + len(clientes_pagina)
            perf_step = time.perf_counter()
            marcar_pedido_em_andamento_payloads(clientes_pagina)
            _log_perf(app, "pedido_em_andamento", perf_step, total=len(clientes_pagina), lazy=True)
            return render_template(
                "meus_clientes/_lista_agrupada.html",
                representantes=[(nome_grupo, {**dados_grupo, "clientes": clientes_pagina})],
                usar_lazy_grupos=False,
                usar_vista_agrupada=True,
                aba=aba,
                is_supervisor=current_user.tipo == "supervisor",
                now=datetime.now,
                dashboard_tipo=dashboard_tipo,
                visao=visao,
                agrupar_por=agrupar_por_ativo,
                ano_recencia=datetime.now().year,
                periodo_recencia=periodo_recencia,
                lazy_next_offset=next_offset,
                lazy_has_more=next_offset < len(clientes_grupo),
                lazy_total=len(clientes_grupo),
            )
        return ""
    _log_perf(app, "pedido_em_andamento", time.perf_counter(), total=0, skipped_lazy=True)
    if not filtrar_por_vinculo_dashboard:
        cache_store[current_user.id] = {
            "count": total_inativos,
            "ts": datetime.now(),
        }

    resumo_sync_hoje = SyncResumoDiario.query.filter_by(data_ref=datetime.now().date()).first()
    movimento_inativos_hoje = {
        "entraram": int(resumo_sync_hoje.inativos_entraram) if resumo_sync_hoje else 0,
        "sairam": int(resumo_sync_hoje.inativos_sairam) if resumo_sync_hoje else 0,
        "total": int(resumo_sync_hoje.total_inativos) if resumo_sync_hoje else int(total_inativos),
        "atualizado_em": (resumo_sync_hoje.atualizado_em if resumo_sync_hoje else None),
    }
    movimento_inativos_detalhes = carregar_movimento_inativos(datetime.now().date()) or {}

    total_pendentes = 0
    total_retornar = 0
    perf_step = time.perf_counter()
    if current_user.tipo == "televendas":
        clientes_ligados_por_tv = (
            db.session.query(Ligacao.cliente_id)
            .filter(Ligacao.consultor_id == current_user.id)
            .distinct()
        )
        base_tv = Cliente.query.filter(
            Cliente.ativo == True
        ).filter(
            or_(
                Cliente.consultor_id == current_user.id,
                Cliente.id.in_(clientes_ligados_por_tv),
            )
        )
        total_retornar = base_tv.filter(Cliente.proxima_ligacao.isnot(None)).count()
    elif current_user.tipo in ("supervisor_repr", "representante"):
        total_pendentes, total_retornar = calcular_totais_abas_proximos(
            current_user,
            codigos_representantes_vinculados,
        )
    else:
        todos_clientes = Cliente.query.filter_by(ativo=True)
        todos_clientes = aplicar_filtro_carteira_especial_consultor(todos_clientes, current_user)
        if filtrar_por_vinculo_dashboard:
            todos_clientes = todos_clientes.filter(Cliente.consultor_id.in_(operadores_ids_tipo))
        if apenas_meus:
            todos_clientes = todos_clientes.filter(Cliente.consultor_id == current_user.id)

        base_pendentes = todos_clientes.filter(
            Cliente.id.notin_(
                db.session.query(Ligacao.cliente_id).filter(
                    Ligacao.consultor_id == current_user.id if apenas_meus else True
                )
            )
        )
        if current_user.tipo == "consultor":
            # Mantem "Clientes Especiais" consistente em todas as abas:
            # para consultor, remove da contagem operacional a campanha 90-150d.
            limite_min_90_150 = datetime.now() - timedelta(days=150)
            limite_max_90_150 = datetime.now() - timedelta(days=90)
            base_pendentes = base_pendentes.filter(
                ~and_(
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_min_90_150, limite_max_90_150),
                )
            )
        total_pendentes = base_pendentes.count()

        total_retornar = todos_clientes.filter(Cliente.proxima_ligacao.isnot(None)).count()
    _log_perf(
        app,
        "totais_operacionais",
        perf_step,
        pendentes=total_pendentes,
        retornar=total_retornar,
    )

    perf_step = time.perf_counter()
    stats_televendas = montar_stats_produtividade_televendas()
    mes_filtro, ano_filtro = parse_filtro_mes_ano(request.args, current_user.tipo)
    stats_dashboard = {}
    meses_disponiveis_consultor = []
    if current_user.tipo == "televendas":
        stats_dashboard = montar_stats_consultor_televendas(
            current_user,
            total_oracle_badge,
            total_ativos_badge,
        )
        meses_disponiveis_consultor = montar_meses_disponiveis(current_user.tipo)
    total_inativos_exibido = total_inativos if filtrar_por_vinculo_dashboard else total_inativos_badge
    _log_perf(app, "stats_dashboard", perf_step, tipo=current_user.tipo)

    perf_step = time.perf_counter()
    response = render_template(
        "meus_clientes.html",
        representantes=representantes_ordenados,
        aba=aba,
        total_pendentes=total_pendentes,
        total_retornar=total_retornar,
        total_oracle=total_oracle_badge,
        total_ativos=total_ativos_badge,
        total_inativos=total_inativos_exibido,
        total_proximos=total_proximos_badge,
        total_construtoras=total_construtoras_badge,
        total_retornos_atrasados=total_retornos_atrasados_badge,
        usar_vista_agrupada=True,
        is_supervisor=current_user.tipo == "supervisor",
        stats=stats_dashboard,
        stats_inativos=stats_inativos,
        movimento_inativos_hoje=movimento_inativos_hoje,
        movimento_inativos_detalhes=movimento_inativos_detalhes,
        stats_televendas=stats_televendas,
        consultores_inativos=consultores_inativos,
        q=request.args.get("q", ""),
        meses_disponiveis_consultor=meses_disponiveis_consultor,
        mes_filtro=mes_filtro,
        ano_filtro=ano_filtro,
        ano_recencia=datetime.now().year,
        periodo_recencia=periodo_recencia,
        dashboard_tipo=dashboard_tipo,
        visao=visao,
        agrupar_por=agrupar_por_ativo,
        usar_lazy_grupos=True,
    )
    _log_perf(app, "template", perf_step, total=total_inativos)
    _log_perf(app, "total", perf_total, total=total_inativos)
    return response
