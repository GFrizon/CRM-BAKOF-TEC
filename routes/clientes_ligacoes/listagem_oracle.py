import time
from datetime import datetime, timedelta

from flask import render_template
from sqlalchemy import and_
from sqlalchemy.orm import load_only, selectinload

from core.extensions import db
from core.models import Cliente, Ligacao, Usuario
from routes.clientes_ligacoes.client_metrics import carregar_stats_e_locks_por_cliente_id
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
from routes.clientes_ligacoes.continuidade_compra import (
    enriquecer_payloads_com_continuidade_compra,
    obter_total_meses_periodo,
)
from routes.clientes_ligacoes.listagem_grouping_utils import (
    consolidar_dados_grupos,
    NOME_GRUPO_RECENCIA_LIVRE,
    ordenar_clientes_recencia_frequencia,
)
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados
from routes.clientes_ligacoes.representante_snapshot_readers import (
    carregar_clientes_oracle_snapshot_representante,
)
from routes.clientes_ligacoes.representante_projection_utils import (
    filtrar_projecao_agrupada,
)
from routes.clientes_ligacoes.local_client_dedup import escolher_melhor_cliente_por_codigo
from routes.clientes_ligacoes.pedido_andamento_helper import marcar_pedido_em_andamento_payloads
from routes.clientes_ligacoes.perf_logger import log_perf
from routes.clientes_ligacoes.proximos_totais import calcular_totais_abas_proximos
from services.representante_projection_cache_service import (
    carregar_ou_gerar_projecao_representante,
)
from services.representante_metricas_cache_service import (
    carregar_meses_compra_representante,
    carregar_pagamento_medio_representante,
)
def _log_perf(app, label, started_at, **extra):
    log_perf(app, "meus-clientes/oracle", label, started_at, **extra)


def _carregar_base_oracle_representante(
    *,
    app,
    current_user,
    codigos_representantes_vinculados,
    agrupar_por_ativo,
    periodo_recencia,
):
    perf_total = time.perf_counter()
    clientes_oracle = carregar_clientes_oracle_snapshot_representante()
    codigos_oracle = [
        str(c.get("cd_cliente")).strip()
        for c in clientes_oracle
        if c.get("cd_cliente")
    ]
    pagamento_medio_por_cd = carregar_pagamento_medio_representante(codigos_oracle)
    mapa_meses_compra = carregar_meses_compra_representante(
        codigos_oracle,
        periodo=periodo_recencia,
    )
    meses_total_periodo = obter_total_meses_periodo(periodo_recencia)

    clientes_locais_por_cd = {}
    stats_ligacoes_por_cliente_id = {}
    locks_por_cliente_id = {}
    if codigos_oracle:
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
            .filter(Cliente.cd_cliente_oracle.in_(codigos_oracle))
            .all()
        )
        ids_locais = [c.id for c in clientes_locais if c.id]
        if ids_locais:
            locks_por_cliente_id, stats_ligacoes_por_cliente_id = carregar_stats_e_locks_por_cliente_id(
                ids_locais
            )
        clientes_locais_por_cd = escolher_melhor_cliente_por_codigo(
            clientes_locais,
            stats_ligacoes_por_cliente_id,
        )

    representantes_data = {}
    for cliente_oracle in clientes_oracle:
        if not representante_oracle_permitido_para_usuario(
            tipo_usuario=current_user.tipo,
            representante_texto=str(cliente_oracle.get("representante") or ""),
            codigos_representantes_vinculados=codigos_representantes_vinculados,
        ):
            continue

        cd_cliente = str(cliente_oracle.get("cd_cliente") or "").strip()
        cliente_local = clientes_locais_por_cd.get(cd_cliente) if cd_cliente else None
        stats_lig = (
            stats_ligacoes_por_cliente_id.get(cliente_local.id, {})
            if cliente_local and cliente_local.id else {}
        )
        lock_info = (
            locks_por_cliente_id.get(cliente_local.id, {})
            if cliente_local and cliente_local.id else {}
        )
        meses_com_compra = len({
            str(m)
            for m in (mapa_meses_compra.get(cd_cliente) or [])
            if m not in (None, "")
        })

        if agrupar_por_ativo == "uf":
            nome_grupo = str(cliente_oracle.get("uf") or "").strip().upper() or "SEM UF"
        elif agrupar_por_ativo == "consultor":
            nome_grupo = (
                str(cliente_oracle.get("consultor") or "").strip()
                or (cliente_local.consultor.nome if (cliente_local and cliente_local.consultor) else "")
                or "SEM CONSULTOR"
            )
        elif agrupar_por_ativo == "recencia":
            nome_grupo = NOME_GRUPO_RECENCIA_LIVRE
        else:
            nome_grupo = str(cliente_oracle.get("representante") or "").strip() or "SEM REPRESENTANTE"

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
            conceito=cliente_oracle.get("conceito", ""),
            origem_padrao="oracle",
        )
        representantes_data[nome_grupo]["clientes"].append(dados_cliente)

        if cliente_local and cliente_local.consultor:
            nome_consultor = cliente_local.consultor.nome
            reps = representantes_data[nome_grupo]["consultores_internos"]
            reps[nome_consultor] = reps.get(nome_consultor, 0) + 1

    representantes_ordenados, consultores_oracle, total_oracle, stats_oracle = consolidar_dados_grupos(
        representantes_data=representantes_data,
        chave_sem_grupo=(
            "SEM UF"
            if agrupar_por_ativo == "uf"
            else ("SEM CONSULTOR" if agrupar_por_ativo == "consultor" else "")
        ),
        conceitos_sem_conceito=("SEM CONCEITO", None),
    )
    periodo_continuidade = periodo_recencia if agrupar_por_ativo == "recencia" else "ano_atual"
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
    _log_perf(app, "projecao_representante_build", perf_total, total=total_oracle)
    return {
        "representantes_ordenados": representantes_ordenados,
        "consultores": consultores_oracle,
        "total": total_oracle,
        "stats": stats_oracle,
    }


def render_aba_oracle(
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
    total_construtoras_badge: int = 0,
    total_retornos_atrasados_badge: int = 0,
    dashboard_tipo=None,
    visao=None,
    agrupar_por="representante",
    periodo_recencia="ano_atual",
    lazy_grupo_nome=None,
    lazy_offset=0,
    lazy_limit=150,
):
    # REGRA VALIDADA (2026-03): usar Oracle como fonte de verdade da lista 90-150d.
    # Nao voltar para filtro principal via MySQL local.
    perf_total = time.perf_counter()
    periodo_oracle = request.args.get("periodo_oracle")
    conceito_filtro, consultor_filtro, termo = extrair_filtros_listagem(request)
    agrupar_por_ativo = agrupar_por if agrupar_por in ("representante", "uf", "consultor", "recencia") else "representante"
    if current_user.tipo == "representante":
        perf_step = time.perf_counter()
        projecao = carregar_ou_gerar_projecao_representante(
            codigo_representante=str(current_user.codigo_representante or ""),
            carteira="oracle",
            agrupar_por=agrupar_por_ativo,
            periodo_recencia=periodo_recencia,
            gerador=lambda: _carregar_base_oracle_representante(
                app=app,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                agrupar_por_ativo=agrupar_por_ativo,
                periodo_recencia=periodo_recencia,
            ),
        )
        _log_perf(app, "projecao_representante_cache", perf_step, grupos=len(projecao.get("representantes_ordenados") or []))
        representantes_ordenados, consultores_oracle, total_oracle, stats_oracle = filtrar_projecao_agrupada(
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
                "centralizadora",
                "conceito",
                "municipio",
                "uf",
                "contato",
                "cd_cliente_oracle",
            ),
            chave_sem_grupo=(
                "SEM UF"
                if agrupar_por_ativo == "uf"
                else ("SEM CONSULTOR" if agrupar_por_ativo == "consultor" else "")
            ),
            conceitos_sem_conceito=("SEM CONCEITO", None),
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
        response = render_template(
            "meus_clientes.html",
            representantes=representantes_ordenados,
            aba=aba,
            total_pendentes=total_pendentes,
            total_retornar=total_retornar,
            total_oracle=int(total_oracle_badge or 0),
            total_ativos=total_ativos_badge,
            total_inativos=total_inativos_badge,
            total_proximos=total_proximos_badge,
            total_construtoras=total_construtoras_badge,
            total_retornos_atrasados=total_retornos_atrasados_badge,
            usar_vista_agrupada=True,
            is_supervisor=current_user.tipo == "supervisor",
            stats={},
            stats_oracle=stats_oracle,
            consultores_oracle=consultores_oracle,
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
        _log_perf(app, "total", perf_total, total=total_oracle)
        return response

    perf_step = time.perf_counter()
    if current_user.tipo == "representante":
        clientes_oracle = carregar_clientes_oracle_snapshot_representante()
    else:
        clientes_oracle = carregar_clientes_oracle_deduplicados(app.logger, periodo_oracle)
    _log_perf(app, "carregar_clientes_oracle", perf_step, total=len(clientes_oracle or []))

    codigos_oracle = [
        str(c.get("cd_cliente")).strip()
        for c in clientes_oracle
        if c.get("cd_cliente")
    ]
    perf_step = time.perf_counter()
    pagamento_medio_por_cd = (
        carregar_pagamento_medio_representante(codigos_oracle)
        if codigos_oracle else {}
    )
    _log_perf(app, "pagamento_medio", perf_step, codigos=len(codigos_oracle))

    clientes_locais_por_cd = {}
    stats_ligacoes_por_cliente_id = {}
    locks_por_cliente_id = {}
    filtrar_oracle_por_categoria = current_user.tipo == "consultor"
    mapa_nome_para_id_oracle = {}
    mapa_codigo_para_id_oracle = {}
    if filtrar_oracle_por_categoria:
        _, mapa_nome_para_id_oracle = carregar_mapa_nome_para_id_usuarios_ativos()
        mapa_codigo_para_id_oracle = construir_mapa_codigo_para_id(mapa_nome_para_id_oracle)
    if codigos_oracle:
        perf_step = time.perf_counter()
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
                Cliente.cd_cliente_oracle.in_(codigos_oracle),
            )
            .all()
        )
        _log_perf(app, "clientes_locais", perf_step, total=len(clientes_locais or []))

        ids_locais = [c.id for c in clientes_locais if c.id]
        if ids_locais:
            perf_step = time.perf_counter()
            locks_por_cliente_id, stats_ligacoes_por_cliente_id = carregar_stats_e_locks_por_cliente_id(
                ids_locais
            )
            _log_perf(app, "stats_locks", perf_step, ids=len(ids_locais))
        clientes_locais_por_cd = escolher_melhor_cliente_por_codigo(
            clientes_locais,
            stats_ligacoes_por_cliente_id,
        )

    perf_step = time.perf_counter()
    mapa_meses_compra = (
        carregar_meses_compra_representante(
            codigos_oracle,
            periodo=periodo_recencia,
        )
        if codigos_oracle else {}
    )
    _log_perf(app, "meses_compra", perf_step, codigos=len(codigos_oracle))
    meses_total_periodo = obter_total_meses_periodo(periodo_recencia)

    agrupar_por_ativo = agrupar_por if agrupar_por in ("representante", "uf", "consultor", "recencia") else "representante"
    representantes_data = {}
    if current_user.tipo == "representante":
        perf_step = time.perf_counter()
        projecao = carregar_ou_gerar_projecao_representante(
            codigo_representante=str(current_user.codigo_representante or ""),
            carteira="oracle",
            agrupar_por=agrupar_por_ativo,
            periodo_recencia=periodo_recencia,
            gerador=lambda: _carregar_base_oracle_representante(
                app=app,
                current_user=current_user,
                codigos_representantes_vinculados=codigos_representantes_vinculados,
                agrupar_por_ativo=agrupar_por_ativo,
                periodo_recencia=periodo_recencia,
            ),
        )
        _log_perf(app, "projecao_representante_cache", perf_step, grupos=len(projecao.get("representantes_ordenados") or []))
        representantes_ordenados, consultores_oracle, total_oracle, stats_oracle = filtrar_projecao_agrupada(
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
                "centralizadora",
                "conceito",
                "municipio",
                "uf",
                "contato",
                "cd_cliente_oracle",
            ),
            chave_sem_grupo=(
                "SEM UF"
                if agrupar_por_ativo == "uf"
                else ("SEM CONSULTOR" if agrupar_por_ativo == "consultor" else "")
            ),
            conceitos_sem_conceito=("SEM CONCEITO", None),
        )
    else:
        perf_step = time.perf_counter()
        for cliente_oracle in clientes_oracle:
            conceito_cliente = str(cliente_oracle.get("conceito") or "").strip().upper()
            consultor_cliente = str(cliente_oracle.get("consultor") or "").strip()

            if not corresponde_conceito_filtro(conceito_filtro, conceito_cliente):
                continue

            if not corresponde_consultor_filtro(consultor_filtro, consultor_cliente):
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
                    "cd_centralizado",
                    "nome_centralizadora",
                    "conceito",
                    "municipio",
                    "uf",
                ),
            ):
                continue

            cd_cliente = str(cliente_oracle.get("cd_cliente") or "").strip()
            cliente_local = clientes_locais_por_cd.get(cd_cliente) if cd_cliente else None

            if not representante_oracle_permitido_para_usuario(
                tipo_usuario=current_user.tipo,
                representante_texto=str(cliente_oracle.get("representante") or ""),
                codigos_representantes_vinculados=codigos_representantes_vinculados,
            ):
                continue

            if apenas_meus and current_user.tipo not in ("supervisor_repr", "representante"):
                if not cliente_local or cliente_local.consultor_id != current_user.id:
                    continue
            if filtrar_oracle_por_categoria and not consultor_categoria_permitido_para_usuario(
                tipo_usuario=current_user.tipo,
                consultor_cliente=consultor_cliente,
                current_user_id=current_user.id,
                mapa_codigo_para_id=mapa_codigo_para_id_oracle,
                mapa_nome_para_id=mapa_nome_para_id_oracle,
            ):
                continue

            stats_lig = (
                stats_ligacoes_por_cliente_id.get(cliente_local.id, {})
                if cliente_local and cliente_local.id else {}
            )
            lock_info = (
                locks_por_cliente_id.get(cliente_local.id, {})
                if cliente_local and cliente_local.id else {}
            )

            meses_com_compra = len({str(m) for m in (mapa_meses_compra.get(cd_cliente) or []) if m not in (None, "")})

            if agrupar_por_ativo == "uf":
                nome_grupo = str(cliente_oracle.get("uf") or "").strip().upper() or "SEM UF"
            elif agrupar_por_ativo == "consultor":
                nome_grupo = (
                    str(cliente_oracle.get("consultor") or "").strip()
                    or (cliente_local.consultor.nome if (cliente_local and cliente_local.consultor) else "")
                    or "SEM CONSULTOR"
                )
            elif agrupar_por_ativo == "recencia":
                nome_grupo = NOME_GRUPO_RECENCIA_LIVRE
            else:
                nome_grupo = (
                    str(cliente_oracle.get("representante") or "").strip() or "SEM REPRESENTANTE"
                )

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
                conceito=cliente_oracle.get("conceito", ""),
                origem_padrao="oracle",
            )

            representantes_data[nome_grupo]["clientes"].append(dados_cliente)

            if cliente_local and cliente_local.consultor:
                nome_consultor = cliente_local.consultor.nome
                reps = representantes_data[nome_grupo]["consultores_internos"]
                if nome_consultor not in reps:
                    reps[nome_consultor] = 0
                reps[nome_consultor] += 1
        _log_perf(app, "montar_payloads", perf_step, grupos=len(representantes_data))

        perf_step = time.perf_counter()
        representantes_ordenados, consultores_oracle, total_oracle, stats_oracle = consolidar_dados_grupos(
            representantes_data=representantes_data,
            chave_sem_grupo=(
                "SEM UF"
                if agrupar_por_ativo == "uf"
                else ("SEM CONSULTOR" if agrupar_por_ativo == "consultor" else "")
            ),
            conceitos_sem_conceito=("SEM CONCEITO", None),
        )
        _log_perf(app, "consolidar_grupos", perf_step, total=total_oracle)
        perf_step = time.perf_counter()
        periodo_continuidade = periodo_recencia if agrupar_por_ativo == "recencia" else "ano_atual"
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
        _log_perf(app, "continuidade_compra", perf_step, total=total_oracle)
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

    perf_step = time.perf_counter()
    if current_user.tipo in ("supervisor_repr", "representante"):
        total_pendentes, total_retornar = calcular_totais_abas_proximos(
            current_user,
            codigos_representantes_vinculados,
        )
    else:
        todos_clientes = Cliente.query.filter_by(ativo=True)
        todos_clientes = aplicar_filtro_carteira_especial_consultor(todos_clientes, current_user)
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
    response = render_template(
        "meus_clientes.html",
        representantes=representantes_ordenados,
        aba=aba,
        total_pendentes=total_pendentes,
        total_retornar=total_retornar,
        total_oracle=int(total_oracle_badge or 0),
        total_ativos=total_ativos_badge,
        total_inativos=total_inativos_badge,
        total_proximos=total_proximos_badge,
        total_construtoras=total_construtoras_badge,
        total_retornos_atrasados=total_retornos_atrasados_badge,
        usar_vista_agrupada=True,
        is_supervisor=current_user.tipo == "supervisor",
        stats={},
        stats_oracle=stats_oracle,
        consultores_oracle=consultores_oracle,
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
    _log_perf(app, "template", perf_step, total=total_oracle)
    _log_perf(app, "total", perf_total, total=total_oracle)
    return response
