from datetime import datetime, timedelta

from flask import render_template
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.models import Cliente, Ligacao, Usuario
from routes.clientes_ligacoes.agrupamento_view import montar_representantes_agrupados
from routes.clientes_ligacoes.badges import calcular_total_inativos_badge_com_cache
from routes.clientes_ligacoes.consultor_mapping import (
    carregar_mapa_nome_para_id_usuarios_ativos,
    construir_mapa_codigo_para_id,
)
from routes.clientes_ligacoes.dashboard_operacional import (
    montar_meses_disponiveis,
    montar_stats_consultor_televendas,
    parse_filtro_mes_ano,
)
from routes.clientes_ligacoes.domain_utils import (
    _codigo_representante_de_texto,
    _normalizar_codigo_representante,
    _resolver_consultor_id_por_categoria,
)
from routes.clientes_ligacoes.lista_operacional import filtrar_listas_por_termo, ordenar_clientes_por_aba
from routes.supervisor_routes import get_banners_ativos


def render_fluxo_operacional(
    *,
    request,
    current_user,
    aba: str,
    total_oracle_badge: int,
    total_proximos_badge: int,
    apenas_meus: bool,
    codigos_representantes_vinculados,
    cache_store,
    cache_ttl_seconds: int,
):
    # Parametros de filtro mensal para consultores e televendas
    mes_filtro, ano_filtro = parse_filtro_mes_ano(request.args, current_user.tipo)

    q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
    if current_user.tipo == "televendas":
        clientes_ligados_por_tv = (
            db.session.query(Ligacao.cliente_id)
            .filter(Ligacao.consultor_id == current_user.id)
            .distinct()
        )
        q = q.filter(
            or_(
                Cliente.consultor_id == current_user.id,
                Cliente.id.in_(clientes_ligados_por_tv),
            )
        )
    elif apenas_meus:
        q = q.filter(Cliente.consultor_id == current_user.id)

    termo = request.args.get("q", "").strip()
    clientes_todos = q.order_by(Cliente.nome.asc()).all()

    pendentes, contatados, precisa_retornar = [], [], []
    agora = datetime.now()
    limite_min_90_120 = agora - timedelta(days=120)
    limite_max_90_120 = agora - timedelta(days=90)
    filtrar_por_categoria_consultor = current_user.tipo == "consultor"
    ajustar_consultor_supervisor_pendentes = (current_user.tipo == "supervisor" and aba == "pendentes")
    mapa_nome_para_id = {}
    mapa_codigo_para_id = {}
    ids_usuarios_ativos = set()
    if filtrar_por_categoria_consultor or ajustar_consultor_supervisor_pendentes:
        usuarios_ativos, mapa_nome_para_id = carregar_mapa_nome_para_id_usuarios_ativos()
        ids_usuarios_ativos = {u.id for u in usuarios_ativos if u and u.id}
        mapa_codigo_para_id = construir_mapa_codigo_para_id(mapa_nome_para_id)

    for c in clientes_todos:
        if current_user.tipo == "supervisor_repr":
            codigo_rep_cliente = _normalizar_codigo_representante(
                _codigo_representante_de_texto(c.representante_oracle or c.representante_nome)
            )
            if not codigo_rep_cliente or codigo_rep_cliente not in codigos_representantes_vinculados:
                continue

        ligacoes_relevantes = (
            [l for l in c.ligacoes if l.consultor_id == current_user.id]
            if current_user.tipo in ("consultor", "televendas")
            else list(c.ligacoes)
        )
        ligs = sorted(ligacoes_relevantes, key=lambda x: x.data_hora, reverse=True)
        ultima = ligs[0] if ligs else None
        total = len(ligs)
        origem_cliente = str(getattr(c, "origem", "") or "").strip().lower()
        consultor_id_view = c.consultor_id
        if ajustar_consultor_supervisor_pendentes and c.cd_cliente_oracle and c.categoria_consultor:
            consultor_esperado = _resolver_consultor_id_por_categoria(
                c.categoria_consultor,
                mapa_codigo_para_id=mapa_codigo_para_id,
                mapa_nome_para_id=mapa_nome_para_id,
            )
            if consultor_esperado:
                consultor_id_view = consultor_esperado
        if ajustar_consultor_supervisor_pendentes and c.consultor_id:
            if c.consultor_id not in ids_usuarios_ativos:
                consultor_id_view = consultor_id_view if consultor_id_view != c.consultor_id else None
        dados = {
            "id": c.id,
            "nome": c.nome,
            "cnpj": c.cnpj,
            "telefone": c.telefone,
            "telefone2": c.telefone2,
            "representante_nome": (c.representante_oracle or c.representante_nome),
            "representante_oracle": c.representante_oracle or "",
            "ultima_ligacao": ultima.data_hora if ultima else None,
            "ultima_ligacao_por": None,
            "total_ligacoes": total,
            "proxima_ligacao": c.proxima_ligacao,
            "origem": getattr(c, "origem", None),
            "valor_total_365dias": c.valor_total_365dias,
            "valor_ultimo_pedido": c.valor_ultimo_pedido,
            "cd_cliente_oracle": c.cd_cliente_oracle,
            "categoria_consultor": c.categoria_consultor or "",
            "centralizadora": "",
            "consultor_id": consultor_id_view,
            "conceito": c.conceito or "",
            "municipio": c.municipio or "",
            "uf": c.uf or "",
            "contato": c.contato or "",
            "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
            "situacao_ultimo_pedido": c.situacao_ultimo_pedido or "",
            "em_atendimento_ativo": bool(c.em_atendimento_por),
            "em_atendimento_por_nome": None,
            "em_atendimento_ate": None,
        }

        if (
            filtrar_por_categoria_consultor
            and c.cd_cliente_oracle
            and c.categoria_consultor
            and origem_cliente != "manual"
        ):
            consultor_esperado = _resolver_consultor_id_por_categoria(
                c.categoria_consultor,
                mapa_codigo_para_id=mapa_codigo_para_id,
                mapa_nome_para_id=mapa_nome_para_id,
            )
            if consultor_esperado and consultor_esperado != current_user.id:
                continue

        # Regra de negocio: para consultor, cliente manual pertence a
        # "Clientes Especiais" (antiga aba Pendentes), mesmo com historico.
        if current_user.tipo in ("consultor", "supervisor_repr") and origem_cliente == "manual":
            pendentes.append(dados)
            continue

        if total == 0:
            # Evita misturar campanha 90-120d na aba operacional de pendentes
            # e no badge "Clientes Especiais" do consultor.
            if (
                current_user.tipo in ("consultor", "supervisor_repr")
                and c.cd_cliente_oracle
                and c.ultimo_pedido_oracle
                and limite_min_90_120 <= c.ultimo_pedido_oracle <= limite_max_90_120
            ):
                continue
            pendentes.append(dados)
        else:
            if c.proxima_ligacao or (ultima and ultima.resultado == "retornar"):
                dados["retorno_atrasado"] = bool(c.proxima_ligacao and (agora >= c.proxima_ligacao))
                precisa_retornar.append(dados)
            else:
                contatados.append(dados)

    total_pendentes_badge = len(pendentes)
    total_contatados_badge = len(contatados)
    total_retornar_badge = len(precisa_retornar)

    # Busca textual so na listagem atual (nao afeta badges).
    pendentes_view, contatados_view, precisa_retornar_view = filtrar_listas_por_termo(
        termo,
        pendentes,
        contatados,
        precisa_retornar,
    )
    clientes = ordenar_clientes_por_aba(
        aba,
        pendentes_view,
        contatados_view,
        precisa_retornar_view,
        request.args.get("filtro"),
    )

    consultores = (
        Usuario.query
        .filter_by(tipo="consultor", ativo=True)
        .order_by(Usuario.nome.asc())
        .all() if current_user.tipo == "supervisor" else None
    )

    stats = montar_stats_consultor_televendas(current_user, total_oracle_badge)

    # Gerar lista de meses/anos disponiveis para o filtro do consultor e televendas
    meses_disponiveis_consultor = montar_meses_disponiveis(current_user.tipo)

    total_inativos_badge = calcular_total_inativos_badge_com_cache(
        current_user=current_user,
        apenas_meus=apenas_meus,
        cache_store=cache_store,
        cache_ttl_seconds=cache_ttl_seconds,
    )

    # Para consultores: converter para vista agrupada por representante
    # (mantendo contatados/retornar na lista simples original).
    if (
        (current_user.tipo in ("supervisor", "consultor") and aba == "pendentes")
        or (
            current_user.tipo in ("consultor", "supervisor", "supervisor_repr")
            and aba not in ("contatados", "retornar", "pendentes")
        )
    ):
        representantes_ordenados_grp = montar_representantes_agrupados(
            clientes=clientes,
            tipo_usuario=current_user.tipo,
            aba=aba,
        )

        return render_template(
            "meus_clientes.html",
            representantes=representantes_ordenados_grp,
            usar_vista_agrupada=True,
            aba=aba,
            total_pendentes=total_pendentes_badge,
            total_contatados=total_contatados_badge,
            total_retornar=total_retornar_badge,
            total_inativos=total_inativos_badge,
            total_oracle=total_oracle_badge,
            total_proximos=total_proximos_badge,
            is_supervisor=(current_user.tipo == "supervisor"),
            now=datetime.now,
            stats=stats,
            mostrar_novidades=not current_user.viu_novidades,
            banners_ativos=get_banners_ativos(),
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis_consultor=meses_disponiveis_consultor,
        )

    return render_template(
        "meus_clientes.html",
        clientes=clientes,
        total_pendentes=total_pendentes_badge,
        total_contatados=total_contatados_badge,
        total_retornar=total_retornar_badge,
        total_inativos=total_inativos_badge,
        total_oracle=total_oracle_badge,
        total_proximos=total_proximos_badge,
        aba=aba,
        is_supervisor=(current_user.tipo == "supervisor"),
        now=datetime.now,
        consultores=consultores,
        stats=stats,
        mostrar_novidades=not current_user.viu_novidades,
        banners_ativos=get_banners_ativos(),
        mes_filtro=mes_filtro,
        ano_filtro=ano_filtro,
        meses_disponiveis_consultor=meses_disponiveis_consultor,
    )
