import time
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.models import Cliente, Ligacao
from routes.clientes_ligacoes.continuidade_compra import (
    enriquecer_payloads_com_continuidade_compra,
    obter_total_meses_periodo,
)
from routes.clientes_ligacoes.domain_utils import _cliente_tem_representante_vinculado
from routes.clientes_ligacoes.listagem_grouping_utils import (
    NOME_GRUPO_RECENCIA_LIVRE,
    ordenar_clientes_recencia_frequencia,
)
from routes.clientes_ligacoes.listagem_client_payload import montar_status_contato_mensal_por_data
from routes.clientes_ligacoes.pedido_andamento_helper import marcar_pedido_em_andamento_payloads
from routes.clientes_ligacoes.perf_logger import log_perf
from services.representante_projection_cache_service import (
    carregar_ou_gerar_projecao_representante,
)
from services.representante_metricas_cache_service import (
    carregar_meses_compra_representante,
    carregar_pagamento_medio_representante,
)


def _log_perf(label, started_at, **extra):
    log_perf(current_app, "meus-clientes/proximos", label, started_at, **extra)


def preparar_contexto_proximos_inativacao(
    current_user,
    codigos_representantes_vinculados,
    agrupar_por=None,
    periodo_recencia="ano_atual",
    incluir_pedido_em_andamento=True,
):
    modo_agrupamento = (
        agrupar_por
        if agrupar_por in ("representante", "uf", "recencia")
        else (
            "representante"
            if current_user.tipo in ("consultor", "televendas", "supervisor_repr", "representante")
            else "consultor"
        )
    )
    if current_user.tipo == "representante":
        return _carregar_contexto_proximos_representante_cache(
            current_user=current_user,
            codigos_representantes_vinculados=codigos_representantes_vinculados,
            agrupar_por=modo_agrupamento,
            periodo_recencia=periodo_recencia,
            incluir_pedido_em_andamento=incluir_pedido_em_andamento,
        )

    perf_total = time.perf_counter()
    agora_px = datetime.now()
    limite_min_px = agora_px - timedelta(days=180)
    limite_max_px = agora_px - timedelta(days=151)

    q_proximos = (
        Cliente.query
        .options(joinedload(Cliente.consultor))
        .filter(
            Cliente.ativo == True,
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.isnot(None),
            Cliente.ultimo_pedido_oracle.between(limite_min_px, limite_max_px),
        )
    )
    if current_user.tipo in ("consultor", "televendas"):
        q_proximos = q_proximos.filter(Cliente.consultor_id == current_user.id)

    perf_step = time.perf_counter()
    clientes_proximos_raw = q_proximos.all()
    _log_perf("clientes_proximos", perf_step, total=len(clientes_proximos_raw or []))
    codigos_proximos = [str(c.cd_cliente_oracle or "").strip() for c in clientes_proximos_raw if c.cd_cliente_oracle]
    perf_step = time.perf_counter()
    pagamento_medio_por_cd = carregar_pagamento_medio_representante(codigos_proximos) if codigos_proximos else {}
    _log_perf("pagamento_medio", perf_step, codigos=len(codigos_proximos))
    perf_step = time.perf_counter()
    mapa_meses_compra = (
        carregar_meses_compra_representante(codigos_proximos, periodo=periodo_recencia)
        if codigos_proximos else {}
    )
    _log_perf("meses_compra", perf_step, codigos=len(codigos_proximos))
    meses_total_periodo = obter_total_meses_periodo(periodo_recencia)

    if current_user.tipo in ("supervisor_repr", "representante"):
        clientes_proximos_raw = [
            c for c in clientes_proximos_raw
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        ]

    ids_proximos = [c.id for c in clientes_proximos_raw]
    stats_lig_px = {}
    if ids_proximos:
        perf_step = time.perf_counter()
        lig_agg_px = (
            db.session.query(
                Ligacao.cliente_id,
                func.count(Ligacao.id).label("total_ligacoes"),
                func.max(Ligacao.data_hora).label("ultima_ligacao"),
            )
            .filter(Ligacao.cliente_id.in_(ids_proximos))
            .group_by(Ligacao.cliente_id)
            .all()
        )
        stats_lig_px = {
            row.cliente_id: {
                "total_ligacoes": int(row.total_ligacoes or 0),
                "ultima_ligacao": row.ultima_ligacao,
            }
            for row in lig_agg_px
        }
        _log_perf("stats_ligacoes", perf_step, ids=len(ids_proximos))

    grupos_px = {}
    perf_step = time.perf_counter()
    for c in clientes_proximos_raw:
        consultor_nome = (c.consultor.nome if c.consultor else None) or "SEM CONSULTOR"
        representante_nome = (c.representante_oracle or c.representante_nome or "").strip() or "SEM REPRESENTANTE"
        uf_nome = (c.uf or "").strip().upper() or "SEM UF"
        meses_com_compra = len({
            str(m)
            for m in (mapa_meses_compra.get(str(c.cd_cliente_oracle or "").strip()) or [])
            if m not in (None, "")
        })
        if modo_agrupamento == "uf":
            nome_grupo = uf_nome
        elif modo_agrupamento == "recencia":
            nome_grupo = NOME_GRUPO_RECENCIA_LIVRE
        elif modo_agrupamento == "representante":
            nome_grupo = representante_nome
        else:
            nome_grupo = consultor_nome

        if nome_grupo not in grupos_px:
            grupos_px[nome_grupo] = {
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
        st_lig = stats_lig_px.get(c.id, {})
        status_contato_mensal = montar_status_contato_mensal_por_data(
            st_lig.get("ultima_ligacao")
        )
        dias_sem = (agora_px - c.ultimo_pedido_oracle).days if c.ultimo_pedido_oracle else 0
        dias_para_inativar = max(0, 181 - dias_sem)
        data_prevista_inativacao = (
            c.ultimo_pedido_oracle + timedelta(days=181)
            if c.ultimo_pedido_oracle else None
        )

        dados_px = {
            "id": c.id,
            "nome": c.nome,
            "cnpj": c.cnpj,
            "telefone": c.telefone,
            "telefone2": c.telefone2,
            "representante_nome": c.representante_oracle or c.representante_nome or "",
            "representante_oracle": c.representante_oracle or "",
            "ultima_ligacao": st_lig.get("ultima_ligacao"),
            "ultima_ligacao_por": None,
            "total_ligacoes": st_lig.get("total_ligacoes", 0),
            "especial_contato_mensal_status": status_contato_mensal["status"],
            "especial_contato_mensal_label": status_contato_mensal["label"],
            "proxima_ligacao": c.proxima_ligacao,
            "origem": c.origem,
            "cd_cliente_oracle": c.cd_cliente_oracle,
            "categoria_consultor": c.categoria_consultor or "",
            "centralizadora": "",
            "consultor_id": c.consultor_id,
            "conceito": c.conceito or "",
            "municipio": c.municipio or "",
            "uf": c.uf or "",
            "contato": c.contato or "",
            "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
            "valor_ultimo_pedido": c.valor_ultimo_pedido,
            "valor_total_365dias": c.valor_total_365dias or 0,
            "meses_com_compra": meses_com_compra,
            "meses_total_periodo": meses_total_periodo,
            "situacao_ultimo_pedido": c.situacao_ultimo_pedido or "",
            "em_atendimento_ativo": bool(
                c.em_atendimento_por and c.em_atendimento_ate and c.em_atendimento_ate > datetime.now()
            ),
            "em_atendimento_por_nome": None,
            "em_atendimento_ate": (
                c.em_atendimento_ate.strftime("%d/%m/%Y %H:%M")
                if c.em_atendimento_ate and c.em_atendimento_ate > datetime.now()
                else None
            ),
            "dias_sem_pedido": dias_sem,
            "dias_para_inativar": dias_para_inativar,
            "data_prevista_inativacao": data_prevista_inativacao,
            "pagamento_medio_dias": pagamento_medio_por_cd.get(str(c.cd_cliente_oracle or "").strip()),
        }
        grupos_px[nome_grupo]["clientes"].append(dados_px)
    _log_perf("montar_payloads", perf_step, grupos=len(grupos_px))

    todos_clientes_payload = [
        cliente
        for dados in grupos_px.values()
        for cliente in dados["clientes"]
    ]
    perf_step = time.perf_counter()
    periodo_continuidade = periodo_recencia if modo_agrupamento == "recencia" else "ano_atual"
    mapa_meses_continuidade = (
        mapa_meses_compra
        if periodo_continuidade == periodo_recencia
        else None
    )
    enriquecer_payloads_com_continuidade_compra(
        todos_clientes_payload,
        periodo=periodo_continuidade,
        mapa_meses=mapa_meses_continuidade,
    )
    _log_perf("continuidade_compra", perf_step, total=len(todos_clientes_payload))
    if incluir_pedido_em_andamento:
        perf_step = time.perf_counter()
        marcar_pedido_em_andamento_payloads(todos_clientes_payload)
        _log_perf("pedido_em_andamento", perf_step, total=len(todos_clientes_payload))
    else:
        _log_perf("pedido_em_andamento", time.perf_counter(), total=0, skipped_lazy=True)

    perf_step = time.perf_counter()
    for dados in grupos_px.values():
        cls = dados["clientes"]
        dados["total_clientes"] = len(cls)
        dados["liberados"] = sum(1 for c in cls if c.get("conceito") == "LIBERADO")
        dados["inadimplentes"] = sum(1 for c in cls if c.get("conceito") == "INADIMPLENTE")
        dados["sem_conceito"] = sum(1 for c in cls if c.get("conceito") in ("", "SEM CONCEITO", None))
        vals = [c.get("valor_ultimo_pedido", 0) for c in cls if c.get("valor_ultimo_pedido")]
        dados["ticket_medio"] = sum(vals) / len(vals) if vals else 0
        dias_list = [c.get("dias_sem_pedido", 0) for c in cls if c.get("dias_sem_pedido")]
        dados["dias_medio"] = sum(dias_list) / len(dias_list) if dias_list else 0
        if modo_agrupamento == "recencia":
            dados["clientes"] = ordenar_clientes_recencia_frequencia(cls)
        else:
            dados["clientes"] = sorted(cls, key=lambda x: -x.get("dias_sem_pedido", 0))
    _log_perf("consolidar_grupos", perf_step, grupos=len(grupos_px))

    if modo_agrupamento == "recencia":
        representantes_ordenados_px = [(NOME_GRUPO_RECENCIA_LIVRE, grupos_px[NOME_GRUPO_RECENCIA_LIVRE])] if NOME_GRUPO_RECENCIA_LIVRE in grupos_px else []
    else:
        representantes_ordenados_px = sorted(
            grupos_px.items(),
            key=lambda x: (
                -x[1]["total_clientes"],
                x[0] == (
                    "SEM UF" if modo_agrupamento == "uf"
                    else ("SEM REPRESENTANTE" if modo_agrupamento == "representante" else "SEM CONSULTOR")
                ),
                x[0],
            ),
        )

    total_proximos_count = sum(len(d["clientes"]) for d in grupos_px.values())
    total_lib_px = sum(d["liberados"] for d in grupos_px.values())
    total_inad_px = sum(d["inadimplentes"] for d in grupos_px.values())
    total_sc_px = sum(d["sem_conceito"] for d in grupos_px.values())
    todos_vals_px = [
        c.get("valor_ultimo_pedido")
        for _, d in grupos_px.items()
        for c in d["clientes"]
        if c.get("valor_ultimo_pedido")
    ]
    todos_dias_px = [
        c.get("dias_sem_pedido", 0)
        for _, d in grupos_px.items()
        for c in d["clientes"]
    ]
    ticket_medio_px = sum(todos_vals_px) / len(todos_vals_px) if todos_vals_px else 0
    dias_medio_px = sum(todos_dias_px) / len(todos_dias_px) if todos_dias_px else 0
    stats_proximos = {
        "liberados": total_lib_px,
        "inadimplentes": total_inad_px,
        "sem_conceito": total_sc_px,
        "ticket_medio": ticket_medio_px,
        "dias_sem_pedido": int(dias_medio_px),
        "perc_liberados": round((total_lib_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
        "perc_inadimplentes": round((total_inad_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
        "perc_sem_conceito": round((total_sc_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
    }

    _log_perf("total", perf_total, total=total_proximos_count)
    return representantes_ordenados_px, total_proximos_count, stats_proximos


def _carregar_contexto_proximos_representante_cache(
    *,
    current_user,
    codigos_representantes_vinculados,
    agrupar_por,
    periodo_recencia,
    incluir_pedido_em_andamento,
):
    def _gerar():
        perf_total = time.perf_counter()
        agora_px = datetime.now()
        limite_min_px = agora_px - timedelta(days=180)
        limite_max_px = agora_px - timedelta(days=151)
        clientes_proximos_raw = (
            Cliente.query
            .options(joinedload(Cliente.consultor))
            .filter(
                Cliente.ativo == True,
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.isnot(None),
                Cliente.ultimo_pedido_oracle.between(limite_min_px, limite_max_px),
            )
            .all()
        )
        clientes_proximos_raw = [
            c for c in clientes_proximos_raw
            if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
        ]
        codigos_proximos = [str(c.cd_cliente_oracle or "").strip() for c in clientes_proximos_raw if c.cd_cliente_oracle]
        pagamento_medio_por_cd = carregar_pagamento_medio_representante(codigos_proximos)
        mapa_meses_compra = carregar_meses_compra_representante(
            codigos_proximos,
            periodo=periodo_recencia,
        )
        meses_total_periodo = obter_total_meses_periodo(periodo_recencia)
        ids_proximos = [c.id for c in clientes_proximos_raw]
        stats_lig_px = {}
        if ids_proximos:
            lig_agg_px = (
                db.session.query(
                    Ligacao.cliente_id,
                    func.count(Ligacao.id).label("total_ligacoes"),
                    func.max(Ligacao.data_hora).label("ultima_ligacao"),
                )
                .filter(Ligacao.cliente_id.in_(ids_proximos))
                .group_by(Ligacao.cliente_id)
                .all()
            )
            stats_lig_px = {
                row.cliente_id: {
                    "total_ligacoes": int(row.total_ligacoes or 0),
                    "ultima_ligacao": row.ultima_ligacao,
                }
                for row in lig_agg_px
            }

        grupos_px = {}
        for c in clientes_proximos_raw:
            consultor_nome = (c.consultor.nome if c.consultor else None) or "SEM CONSULTOR"
            representante_nome = (c.representante_oracle or c.representante_nome or "").strip() or "SEM REPRESENTANTE"
            uf_nome = (c.uf or "").strip().upper() or "SEM UF"
            meses_com_compra = len({
                str(m)
                for m in (mapa_meses_compra.get(str(c.cd_cliente_oracle or "").strip()) or [])
                if m not in (None, "")
            })
            if agrupar_por == "uf":
                nome_grupo = uf_nome
            elif agrupar_por == "recencia":
                nome_grupo = NOME_GRUPO_RECENCIA_LIVRE
            else:
                nome_grupo = representante_nome
            if nome_grupo not in grupos_px:
                grupos_px[nome_grupo] = {
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
            st_lig = stats_lig_px.get(c.id, {})
            status_contato_mensal = montar_status_contato_mensal_por_data(st_lig.get("ultima_ligacao"))
            dias_sem = (agora_px - c.ultimo_pedido_oracle).days if c.ultimo_pedido_oracle else 0
            data_prevista_inativacao = (c.ultimo_pedido_oracle + timedelta(days=181)) if c.ultimo_pedido_oracle else None
            grupos_px[nome_grupo]["clientes"].append(
                {
                    "id": c.id,
                    "nome": c.nome,
                    "cnpj": c.cnpj,
                    "telefone": c.telefone,
                    "telefone2": c.telefone2,
                    "representante_nome": c.representante_oracle or c.representante_nome or "",
                    "representante_oracle": c.representante_oracle or "",
                    "ultima_ligacao": st_lig.get("ultima_ligacao"),
                    "ultima_ligacao_por": None,
                    "total_ligacoes": st_lig.get("total_ligacoes", 0),
                    "especial_contato_mensal_status": status_contato_mensal["status"],
                    "especial_contato_mensal_label": status_contato_mensal["label"],
                    "proxima_ligacao": c.proxima_ligacao,
                    "origem": c.origem,
                    "cd_cliente_oracle": c.cd_cliente_oracle,
                    "categoria_consultor": c.categoria_consultor or "",
                    "centralizadora": "",
                    "consultor_id": c.consultor_id,
                    "conceito": c.conceito or "",
                    "municipio": c.municipio or "",
                    "uf": c.uf or "",
                    "contato": c.contato or "",
                    "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
                    "valor_ultimo_pedido": c.valor_ultimo_pedido,
                    "valor_total_365dias": c.valor_total_365dias or 0,
                    "meses_com_compra": meses_com_compra,
                    "meses_total_periodo": meses_total_periodo,
                    "situacao_ultimo_pedido": c.situacao_ultimo_pedido or "",
                    "em_atendimento_ativo": bool(
                        c.em_atendimento_por and c.em_atendimento_ate and c.em_atendimento_ate > datetime.now()
                    ),
                    "em_atendimento_por_nome": None,
                    "em_atendimento_ate": (
                        c.em_atendimento_ate.strftime("%d/%m/%Y %H:%M")
                        if c.em_atendimento_ate and c.em_atendimento_ate > datetime.now()
                        else None
                    ),
                    "dias_sem_pedido": dias_sem,
                    "dias_para_inativar": max(0, 181 - dias_sem),
                    "data_prevista_inativacao": data_prevista_inativacao,
                    "pagamento_medio_dias": pagamento_medio_por_cd.get(str(c.cd_cliente_oracle or "").strip()),
                }
            )

        todos_clientes_payload = [
            cliente
            for dados in grupos_px.values()
            for cliente in dados["clientes"]
        ]
        periodo_continuidade = periodo_recencia if agrupar_por == "recencia" else "ano_atual"
        mapa_meses_continuidade = mapa_meses_compra if periodo_continuidade == periodo_recencia else None
        enriquecer_payloads_com_continuidade_compra(
            todos_clientes_payload,
            periodo=periodo_continuidade,
            mapa_meses=mapa_meses_continuidade,
        )
        for dados in grupos_px.values():
            cls = dados["clientes"]
            dados["total_clientes"] = len(cls)
            dados["liberados"] = sum(1 for c in cls if c.get("conceito") == "LIBERADO")
            dados["inadimplentes"] = sum(1 for c in cls if c.get("conceito") == "INADIMPLENTE")
            dados["sem_conceito"] = sum(1 for c in cls if c.get("conceito") in ("", "SEM CONCEITO", None))
            vals = [c.get("valor_ultimo_pedido", 0) for c in cls if c.get("valor_ultimo_pedido")]
            dados["ticket_medio"] = sum(vals) / len(vals) if vals else 0
            dias_list = [c.get("dias_sem_pedido", 0) for c in cls if c.get("dias_sem_pedido")]
            dados["dias_medio"] = sum(dias_list) / len(dias_list) if dias_list else 0
            if agrupar_por == "recencia":
                dados["clientes"] = ordenar_clientes_recencia_frequencia(cls)
            else:
                dados["clientes"] = sorted(cls, key=lambda x: -x.get("dias_sem_pedido", 0))

        if agrupar_por == "recencia":
            representantes_ordenados_px = [(NOME_GRUPO_RECENCIA_LIVRE, grupos_px[NOME_GRUPO_RECENCIA_LIVRE])] if NOME_GRUPO_RECENCIA_LIVRE in grupos_px else []
        else:
            representantes_ordenados_px = sorted(
                grupos_px.items(),
                key=lambda x: (
                    -x[1]["total_clientes"],
                    x[0] == ("SEM UF" if agrupar_por == "uf" else "SEM REPRESENTANTE"),
                    x[0],
                ),
            )
        total_proximos_count = sum(len(d["clientes"]) for d in grupos_px.values())
        total_lib_px = sum(d["liberados"] for d in grupos_px.values())
        total_inad_px = sum(d["inadimplentes"] for d in grupos_px.values())
        total_sc_px = sum(d["sem_conceito"] for d in grupos_px.values())
        todos_vals_px = [c.get("valor_ultimo_pedido") for _, d in grupos_px.items() for c in d["clientes"] if c.get("valor_ultimo_pedido")]
        todos_dias_px = [c.get("dias_sem_pedido", 0) for _, d in grupos_px.items() for c in d["clientes"]]
        stats_proximos = {
            "liberados": total_lib_px,
            "inadimplentes": total_inad_px,
            "sem_conceito": total_sc_px,
            "ticket_medio": sum(todos_vals_px) / len(todos_vals_px) if todos_vals_px else 0,
            "dias_sem_pedido": int((sum(todos_dias_px) / len(todos_dias_px))) if todos_dias_px else 0,
            "perc_liberados": round((total_lib_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
            "perc_inadimplentes": round((total_inad_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
            "perc_sem_conceito": round((total_sc_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
        }
        _log_perf("projecao_representante_build", perf_total, total=total_proximos_count)
        return {
            "representantes_ordenados": representantes_ordenados_px,
            "total": total_proximos_count,
            "stats": stats_proximos,
        }

    projecao = carregar_ou_gerar_projecao_representante(
        codigo_representante=str(current_user.codigo_representante or ""),
        carteira="proximos_inativacao",
        agrupar_por=agrupar_por,
        periodo_recencia=periodo_recencia,
        gerador=_gerar,
    )
    representantes_ordenados_px = [
        (str(grupo[0]), dict(grupo[1]))
        for grupo in (projecao.get("representantes_ordenados") or [])
        if isinstance(grupo, (list, tuple)) and len(grupo) >= 2
    ]
    if incluir_pedido_em_andamento:
        todos_clientes_payload = [
            cliente
            for _, dados in representantes_ordenados_px
            for cliente in dados.get("clientes", [])
        ]
        marcar_pedido_em_andamento_payloads(todos_clientes_payload)
    return (
        representantes_ordenados_px,
        int(projecao.get("total") or 0),
        dict(projecao.get("stats") or {}),
    )
