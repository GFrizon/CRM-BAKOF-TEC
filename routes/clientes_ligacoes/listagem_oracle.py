from datetime import datetime, timedelta

from flask import render_template
from sqlalchemy import and_
from sqlalchemy.orm import selectinload

from core.extensions import db
from core.models import Cliente, Ligacao
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
from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.listagem_grouping_utils import consolidar_dados_grupos
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados


def render_aba_oracle(
    *,
    app,
    aba: str,
    request,
    current_user,
    codigos_representantes_vinculados,
    apenas_meus: bool,
    total_inativos_badge: int,
    total_proximos_badge: int,
    dashboard_tipo=None,
    visao=None,
):
    # REGRA VALIDADA (2026-03): usar Oracle como fonte de verdade da lista 90-150d.
    # Nao voltar para filtro principal via MySQL local.
    periodo_oracle = request.args.get("periodo_oracle")
    conceito_filtro, consultor_filtro, termo = extrair_filtros_listagem(request)
    clientes_oracle = carregar_clientes_oracle_deduplicados(app.logger, periodo_oracle)

    codigos_oracle = [
        str(c.get("cd_cliente")).strip()
        for c in clientes_oracle
        if c.get("cd_cliente")
    ]

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
        clientes_locais = (
            Cliente.query
            .options(selectinload(Cliente.consultor))
            .filter(
                Cliente.cd_cliente_oracle.in_(codigos_oracle),
                Cliente.ativo == True,
            )
            .all()
        )
        clientes_locais_por_cd = {
            str(c.cd_cliente_oracle): c
            for c in clientes_locais if c.cd_cliente_oracle
        }

        ids_locais = [c.id for c in clientes_locais if c.id]
        if ids_locais:
            locks_por_cliente_id, stats_ligacoes_por_cliente_id = carregar_stats_e_locks_por_cliente_id(
                ids_locais
            )

    representantes_data = {}
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

        if apenas_meus and current_user.tipo != "supervisor_repr":
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

        representante = str(cliente_oracle.get("representante") or "").strip() or "SEM REPRESENTANTE"

        if representante not in representantes_data:
            representantes_data[representante] = {
                "nome": representante,
                "clientes": [],
                "total_clientes": 0,
                "liberados": 0,
                "inadimplentes": 0,
                "sem_conceito": 0,
                "ticket_medio": 0,
                "dias_medio": 0,
                "consultores_internos": {},
            }

        dados_cliente = montar_payload_cliente_oracle(
            cliente_oracle=cliente_oracle,
            cliente_local=cliente_local,
            stats_lig=stats_lig,
            lock_info=lock_info,
            conceito=cliente_oracle.get("conceito", ""),
            origem_padrao="oracle",
        )

        representantes_data[representante]["clientes"].append(dados_cliente)

        if cliente_local and cliente_local.consultor:
            nome_consultor = cliente_local.consultor.nome
            reps = representantes_data[representante]["consultores_internos"]
            if nome_consultor not in reps:
                reps[nome_consultor] = 0
            reps[nome_consultor] += 1

    representantes_ordenados, consultores_oracle, total_oracle, stats_oracle = consolidar_dados_grupos(
        representantes_data=representantes_data,
        chave_sem_grupo="SEM REPRESENTANTE",
        conceitos_sem_conceito=("SEM CONCEITO", None),
    )

    todos_clientes = Cliente.query.filter_by(ativo=True)
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

    total_contatados = (
        todos_clientes
        .filter(
            Cliente.id.in_(
                db.session.query(Ligacao.cliente_id).filter(
                    Ligacao.consultor_id == current_user.id if apenas_meus else True
                )
            )
        )
        .filter(Cliente.proxima_ligacao.is_(None))
        .count()
    )

    total_retornar = todos_clientes.filter(Cliente.proxima_ligacao.isnot(None)).count()
    return render_template(
        "meus_clientes.html",
        representantes=representantes_ordenados,
        aba=aba,
        total_pendentes=total_pendentes,
        total_contatados=total_contatados,
        total_retornar=total_retornar,
        total_oracle=total_oracle,
        total_inativos=total_inativos_badge,
        total_proximos=total_proximos_badge,
        usar_vista_agrupada=True,
        is_supervisor=current_user.tipo == "supervisor",
        stats={},
        stats_oracle=stats_oracle,
        consultores_oracle=consultores_oracle,
        q=request.args.get("q", ""),
        meses_disponiveis_consultor=[],
        mes_filtro=None,
        ano_filtro=None,
        dashboard_tipo=dashboard_tipo,
        visao=visao,
    )

