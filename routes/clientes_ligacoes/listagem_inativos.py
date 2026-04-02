from datetime import datetime

from flask import render_template
from sqlalchemy import or_

from core.extensions import db
from core.models import Cliente, Ligacao, SyncResumoDiario, Usuario
from routes.clientes_ligacoes.client_metrics import carregar_stats_e_locks_por_cliente_id
from routes.clientes_ligacoes.listagem_client_payload import montar_payload_cliente_oracle
from routes.clientes_ligacoes.listagem_filters import (
    corresponde_conceito_filtro,
    corresponde_consultor_filtro,
    corresponde_termo_busca,
    extrair_filtros_listagem,
)
from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.domain_utils import (
    _codigo_representante_de_texto,
    _normalizar_codigo_representante,
    _resolver_consultor_id_por_categoria,
    normalizar_conceito,
)
from routes.clientes_ligacoes.listagem_grouping_utils import consolidar_dados_grupos
from routes.clientes_ligacoes.inativos_tab import carregar_clientes_inativos_enriquecidos
from routes.clientes_ligacoes.televendas_stats import montar_stats_produtividade_televendas


def render_aba_inativos(
    *,
    app,
    aba: str,
    request,
    current_user,
    codigos_representantes_vinculados,
    total_oracle_badge: int,
    total_proximos_badge: int,
    cache_store: dict,
):
    # REGRA VALIDADA (2026-03): lista de inativos vem da base local sincronizada diariamente.
    app.logger.info("=== INICIANDO TRATAMENTO ABA INATIVOS ===")
    app.logger.info(f"Usuario: {current_user.nome} ({current_user.tipo})")

    clientes_oracle_inativos = carregar_clientes_inativos_enriquecidos(app.logger)
    filtrar_inativos_por_categoria = current_user.tipo == "consultor"
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
    clientes_locais_por_cd = {}
    stats_ligacoes_por_cliente_id = {}
    locks_por_cliente_id = {}
    locks_por_cd_oracle = {}
    if codigos_inativos:
        clientes_locais = (
            Cliente.query
            .filter(
                Cliente.cd_cliente_oracle.in_(codigos_inativos),
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
            locks_rows = (
                db.session.query(
                    Cliente.id.label("cliente_id"),
                    Cliente.cd_cliente_oracle.label("cd_cliente_oracle"),
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
            for row in locks_rows:
                cd_lock = str(row.cd_cliente_oracle or "").strip()
                if not cd_lock:
                    continue
                if cd_lock not in locks_por_cd_oracle:
                    locks_por_cd_oracle[cd_lock] = {
                        "ativo": True,
                        "por_nome": (row.usuario_nome or "Outro usuario"),
                        "ate": None,
                    }

    representantes_data = {}
    for cliente_oracle in clientes_oracle_inativos:
        conceito_cliente = normalizar_conceito(cliente_oracle.get("conceito"))
        consultor_cliente = str(cliente_oracle.get("consultor") or "").strip()

        if not corresponde_conceito_filtro(conceito_filtro, conceito_cliente):
            continue

        if not corresponde_consultor_filtro(consultor_filtro, consultor_cliente):
            continue
        if filtrar_inativos_por_categoria and consultor_cliente:
            consultor_esperado = _resolver_consultor_id_por_categoria(
                consultor_cliente,
                mapa_codigo_para_id=mapa_codigo_para_id_inativos,
                mapa_nome_para_id=mapa_nome_para_id_inativos,
            )
            if consultor_esperado and consultor_esperado != current_user.id:
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

        if current_user.tipo == "supervisor_repr":
            representante_str = str(cliente_oracle.get("representante") or "")
            cd_representante = _normalizar_codigo_representante(
                _codigo_representante_de_texto(representante_str)
            )
            if not cd_representante or cd_representante not in codigos_representantes_vinculados:
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
        total_ligacoes_local = stats_lig.get("total_ligacoes", 0)

        # Fluxo operacional: apos primeiro contato, o cliente sai de "Inativos"
        # e passa a ser tratado nas abas "Contatados" ou "Retornar".
        if cliente_local and (total_ligacoes_local > 0 or cliente_local.proxima_ligacao is not None):
            continue

        uf_grupo = str(cliente_oracle.get("uf") or "").strip().upper() or "SEM UF"

        if uf_grupo not in representantes_data:
            representantes_data[uf_grupo] = {
                "nome": uf_grupo,
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
            conceito=conceito_cliente,
            origem_padrao="oracle_inativos",
        )

        representantes_data[uf_grupo]["clientes"].append(dados_cliente)
        if consultor_cliente:
            consultores_uf = representantes_data[uf_grupo]["consultores_internos"]
            consultores_uf[consultor_cliente] = consultores_uf.get(consultor_cliente, 0) + 1

    representantes_ordenados, consultores_inativos, total_inativos, stats_inativos = consolidar_dados_grupos(
        representantes_data=representantes_data,
        chave_sem_grupo="SEM UF",
        conceitos_sem_conceito=("", "SEM CONCEITO", None),
    )
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

    total_contatados_tv = 0
    total_retornar_tv = 0
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
        total_retornar_tv = base_tv.filter(Cliente.proxima_ligacao.isnot(None)).count()
        total_contatados_tv = (
            base_tv
            .filter(Cliente.proxima_ligacao.is_(None))
            .filter(Cliente.id.in_(clientes_ligados_por_tv))
            .count()
        )

    stats_televendas = montar_stats_produtividade_televendas()

    return render_template(
        "meus_clientes.html",
        representantes=representantes_ordenados,
        aba=aba,
        total_pendentes=0,
        total_contatados=total_contatados_tv,
        total_retornar=total_retornar_tv,
        total_oracle=total_oracle_badge,
        total_inativos=total_inativos,
        total_proximos=total_proximos_badge,
        usar_vista_agrupada=True,
        is_supervisor=current_user.tipo == "supervisor",
        stats={},
        stats_inativos=stats_inativos,
        movimento_inativos_hoje=movimento_inativos_hoje,
        stats_televendas=stats_televendas,
        consultores_inativos=consultores_inativos,
        q=request.args.get("q", ""),
        meses_disponiveis_consultor=[],
        mes_filtro=None,
        ano_filtro=None,
    )
