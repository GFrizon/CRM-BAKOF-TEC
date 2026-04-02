from datetime import datetime

from flask import render_template
from sqlalchemy import or_

from core.extensions import db
from core.models import Cliente, Ligacao, SyncResumoDiario, Usuario
from routes.clientes_ligacoes.client_metrics import carregar_stats_e_locks_por_cliente_id
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
from routes.clientes_ligacoes.grouping_stats import (
    calcular_stats_gerais_grupos,
    extrair_consultores_dos_grupos,
)
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

    conceito_filtro = (request.args.get("conceito_filtro") or "").strip().upper()
    consultor_filtro = (request.args.get("consultor_filtro") or "").strip()
    termo = (request.args.get("q") or "").strip().lower()

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

        if conceito_filtro:
            if conceito_filtro in ("SEM_CONCEITO", "SEM CONCEITO"):
                if conceito_cliente not in ("", "SEM CONCEITO"):
                    continue
            elif conceito_cliente != conceito_filtro:
                continue

        if consultor_filtro and consultor_filtro.lower() not in consultor_cliente.lower():
            continue
        if filtrar_inativos_por_categoria and consultor_cliente:
            consultor_esperado = _resolver_consultor_id_por_categoria(
                consultor_cliente,
                mapa_codigo_para_id=mapa_codigo_para_id_inativos,
                mapa_nome_para_id=mapa_nome_para_id_inativos,
            )
            if consultor_esperado and consultor_esperado != current_user.id:
                continue

        if termo:
            base_busca = " ".join(
                [
                    str(cliente_oracle.get("cliente") or ""),
                    str(cliente_oracle.get("cnpj") or ""),
                    str(cliente_oracle.get("telefone1") or ""),
                    str(cliente_oracle.get("telefone2") or ""),
                    str(cliente_oracle.get("representante") or ""),
                    str(cliente_oracle.get("consultor") or ""),
                    str(cliente_oracle.get("conceito") or ""),
                    str(cliente_oracle.get("municipio") or ""),
                    str(cliente_oracle.get("uf") or ""),
                ]
            ).lower()
            if termo not in base_busca:
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
        ultima_local = stats_lig.get("ultima_ligacao")
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

        dados_cliente = {
            "id": cliente_local.id if cliente_local else None,
            "nome": cliente_oracle.get("cliente", ""),
            "cnpj": cliente_oracle.get("cnpj", ""),
            "telefone": (
                cliente_local.telefone
                if cliente_local and cliente_local.telefone
                else (cliente_oracle.get("telefone1") or cliente_oracle.get("telefone2"))
            ),
            "telefone2": (cliente_local.telefone2 if cliente_local else cliente_oracle.get("telefone2")),
            "representante_nome": cliente_oracle.get("representante", "SEM REPRESENTANTE"),
            "ultima_ligacao": ultima_local,
            "ultima_ligacao_por": stats_lig.get("ultima_ligacao_por"),
            "total_ligacoes": total_ligacoes_local,
            "proxima_ligacao": (cliente_local.proxima_ligacao if cliente_local else None),
            "origem": (getattr(cliente_local, "origem", None) if cliente_local else "oracle_inativos"),
            "cd_cliente_oracle": cliente_oracle.get("cd_cliente"),
            "categoria_consultor": cliente_oracle.get("consultor", ""),
            "centralizadora": (
                f"{cliente_oracle.get('cd_centralizado')} - {cliente_oracle.get('nome_centralizadora')}"
                if cliente_oracle.get("cd_centralizado") and cliente_oracle.get("nome_centralizadora")
                else (str(cliente_oracle.get("cd_centralizado") or "").strip() or "")
            ),
            "consultor_id": (cliente_local.consultor_id if cliente_local else None),
            "conceito": conceito_cliente,
            "municipio": cliente_oracle.get("municipio", ""),
            "uf": cliente_oracle.get("uf", ""),
            "contato": cliente_oracle.get("contato", ""),
            "ultimo_pedido_oracle": cliente_oracle.get("dt_pedido"),
            "valor_ultimo_pedido": cliente_oracle.get("total_pedido"),
            "valor_total_365dias": (cliente_local.valor_total_365dias if cliente_local else 0),
            "situacao_ultimo_pedido": cliente_oracle.get("situacao", ""),
            "representante_oracle": cliente_oracle.get("representante", "SEM REPRESENTANTE"),
            "em_atendimento_ativo": bool(lock_info.get("ativo")),
            "em_atendimento_por_nome": lock_info.get("por_nome"),
            "em_atendimento_ate": lock_info.get("ate"),
        }

        representantes_data[uf_grupo]["clientes"].append(dados_cliente)
        if consultor_cliente:
            consultores_uf = representantes_data[uf_grupo]["consultores_internos"]
            consultores_uf[consultor_cliente] = consultores_uf.get(consultor_cliente, 0) + 1

    for _, dados in representantes_data.items():
        clientes_rep = dados["clientes"]
        dados["total_clientes"] = len(clientes_rep)
        dados["liberados"] = sum(1 for c in clientes_rep if c.get("conceito") == "LIBERADO")
        dados["inadimplentes"] = sum(1 for c in clientes_rep if c.get("conceito") == "INADIMPLENTE")
        dados["sem_conceito"] = sum(
            1
            for c in clientes_rep
            if c.get("conceito") in ("", "SEM CONCEITO", None)
        )

        valores = [c.get("valor_ultimo_pedido", 0) for c in clientes_rep if c.get("valor_ultimo_pedido")]
        dados["ticket_medio"] = sum(valores) / len(valores) if valores else 0

        hoje = datetime.now()
        dias_sem_pedido = []
        for c in clientes_rep:
            if c.get("ultimo_pedido_oracle"):
                d = (hoje - c["ultimo_pedido_oracle"]).days
                dias_sem_pedido.append(d)
        dados["dias_medio"] = sum(dias_sem_pedido) / len(dias_sem_pedido) if dias_sem_pedido else 0

        dados["clientes"] = sorted(
            clientes_rep,
            key=lambda x: (
                float(x.get("valor_total_365dias") or 0),
                float(x.get("valor_ultimo_pedido") or 0),
            ),
            reverse=True,
        )

    representantes_ordenados = sorted(
        representantes_data.items(),
        key=lambda x: (-x[1]["total_clientes"], x[0] == "SEM UF", x[0]),
    )

    consultores_inativos = extrair_consultores_dos_grupos(representantes_data)

    total_inativos, stats_inativos = calcular_stats_gerais_grupos(representantes_data)
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
