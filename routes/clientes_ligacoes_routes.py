from datetime import datetime, timedelta

import pandas as pd
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, case, desc, extract, func, or_, text
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.helpers import _percent, formatar_dinheiro, get_pos, s, so_digits
from core.models import Cliente, Ligacao, Nota, SyncResumoDiario, Usuario
from routes.clientes_ligacoes.access_control import bloquear_escrita_supervisor_repr
from routes.clientes_ligacoes.badges import (
    _total_inativos_badge,
    _total_oracle_badge,
    _total_oracle_badge_supervisor_repr,
    _total_proximos_badge,
)
from routes.clientes_ligacoes.consultor_mapping import construir_mapa_codigo_para_id
from routes.clientes_ligacoes.domain_utils import (
    _cliente_tem_representante_vinculado,
    _codigo_representante_de_texto,
    _extrair_nome_oracle_consultor,
    _normalizar_codigo_representante,
    _normalizar_nome_consultor,
    _resolver_consultor_id_por_categoria,
)
from routes.clientes_ligacoes.supervisor_repr import (
    contar_proximos_inativacao_supervisor_repr,
    obter_codigos_representantes_vinculados,
)
from routes.clientes_ligacoes.oracle_tab import carregar_clientes_oracle_deduplicados
from routes.supervisor_routes import get_banners_ativos

_INATIVOS_COUNT_CACHE = {}
_INATIVOS_COUNT_CACHE_TTL_SECONDS = 600


def register_clientes_ligacoes_routes(app):
    @app.before_request
    def _bloquear_escrita_supervisor_repr_clientes():
        return bloquear_escrita_supervisor_repr()

    # =============================================================================
    # LISTAGEM DE CLIENTES
    # =============================================================================
    @app.route('/meus-clientes')
    def meus_clientes():
        if not current_user.is_authenticated:
            return redirect(url_for('login'))

        if current_user.tipo not in ('consultor', 'supervisor', 'televendas', 'supervisor_repr'):
            flash('Perfil sem acesso.', 'danger')
            return redirect(url_for('index'))

        if current_user.tipo == 'televendas':
            aba_padrao = 'inativos'
        elif current_user.tipo == 'supervisor_repr':
            aba_padrao = 'oracle'
        else:
            aba_padrao = 'pendentes'
        aba = request.args.get('aba', aba_padrao)
        total_oracle_badge = _total_oracle_badge() if current_user.tipo != 'televendas' else 0
        total_proximos_badge = _total_proximos_badge(
            current_user.id if current_user.tipo in ('consultor', 'televendas') else None
        )
        # Inativos e uma aba exclusiva de televendas e supervisor.
        if current_user.tipo not in ('televendas', 'supervisor') and aba == 'inativos':
            aba_destino = 'oracle' if current_user.tipo == 'supervisor_repr' else 'pendentes'
            return redirect(url_for('meus_clientes', aba=aba_destino))
        # Televendas não pode acessar pendentes/oracle/proximos_inativacao
        if current_user.tipo == 'televendas' and aba in ('pendentes', 'oracle', 'proximos_inativacao'):
            return redirect(url_for('meus_clientes', aba='inativos'))
        # Supervisor de representante não acessa a aba pendentes/clientes especiais
        if current_user.tipo == 'supervisor_repr' and aba in ('pendentes', 'contatados', 'retornar'):
            return redirect(url_for('meus_clientes', aba='oracle'))
        
        apenas_meus = True if current_user.tipo in ('consultor', 'televendas') else (request.args.get('meus') == '1')
        
        # Buscar códigos de representantes vinculados ao supervisor_repr
        codigos_representantes_vinculados = []
        if current_user.tipo == 'supervisor_repr':
            codigos_representantes_vinculados = obter_codigos_representantes_vinculados(current_user.id)
            if not codigos_representantes_vinculados:
                flash('Nenhum representante vinculado a este supervisor. Entre em contato com o administrador.', 'warning')

            total_proximos_badge = contar_proximos_inativacao_supervisor_repr(
                codigos_representantes_vinculados
            )
            total_oracle_badge = _total_oracle_badge_supervisor_repr(
                codigos_representantes_vinculados
            )
        
        # Tratar aba Oracle
        if aba == 'oracle':
            # REGRA VALIDADA (2026-03): usar Oracle como fonte de verdade da lista 90-120d.
            # Nao voltar para filtro principal via MySQL local.
            periodo_oracle = request.args.get('periodo_oracle')
            conceito_filtro = (request.args.get('conceito_filtro') or '').strip().upper()
            consultor_filtro = (request.args.get('consultor_filtro') or '').strip()
            termo = (request.args.get('q') or '').strip().lower()
            clientes_oracle = carregar_clientes_oracle_deduplicados(app.logger, periodo_oracle)

            codigos_oracle = [
                str(c.get('cd_cliente')).strip()
                for c in clientes_oracle
                if c.get('cd_cliente')
            ]

            clientes_locais_por_cd = {}
            stats_ligacoes_por_cliente_id = {}
            locks_por_cliente_id = {}
            filtrar_oracle_por_categoria = (current_user.tipo == 'consultor')
            mapa_nome_para_id_oracle = {}
            mapa_codigo_para_id_oracle = {}
            if filtrar_oracle_por_categoria:
                usuarios_ativos = Usuario.query.filter(
                    Usuario.ativo == True,
                    Usuario.tipo.in_(["consultor", "televendas", "supervisor"])
                ).all()
                mapa_nome_para_id_oracle = {
                    _normalizar_nome_consultor(u.nome): u.id
                    for u in usuarios_ativos if u and u.nome
                }
                mapa_codigo_para_id_oracle = construir_mapa_codigo_para_id(mapa_nome_para_id_oracle)
            if codigos_oracle:
                clientes_locais = (
                    Cliente.query
                    .filter(
                        Cliente.cd_cliente_oracle.in_(codigos_oracle),
                        Cliente.ativo == True
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
                            Cliente.id.label('cliente_id'),
                            Cliente.em_atendimento_ate,
                            Usuario.nome.label('usuario_nome')
                        )
                        .outerjoin(Usuario, Usuario.id == Cliente.em_atendimento_por)
                        .filter(
                            Cliente.id.in_(ids_locais),
                            Cliente.em_atendimento_por.isnot(None),
                        )
                        .all()
                    )
                    locks_por_cliente_id = {
                        int(row.cliente_id): {
                            'ativo': True,
                            'por_nome': (row.usuario_nome or 'Outro usuario'),
                            'ate': None,
                        }
                        for row in locks_rows
                    }
                    ligacoes_agg = (
                        db.session.query(
                            Ligacao.cliente_id,
                            func.count(Ligacao.id).label('total_ligacoes'),
                            func.max(Ligacao.data_hora).label('ultima_ligacao')
                        )
                        .filter(Ligacao.cliente_id.in_(ids_locais))
                        .group_by(Ligacao.cliente_id)
                        .all()
                    )
                    stats_ligacoes_por_cliente_id = {
                        row.cliente_id: {
                            'total_ligacoes': int(row.total_ligacoes or 0),
                            'ultima_ligacao': row.ultima_ligacao,
                        }
                        for row in ligacoes_agg
                    }
                    ultimas_ligacoes = (
                        db.session.query(
                            Ligacao.cliente_id,
                            Ligacao.data_hora,
                            Usuario.nome.label('ligador_nome')
                        )
                        .join(Usuario, Usuario.id == Ligacao.consultor_id)
                        .filter(Ligacao.cliente_id.in_(ids_locais))
                        .order_by(Ligacao.cliente_id.asc(), Ligacao.data_hora.desc(), Ligacao.id.desc())
                        .all()
                    )
                    vistos_ligador = set()
                    for row in ultimas_ligacoes:
                        if row.cliente_id in vistos_ligador:
                            continue
                        vistos_ligador.add(row.cliente_id)
                        if row.cliente_id not in stats_ligacoes_por_cliente_id:
                            stats_ligacoes_por_cliente_id[row.cliente_id] = {
                                'total_ligacoes': 0,
                                'ultima_ligacao': row.data_hora,
                            }
                        stats_ligacoes_por_cliente_id[row.cliente_id]['ultima_ligacao_por'] = row.ligador_nome

            representantes_data = {}
            for cliente_oracle in clientes_oracle:
                conceito_cliente = (str(cliente_oracle.get('conceito') or '').strip().upper())
                consultor_cliente = (str(cliente_oracle.get('consultor') or '').strip())

                if conceito_filtro:
                    if conceito_filtro in ('SEM_CONCEITO', 'SEM CONCEITO'):
                        if conceito_cliente not in ('', 'SEM CONCEITO'):
                            continue
                    elif conceito_cliente != conceito_filtro:
                        continue

                if consultor_filtro and consultor_filtro.lower() not in consultor_cliente.lower():
                    continue

                if termo:
                    base_busca = ' '.join([
                        str(cliente_oracle.get('cliente') or ''),
                        str(cliente_oracle.get('cnpj') or ''),
                        str(cliente_oracle.get('telefone1') or ''),
                        str(cliente_oracle.get('telefone2') or ''),
                        str(cliente_oracle.get('representante') or ''),
                        str(cliente_oracle.get('consultor') or ''),
                        str(cliente_oracle.get('cd_centralizado') or ''),
                        str(cliente_oracle.get('nome_centralizadora') or ''),
                        str(cliente_oracle.get('conceito') or ''),
                        str(cliente_oracle.get('municipio') or ''),
                        str(cliente_oracle.get('uf') or ''),
                    ]).lower()
                    if termo not in base_busca:
                        continue

                cd_cliente = str(cliente_oracle.get('cd_cliente') or '').strip()
                cliente_local = clientes_locais_por_cd.get(cd_cliente) if cd_cliente else None

                # Filtro para supervisor_repr: apenas clientes dos representantes vinculados
                if current_user.tipo == 'supervisor_repr':
                    representante_str = str(cliente_oracle.get('representante') or '')
                    cd_representante = _normalizar_codigo_representante(
                        _codigo_representante_de_texto(representante_str)
                    )
                    if not cd_representante or cd_representante not in codigos_representantes_vinculados:
                        continue

                if apenas_meus and current_user.tipo != 'supervisor_repr':
                    if not cliente_local or cliente_local.consultor_id != current_user.id:
                        continue
                if filtrar_oracle_por_categoria and consultor_cliente:
                    consultor_esperado = _resolver_consultor_id_por_categoria(
                        consultor_cliente,
                        mapa_codigo_para_id=mapa_codigo_para_id_oracle,
                        mapa_nome_para_id=mapa_nome_para_id_oracle,
                    )
                    if consultor_esperado and consultor_esperado != current_user.id:
                        continue

                stats_lig = (
                    stats_ligacoes_por_cliente_id.get(cliente_local.id, {})
                    if cliente_local and cliente_local.id else {}
                )
                lock_info = (
                    locks_por_cliente_id.get(cliente_local.id, {})
                    if cliente_local and cliente_local.id else {}
                )
                ultima_local = stats_lig.get('ultima_ligacao')
                total_ligacoes_local = stats_lig.get('total_ligacoes', 0)

                representante = (str(cliente_oracle.get('representante') or '').strip()) or 'SEM REPRESENTANTE'

                if representante not in representantes_data:
                    representantes_data[representante] = {
                        'nome': representante,
                        'clientes': [],
                        'total_clientes': 0,
                        'liberados': 0,
                        'inadimplentes': 0,
                        'sem_conceito': 0,
                        'ticket_medio': 0,
                        'dias_medio': 0,
                        'consultores_internos': {}
                    }

                dados_cliente = {
                    "id": cliente_local.id if cliente_local else None,
                    "nome": cliente_oracle.get('cliente', ''),
                    "cnpj": cliente_oracle.get('cnpj', ''),
                    "telefone": (cliente_local.telefone if cliente_local and cliente_local.telefone else (cliente_oracle.get('telefone1') or cliente_oracle.get('telefone2'))),
                    "telefone2": (cliente_local.telefone2 if cliente_local else cliente_oracle.get('telefone2')),
                    "representante_nome": cliente_oracle.get('representante', 'SEM REPRESENTANTE'),
                    "ultima_ligacao": ultima_local,
                    "ultima_ligacao_por": stats_lig.get('ultima_ligacao_por'),
                    "total_ligacoes": total_ligacoes_local,
                    "proxima_ligacao": (cliente_local.proxima_ligacao if cliente_local else None),
                    "origem": (getattr(cliente_local, 'origem', None) if cliente_local else 'oracle'),
                    "cd_cliente_oracle": cliente_oracle.get('cd_cliente'),
                    "categoria_consultor": cliente_oracle.get('consultor', ''),
                    "centralizadora": (
                        f"{cliente_oracle.get('cd_centralizado')} - {cliente_oracle.get('nome_centralizadora')}"
                        if cliente_oracle.get('cd_centralizado') and cliente_oracle.get('nome_centralizadora')
                        else (str(cliente_oracle.get('cd_centralizado') or '').strip() or '')
                    ),
                    "consultor_id": (cliente_local.consultor_id if cliente_local else None),
                    "conceito": cliente_oracle.get('conceito', ''),
                    "municipio": cliente_oracle.get('municipio', ''),
                    "uf": cliente_oracle.get('uf', ''),
                    "contato": cliente_oracle.get('contato', ''),
                    "ultimo_pedido_oracle": cliente_oracle.get('dt_pedido'),
                    "valor_ultimo_pedido": cliente_oracle.get('total_pedido'),
                    "valor_total_365dias": (cliente_local.valor_total_365dias if cliente_local else 0),
                    "situacao_ultimo_pedido": cliente_oracle.get('situacao', ''),
                    "representante_oracle": cliente_oracle.get('representante', 'SEM REPRESENTANTE'),
                    "em_atendimento_ativo": bool(lock_info.get('ativo')),
                    "em_atendimento_por_nome": lock_info.get('por_nome'),
                    "em_atendimento_ate": lock_info.get('ate'),
                }

                representantes_data[representante]['clientes'].append(dados_cliente)

                if cliente_local and cliente_local.consultor:
                    nome_consultor = cliente_local.consultor.nome
                    reps = representantes_data[representante]['consultores_internos']
                    if nome_consultor not in reps:
                        reps[nome_consultor] = 0
                    reps[nome_consultor] += 1

            for representante, dados in representantes_data.items():
                clientes_rep = dados['clientes']
                dados['total_clientes'] = len(clientes_rep)
                dados['liberados'] = sum(1 for c in clientes_rep if c.get('conceito') == 'LIBERADO')
                dados['inadimplentes'] = sum(1 for c in clientes_rep if c.get('conceito') == 'INADIMPLENTE')
                dados['sem_conceito'] = sum(1 for c in clientes_rep if c.get('conceito') in ['SEM CONCEITO', None])

                valores = [c.get('valor_ultimo_pedido', 0) for c in clientes_rep if c.get('valor_ultimo_pedido')]
                dados['ticket_medio'] = sum(valores) / len(valores) if valores else 0

                hoje = datetime.now()
                dias_sem_pedido = []
                for c in clientes_rep:
                    if c.get('ultimo_pedido_oracle'):
                        dias = (hoje - c['ultimo_pedido_oracle']).days
                        dias_sem_pedido.append(dias)
                dados['dias_medio'] = sum(dias_sem_pedido) / len(dias_sem_pedido) if dias_sem_pedido else 0

                dados['clientes'] = sorted(
                    clientes_rep,
                    key=lambda x: (
                        float(x.get('valor_total_365dias') or 0),
                        float(x.get('valor_ultimo_pedido') or 0)
                    ),
                    reverse=True
                )

            representantes_ordenados = sorted(
                representantes_data.items(),
                key=lambda x: (-x[1]['total_clientes'], x[0] == 'SEM REPRESENTANTE', x[0])
            )

            consultores_oracle = []
            if representantes_data:
                consultores_set = set()
                for _, dados in representantes_data.items():
                    for c in dados['clientes']:
                        if c.get('categoria_consultor'):
                            consultores_set.add(c.get('categoria_consultor'))
                for nome in sorted(consultores_set):
                    consultores_oracle.append({'nome': nome})

            todos_clientes = Cliente.query.filter_by(ativo=True)
            if apenas_meus:
                todos_clientes = todos_clientes.filter(Cliente.consultor_id == current_user.id)

            base_pendentes = todos_clientes.filter(Cliente.id.notin_(
                db.session.query(Ligacao.cliente_id).filter(
                    Ligacao.consultor_id == current_user.id if apenas_meus else True
                )
            ))
            if current_user.tipo == 'consultor':
                # Mantém "Clientes Especiais" consistente em todas as abas:
                # para consultor, remove da contagem operacional a campanha 90-120d.
                limite_min_90_120 = datetime.now() - timedelta(days=120)
                limite_max_90_120 = datetime.now() - timedelta(days=90)
                base_pendentes = base_pendentes.filter(~and_(
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_min_90_120, limite_max_90_120),
                ))
            total_pendentes = base_pendentes.count()

            total_contatados = todos_clientes.filter(Cliente.id.in_(
                db.session.query(Ligacao.cliente_id).filter(
                    Ligacao.consultor_id == current_user.id if apenas_meus else True
                )
            )).filter(Cliente.proxima_ligacao.is_(None)).count()

            total_retornar = todos_clientes.filter(Cliente.proxima_ligacao.isnot(None)).count()
            total_oracle = sum(len(dados['clientes']) for dados in representantes_data.values())

            total_clientes_oracle = 0
            total_liberados = 0
            total_inadimplentes = 0
            total_sem_conceito = 0
            todos_valores = []
            todos_dias = []

            for _, dados in representantes_data.items():
                clientes_rep = dados['clientes']
                total_clientes_oracle += len(clientes_rep)
                total_liberados += dados['liberados']
                total_inadimplentes += dados['inadimplentes']
                total_sem_conceito += dados['sem_conceito']

                for c in clientes_rep:
                    if c.get('valor_ultimo_pedido'):
                        todos_valores.append(c.get('valor_ultimo_pedido'))
                    if c.get('ultimo_pedido_oracle'):
                        dias = (datetime.now() - c['ultimo_pedido_oracle']).days
                        todos_dias.append(dias)

            ticket_medio_geral = sum(todos_valores) / len(todos_valores) if todos_valores else 0
            dias_medio_geral = sum(todos_dias) / len(todos_dias) if todos_dias else 0

            stats_oracle = {
                'liberados': total_liberados,
                'inadimplentes': total_inadimplentes,
                'sem_conceito': total_sem_conceito,
                'ticket_medio': ticket_medio_geral,
                'dias_sem_pedido': int(dias_medio_geral),
                'perc_liberados': round((total_liberados / total_clientes_oracle) * 100, 1) if total_clientes_oracle > 0 else 0,
                'perc_inadimplentes': round((total_inadimplentes / total_clientes_oracle) * 100, 1) if total_clientes_oracle > 0 else 0,
                'perc_sem_conceito': round((total_sem_conceito / total_clientes_oracle) * 100, 1) if total_clientes_oracle > 0 else 0
            }

            return render_template('meus_clientes.html',
                                 representantes=representantes_ordenados,
                                 aba=aba,
                                 total_pendentes=total_pendentes,
                                 total_contatados=total_contatados,
                                 total_retornar=total_retornar,
                                 total_oracle=total_oracle,
                                 total_inativos=0,
                                 total_proximos=total_proximos_badge,
                                 usar_vista_agrupada=True,
                                 is_supervisor=current_user.tipo == 'supervisor',
                                 stats={},
                                 stats_oracle=stats_oracle,
                                 consultores_oracle=consultores_oracle,
                                 q=request.args.get('q', ''),
                                 meses_disponiveis_consultor=[],
                                 mes_filtro=None,
                                 ano_filtro=None)
        
        # Tratar aba Inativos (181 dias a 2 anos sem pedidos) - televendas e supervisor
        if aba == 'inativos':
            # REGRA VALIDADA (2026-03): lista de inativos vem da base local sincronizada diariamente.
            app.logger.info("=== INICIANDO TRATAMENTO ABA INATIVOS ===")
            app.logger.info(f"Usuário: {current_user.nome} ({current_user.tipo})")

            limite_max = datetime.now() - timedelta(days=181)
            limite_min = datetime.now() - timedelta(days=730)

            clientes_inativos_local = (
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_min, limite_max),
                )
                .all()
            )

            # Enriquecer centralizadora via Oracle para exibir na listagem de inativos.
            centralizadora_por_cd = {}
            try:
                from oracle_service import get_clientes_inativos_oracle as _get_clientes_inativos_oracle
                inativos_oracle_raw = _get_clientes_inativos_oracle() or []
                for row in inativos_oracle_raw:
                    cd = str(row.get('cd_cliente') or '').strip()
                    if not cd or cd in centralizadora_por_cd:
                        continue
                    centralizadora_por_cd[cd] = {
                        "cd_centralizado": row.get('cd_centralizado'),
                        "nome_centralizadora": row.get('nome_centralizadora'),
                    }
            except Exception as e:
                app.logger.warning(f"Falha ao enriquecer centralizadoras dos inativos via Oracle: {e}")

            clientes_oracle_inativos = [
                {
                    "cd_cliente": c.cd_cliente_oracle,
                    "cliente": c.nome,
                    "cnpj": c.cnpj,
                    "telefone1": c.telefone,
                    "telefone2": c.telefone2,
                    "representante": c.representante_oracle,
                    "consultor": c.categoria_consultor,
                    "conceito": c.conceito,
                    "municipio": c.municipio,
                    "uf": c.uf,
                    "contato": c.contato,
                    "dt_pedido": c.ultimo_pedido_oracle,
                    "total_pedido": c.valor_ultimo_pedido,
                    "situacao": c.situacao_ultimo_pedido,
                    "cd_centralizado": (
                        centralizadora_por_cd.get(str(c.cd_cliente_oracle).strip(), {}).get("cd_centralizado")
                        if c.cd_cliente_oracle else None
                    ),
                    "nome_centralizadora": (
                        centralizadora_por_cd.get(str(c.cd_cliente_oracle).strip(), {}).get("nome_centralizadora")
                        if c.cd_cliente_oracle else None
                    ),
                }
                for c in clientes_inativos_local
            ]
            app.logger.info(f"Buscados {len(clientes_oracle_inativos)} clientes inativos da base local sincronizada")
            filtrar_inativos_por_categoria = (current_user.tipo == 'consultor')
            mapa_nome_para_id_inativos = {}
            mapa_codigo_para_id_inativos = {}
            if filtrar_inativos_por_categoria:
                usuarios_ativos = Usuario.query.filter(
                    Usuario.ativo == True,
                    Usuario.tipo.in_(["consultor", "televendas", "supervisor"])
                ).all()
                mapa_nome_para_id_inativos = {
                    _normalizar_nome_consultor(u.nome): u.id
                    for u in usuarios_ativos if u and u.nome
                }
                mapa_codigo_para_id_inativos = construir_mapa_codigo_para_id(mapa_nome_para_id_inativos)

            conceito_filtro = (request.args.get('conceito_filtro') or '').strip().upper()
            consultor_filtro = (request.args.get('consultor_filtro') or '').strip()
            termo = (request.args.get('q') or '').strip().lower()

            # Para televendas, garantir ID local para habilitar a mesma lógica
            # das outras abas (detalhes, histórico e registro de ligação).
            codigos_inativos = [
                str(c.get('cd_cliente')).strip()
                for c in clientes_oracle_inativos
                if c.get('cd_cliente')
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
                        Cliente.ativo == True
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
                            Cliente.id.label('cliente_id'),
                            Cliente.cd_cliente_oracle.label('cd_cliente_oracle'),
                            Cliente.em_atendimento_ate,
                            Usuario.nome.label('usuario_nome')
                        )
                        .outerjoin(Usuario, Usuario.id == Cliente.em_atendimento_por)
                        .filter(
                            Cliente.id.in_(ids_locais),
                            Cliente.em_atendimento_por.isnot(None),
                        )
                        .all()
                    )
                    locks_por_cliente_id = {
                        int(row.cliente_id): {
                            'ativo': True,
                            'por_nome': (row.usuario_nome or 'Outro usuario'),
                            'ate': None,
                        }
                        for row in locks_rows
                    }
                    for row in locks_rows:
                        cd_lock = str(row.cd_cliente_oracle or '').strip()
                        if not cd_lock:
                            continue
                        if cd_lock not in locks_por_cd_oracle:
                            locks_por_cd_oracle[cd_lock] = {
                                'ativo': True,
                                'por_nome': (row.usuario_nome or 'Outro usuario'),
                                'ate': None,
                            }
                    ligacoes_agg = (
                        db.session.query(
                            Ligacao.cliente_id,
                            func.count(Ligacao.id).label('total_ligacoes'),
                            func.max(Ligacao.data_hora).label('ultima_ligacao')
                        )
                        .filter(Ligacao.cliente_id.in_(ids_locais))
                        .group_by(Ligacao.cliente_id)
                        .all()
                    )
                    stats_ligacoes_por_cliente_id = {
                        row.cliente_id: {
                            'total_ligacoes': int(row.total_ligacoes or 0),
                            'ultima_ligacao': row.ultima_ligacao,
                        }
                        for row in ligacoes_agg
                    }
                    ultimas_ligacoes = (
                        db.session.query(
                            Ligacao.cliente_id,
                            Ligacao.data_hora,
                            Usuario.nome.label('ligador_nome')
                        )
                        .join(Usuario, Usuario.id == Ligacao.consultor_id)
                        .filter(Ligacao.cliente_id.in_(ids_locais))
                        .order_by(Ligacao.cliente_id.asc(), Ligacao.data_hora.desc(), Ligacao.id.desc())
                        .all()
                    )
                    vistos_ligador = set()
                    for row in ultimas_ligacoes:
                        if row.cliente_id in vistos_ligador:
                            continue
                        vistos_ligador.add(row.cliente_id)
                        if row.cliente_id not in stats_ligacoes_por_cliente_id:
                            stats_ligacoes_por_cliente_id[row.cliente_id] = {
                                'total_ligacoes': 0,
                                'ultima_ligacao': row.data_hora,
                            }
                        stats_ligacoes_por_cliente_id[row.cliente_id]['ultima_ligacao_por'] = row.ligador_nome

            def normalizar_conceito(valor):
                return str(valor or '').strip().upper()

            # Agrupar por UF (somente inativos)
            representantes_data = {}
            for cliente_oracle in clientes_oracle_inativos:
                conceito_cliente = normalizar_conceito(cliente_oracle.get('conceito'))
                consultor_cliente = (str(cliente_oracle.get('consultor') or '').strip())

                if conceito_filtro:
                    if conceito_filtro in ('SEM_CONCEITO', 'SEM CONCEITO'):
                        if conceito_cliente not in ('', 'SEM CONCEITO'):
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
                    base_busca = " ".join([
                        str(cliente_oracle.get('cliente') or ''),
                        str(cliente_oracle.get('cnpj') or ''),
                        str(cliente_oracle.get('telefone1') or ''),
                        str(cliente_oracle.get('telefone2') or ''),
                        str(cliente_oracle.get('representante') or ''),
                        str(cliente_oracle.get('consultor') or ''),
                        str(cliente_oracle.get('conceito') or ''),
                        str(cliente_oracle.get('municipio') or ''),
                        str(cliente_oracle.get('uf') or ''),
                    ]).lower()
                    if termo not in base_busca:
                        continue

                cd_cliente = str(cliente_oracle.get('cd_cliente') or '').strip()
                cliente_local = clientes_locais_por_cd.get(cd_cliente) if cd_cliente else None
                
                # Filtro para supervisor_repr: apenas clientes dos representantes vinculados
                if current_user.tipo == 'supervisor_repr':
                    representante_str = str(cliente_oracle.get('representante') or '')
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
                ultima_local = stats_lig.get('ultima_ligacao')
                total_ligacoes_local = stats_lig.get('total_ligacoes', 0)

                # Fluxo operacional: apos primeiro contato, o cliente sai de "Inativos"
                # e passa a ser tratado nas abas "Contatados" ou "Retornar".
                if cliente_local and (total_ligacoes_local > 0 or cliente_local.proxima_ligacao is not None):
                    continue

                uf_grupo = (str(cliente_oracle.get('uf') or '').strip().upper()) or 'SEM UF'
                
                if uf_grupo not in representantes_data:
                    representantes_data[uf_grupo] = {
                        'nome': uf_grupo,
                        'clientes': [],
                        'total_clientes': 0,
                        'liberados': 0,
                        'inadimplentes': 0,
                        'sem_conceito': 0,
                        'ticket_medio': 0,
                        'dias_medio': 0,
                        'consultores_internos': {}
                    }
                
                dados_cliente = {
                    "id": cliente_local.id if cliente_local else None,
                    "nome": cliente_oracle.get('cliente', ''),
                    "cnpj": cliente_oracle.get('cnpj', ''),
                    "telefone": (cliente_local.telefone if cliente_local and cliente_local.telefone else (cliente_oracle.get('telefone1') or cliente_oracle.get('telefone2'))),
                    "telefone2": (cliente_local.telefone2 if cliente_local else cliente_oracle.get('telefone2')),
                    "representante_nome": cliente_oracle.get('representante', 'SEM REPRESENTANTE'),
                    "ultima_ligacao": ultima_local,
                    "ultima_ligacao_por": stats_lig.get('ultima_ligacao_por'),
                    "total_ligacoes": total_ligacoes_local,
                    "proxima_ligacao": (cliente_local.proxima_ligacao if cliente_local else None),
                    "origem": (getattr(cliente_local, 'origem', None) if cliente_local else 'oracle_inativos'),
                    "cd_cliente_oracle": cliente_oracle.get('cd_cliente'),
                    "categoria_consultor": cliente_oracle.get('consultor', ''),
                    "centralizadora": (
                        f"{cliente_oracle.get('cd_centralizado')} - {cliente_oracle.get('nome_centralizadora')}"
                        if cliente_oracle.get('cd_centralizado') and cliente_oracle.get('nome_centralizadora')
                        else (str(cliente_oracle.get('cd_centralizado') or '').strip() or '')
                    ),
                    "consultor_id": (cliente_local.consultor_id if cliente_local else None),
                    "conceito": conceito_cliente,
                    "municipio": cliente_oracle.get('municipio', ''),
                    "uf": cliente_oracle.get('uf', ''),
                    "contato": cliente_oracle.get('contato', ''),
                    "ultimo_pedido_oracle": cliente_oracle.get('dt_pedido'),
                    "valor_ultimo_pedido": cliente_oracle.get('total_pedido'),
                    "valor_total_365dias": (cliente_local.valor_total_365dias if cliente_local else 0),
                    "situacao_ultimo_pedido": cliente_oracle.get('situacao', ''),
                    "representante_oracle": cliente_oracle.get('representante', 'SEM REPRESENTANTE'),
                    "em_atendimento_ativo": bool(lock_info.get('ativo')),
                    "em_atendimento_por_nome": lock_info.get('por_nome'),
                    "em_atendimento_ate": lock_info.get('ate'),
                }
                
                representantes_data[uf_grupo]['clientes'].append(dados_cliente)
                if consultor_cliente:
                    consultores_uf = representantes_data[uf_grupo]['consultores_internos']
                    consultores_uf[consultor_cliente] = consultores_uf.get(consultor_cliente, 0) + 1
            
            # Calcular estatísticas
            for uf, dados in representantes_data.items():
                clientes_rep = dados['clientes']
                dados['total_clientes'] = len(clientes_rep)
                dados['liberados'] = sum(1 for c in clientes_rep if c.get('conceito') == 'LIBERADO')
                dados['inadimplentes'] = sum(1 for c in clientes_rep if c.get('conceito') == 'INADIMPLENTE')
                dados['sem_conceito'] = sum(
                    1
                    for c in clientes_rep
                    if c.get('conceito') in ('', 'SEM CONCEITO', None)
                )
                
                valores = [c.get('valor_ultimo_pedido', 0) for c in clientes_rep if c.get('valor_ultimo_pedido')]
                dados['ticket_medio'] = sum(valores) / len(valores) if valores else 0
                
                hoje = datetime.now()
                dias_sem_pedido = []
                for c in clientes_rep:
                    if c.get('ultimo_pedido_oracle'):
                        d = (hoje - c['ultimo_pedido_oracle']).days
                        dias_sem_pedido.append(d)
                dados['dias_medio'] = sum(dias_sem_pedido) / len(dias_sem_pedido) if dias_sem_pedido else 0

                # Ordenar clientes por maior valor (365d e fallback para ultimo pedido)
                dados['clientes'] = sorted(
                    clientes_rep,
                    key=lambda x: (
                        float(x.get('valor_total_365dias') or 0),
                        float(x.get('valor_ultimo_pedido') or 0)
                    ),
                    reverse=True
                )
            
            representantes_ordenados = sorted(
                representantes_data.items(),
                key=lambda x: (-x[1]['total_clientes'], x[0] == 'SEM UF', x[0])
            )
            
            consultores_inativos = []
            if representantes_data:
                consultores_set = set()
                for uf, dados in representantes_data.items():
                    for c in dados['clientes']:
                        if c.get('categoria_consultor'):
                            consultores_set.add(c.get('categoria_consultor'))
                for nome in sorted(consultores_set):
                    consultores_inativos.append({'nome': nome})
            
            total_inativos = sum(len(dados['clientes']) for dados in representantes_data.values())
            _INATIVOS_COUNT_CACHE[current_user.id] = {
                "count": total_inativos,
                "ts": datetime.now()
            }
            
            total_liberados = sum(d['liberados'] for d in representantes_data.values())
            total_inadimplentes = sum(d['inadimplentes'] for d in representantes_data.values())
            total_sem_conceito = sum(d['sem_conceito'] for d in representantes_data.values())
            todos_valores = []
            todos_dias = []
            for dados in representantes_data.values():
                for c in dados['clientes']:
                    if c.get('valor_ultimo_pedido'):
                        todos_valores.append(c.get('valor_ultimo_pedido'))
                    if c.get('ultimo_pedido_oracle'):
                        todos_dias.append((datetime.now() - c['ultimo_pedido_oracle']).days)
            
            ticket_medio_geral = sum(todos_valores) / len(todos_valores) if todos_valores else 0
            dias_medio_geral = sum(todos_dias) / len(todos_dias) if todos_dias else 0
            
            stats_inativos = {
                'liberados': total_liberados,
                'inadimplentes': total_inadimplentes,
                'sem_conceito': total_sem_conceito,
                'ticket_medio': ticket_medio_geral,
                'dias_sem_pedido': int(dias_medio_geral),
                'perc_liberados': round((total_liberados / total_inativos) * 100, 1) if total_inativos > 0 else 0,
                'perc_inadimplentes': round((total_inadimplentes / total_inativos) * 100, 1) if total_inativos > 0 else 0,
                'perc_sem_conceito': round((total_sem_conceito / total_inativos) * 100, 1) if total_inativos > 0 else 0
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
            if current_user.tipo == 'televendas':
                clientes_ligados_por_tv = (
                    db.session.query(Ligacao.cliente_id)
                    .filter(Ligacao.consultor_id == current_user.id)
                    .distinct()
                )
                base_tv = Cliente.query.filter(
                    Cliente.ativo == True
                ).filter(or_(
                    Cliente.consultor_id == current_user.id,
                    Cliente.id.in_(clientes_ligados_por_tv)
                ))
                total_retornar_tv = base_tv.filter(Cliente.proxima_ligacao.isnot(None)).count()
                total_contatados_tv = (
                    base_tv
                    .filter(Cliente.proxima_ligacao.is_(None))
                    .filter(Cliente.id.in_(clientes_ligados_por_tv))
                    .count()
                )

            # Painel simples de produtividade da equipe de televendas.
            stats_televendas = []
            hoje_date = datetime.now().date()
            desde7 = datetime.now() - timedelta(days=7)
            desde30 = datetime.now() - timedelta(days=30)
            equipe_tv = (
                Usuario.query
                .filter(Usuario.tipo == 'televendas', Usuario.ativo == True)
                .order_by(Usuario.nome.asc())
                .all()
            )
            for tv in equipe_tv:
                lig_hoje = (
                    db.session.query(func.count(Ligacao.id))
                    .filter(
                        Ligacao.consultor_id == tv.id,
                        func.date(Ligacao.data_hora) == hoje_date
                    )
                    .scalar() or 0
                )
                lig_semana = (
                    db.session.query(func.count(Ligacao.id))
                    .filter(
                        Ligacao.consultor_id == tv.id,
                        Ligacao.data_hora >= desde7
                    )
                    .scalar() or 0
                )
                lig_mes = (
                    db.session.query(func.count(Ligacao.id))
                    .filter(
                        Ligacao.consultor_id == tv.id,
                        Ligacao.data_hora >= desde30
                    )
                    .scalar() or 0
                )
                stats_televendas.append({
                    "usuario_id": tv.id,
                    "nome": tv.nome,
                    "ligacoes_hoje": int(lig_hoje),
                    "ligacoes_semana": int(lig_semana),
                    "ligacoes_mes": int(lig_mes),
                })
            stats_televendas = sorted(
                stats_televendas,
                key=lambda x: (-x["ligacoes_hoje"], -x["ligacoes_semana"], x["nome"])
            )

            return render_template('meus_clientes.html',
                                   representantes=representantes_ordenados,
                                   aba=aba,
                                   total_pendentes=0,
                                   total_contatados=total_contatados_tv,
                                   total_retornar=total_retornar_tv,
                                   total_oracle=total_oracle_badge,
                                   total_inativos=total_inativos,
                                   total_proximos=total_proximos_badge,
                                   usar_vista_agrupada=True,
                                   is_supervisor=current_user.tipo == 'supervisor',
                                   stats={},
                                   stats_inativos=stats_inativos,
                                   movimento_inativos_hoje=movimento_inativos_hoje,
                                   stats_televendas=stats_televendas,
                                   consultores_inativos=consultores_inativos,
                                   q=request.args.get('q', ''),
                                   meses_disponiveis_consultor=[],
                                   mes_filtro=None,
                                   ano_filtro=None)
        
        # Tratar aba Clientes próximos de inativação (151-180 dias sem pedido)
        if aba == 'proximos_inativacao':
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
            if current_user.tipo in ('consultor', 'televendas'):
                q_proximos = q_proximos.filter(Cliente.consultor_id == current_user.id)

            clientes_proximos_raw = q_proximos.all()

            if current_user.tipo == 'supervisor_repr':
                clientes_proximos_raw = [
                    c for c in clientes_proximos_raw
                    if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
                ]

            ids_proximos = [c.id for c in clientes_proximos_raw]
            stats_lig_px = {}
            if ids_proximos:
                lig_agg_px = (
                    db.session.query(
                        Ligacao.cliente_id,
                        func.count(Ligacao.id).label('total_ligacoes'),
                        func.max(Ligacao.data_hora).label('ultima_ligacao')
                    )
                    .filter(Ligacao.cliente_id.in_(ids_proximos))
                    .group_by(Ligacao.cliente_id)
                    .all()
                )
                stats_lig_px = {
                    row.cliente_id: {
                        'total_ligacoes': int(row.total_ligacoes or 0),
                        'ultima_ligacao': row.ultima_ligacao,
                    }
                    for row in lig_agg_px
                }

            agrupar_por_representante_px = current_user.tipo in ('consultor', 'televendas', 'supervisor_repr')
            grupos_px = {}
            for c in clientes_proximos_raw:
                consultor_nome = (c.consultor.nome if c.consultor else None) or 'SEM CONSULTOR'
                representante_nome = (c.representante_oracle or c.representante_nome or '').strip() or 'SEM REPRESENTANTE'
                nome_grupo = representante_nome if agrupar_por_representante_px else consultor_nome

                if nome_grupo not in grupos_px:
                    grupos_px[nome_grupo] = {
                        'nome': nome_grupo,
                        'clientes': [],
                        'total_clientes': 0,
                        'liberados': 0,
                        'inadimplentes': 0,
                        'sem_conceito': 0,
                        'ticket_medio': 0,
                        'dias_medio': 0,
                        'consultores_internos': {}
                    }

                st_lig = stats_lig_px.get(c.id, {})
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
                    "representante_nome": c.representante_oracle or c.representante_nome or '',
                    "representante_oracle": c.representante_oracle or '',
                    "ultima_ligacao": st_lig.get('ultima_ligacao'),
                    "ultima_ligacao_por": None,
                    "total_ligacoes": st_lig.get('total_ligacoes', 0),
                    "proxima_ligacao": c.proxima_ligacao,
                    "origem": c.origem,
                    "cd_cliente_oracle": c.cd_cliente_oracle,
                    "categoria_consultor": c.categoria_consultor or '',
                    "centralizadora": '',
                    "consultor_id": c.consultor_id,
                    "conceito": c.conceito or '',
                    "municipio": c.municipio or '',
                    "uf": c.uf or '',
                    "contato": c.contato or '',
                    "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
                    "valor_ultimo_pedido": c.valor_ultimo_pedido,
                    "valor_total_365dias": c.valor_total_365dias or 0,
                    "situacao_ultimo_pedido": c.situacao_ultimo_pedido or '',
                    "em_atendimento_ativo": bool(c.em_atendimento_por),
                    "em_atendimento_por_nome": None,
                    "em_atendimento_ate": None,
                    "dias_sem_pedido": dias_sem,
                    "dias_para_inativar": dias_para_inativar,
                    "data_prevista_inativacao": data_prevista_inativacao,
                }
                grupos_px[nome_grupo]['clientes'].append(dados_px)

            for _cnome, dados in grupos_px.items():
                cls = dados['clientes']
                dados['total_clientes'] = len(cls)
                dados['liberados'] = sum(1 for c in cls if c.get('conceito') == 'LIBERADO')
                dados['inadimplentes'] = sum(1 for c in cls if c.get('conceito') == 'INADIMPLENTE')
                dados['sem_conceito'] = sum(1 for c in cls if c.get('conceito') in ('', 'SEM CONCEITO', None))
                vals = [c.get('valor_ultimo_pedido', 0) for c in cls if c.get('valor_ultimo_pedido')]
                dados['ticket_medio'] = sum(vals) / len(vals) if vals else 0
                dias_list = [c.get('dias_sem_pedido', 0) for c in cls if c.get('dias_sem_pedido')]
                dados['dias_medio'] = sum(dias_list) / len(dias_list) if dias_list else 0
                dados['clientes'] = sorted(cls, key=lambda x: -x.get('dias_sem_pedido', 0))

            representantes_ordenados_px = sorted(
                grupos_px.items(),
                key=lambda x: (
                    -x[1]['total_clientes'],
                    x[0] == ('SEM REPRESENTANTE' if agrupar_por_representante_px else 'SEM CONSULTOR'),
                    x[0]
                )
            )

            total_proximos_count = sum(len(d['clientes']) for d in grupos_px.values())
            total_lib_px = sum(d['liberados'] for d in grupos_px.values())
            total_inad_px = sum(d['inadimplentes'] for d in grupos_px.values())
            total_sc_px = sum(d['sem_conceito'] for d in grupos_px.values())
            todos_vals_px = [
                c.get('valor_ultimo_pedido')
                for _, d in grupos_px.items()
                for c in d['clientes']
                if c.get('valor_ultimo_pedido')
            ]
            todos_dias_px = [
                c.get('dias_sem_pedido', 0)
                for _, d in grupos_px.items()
                for c in d['clientes']
            ]
            ticket_medio_px = sum(todos_vals_px) / len(todos_vals_px) if todos_vals_px else 0
            dias_medio_px = sum(todos_dias_px) / len(todos_dias_px) if todos_dias_px else 0
            stats_proximos = {
                'liberados': total_lib_px,
                'inadimplentes': total_inad_px,
                'sem_conceito': total_sc_px,
                'ticket_medio': ticket_medio_px,
                'dias_sem_pedido': int(dias_medio_px),
                'perc_liberados': round((total_lib_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
                'perc_inadimplentes': round((total_inad_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
                'perc_sem_conceito': round((total_sc_px / total_proximos_count) * 100, 1) if total_proximos_count > 0 else 0,
            }

            apenas_meus_px = (current_user.tipo in ('consultor', 'televendas'))
            base_q_px = Cliente.query.filter_by(ativo=True)
            if apenas_meus_px:
                base_q_px = base_q_px.filter(Cliente.consultor_id == current_user.id)
            if current_user.tipo == 'supervisor_repr':
                base_clientes_px = [
                    c for c in base_q_px.all()
                    if _cliente_tem_representante_vinculado(c, codigos_representantes_vinculados)
                ]
                ids_px = [c.id for c in base_clientes_px if c.id]
                ligados_ids_px = set()
                if ids_px:
                    rows_lig_px = (
                        db.session.query(Ligacao.cliente_id)
                        .filter(Ligacao.cliente_id.in_(ids_px))
                        .distinct()
                        .all()
                    )
                    ligados_ids_px = {row.cliente_id for row in rows_lig_px if row.cliente_id}

                total_pendentes_px = sum(1 for c in base_clientes_px if c.id not in ligados_ids_px)
                total_contatados_px = sum(1 for c in base_clientes_px if c.id in ligados_ids_px and c.proxima_ligacao is None)
                total_retornar_px = sum(1 for c in base_clientes_px if c.proxima_ligacao is not None)
            else:
                clig_px = (
                    db.session.query(Ligacao.cliente_id)
                    .filter(Ligacao.consultor_id == current_user.id)
                    .distinct()
                ) if apenas_meus_px else db.session.query(Ligacao.cliente_id).distinct()
                total_pendentes_px = base_q_px.filter(Cliente.id.notin_(clig_px)).count()
                total_contatados_px = base_q_px.filter(
                    Cliente.id.in_(clig_px), Cliente.proxima_ligacao.is_(None)
                ).count()
                total_retornar_px = base_q_px.filter(Cliente.proxima_ligacao.isnot(None)).count()

            return render_template(
                'meus_clientes.html',
                representantes=representantes_ordenados_px,
                aba=aba,
                total_pendentes=total_pendentes_px,
                total_contatados=total_contatados_px,
                total_retornar=total_retornar_px,
                total_oracle=total_oracle_badge,
                total_inativos=0,
                total_proximos=total_proximos_count,
                usar_vista_agrupada=True,
                is_supervisor=(current_user.tipo == 'supervisor'),
                stats={},
                stats_proximos=stats_proximos,
                q=request.args.get('q', ''),
                meses_disponiveis_consultor=[],
                mes_filtro=None,
                ano_filtro=None,
            )

        # Parâmetros de filtro mensal para consultores e televendas
        mes_filtro = None
        ano_filtro = None
        if current_user.tipo in ('consultor', 'televendas'):
            mes_filtro = request.args.get('mes')
            ano_filtro = request.args.get('ano')
            if mes_filtro:
                mes_filtro = int(mes_filtro)
            if ano_filtro:
                ano_filtro = int(ano_filtro)

        q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
        if current_user.tipo == 'televendas':
            clientes_ligados_por_tv = (
                db.session.query(Ligacao.cliente_id)
                .filter(Ligacao.consultor_id == current_user.id)
                .distinct()
            )
            q = q.filter(or_(
                Cliente.consultor_id == current_user.id,
                Cliente.id.in_(clientes_ligados_por_tv)
            ))
        elif apenas_meus:
            q = q.filter(Cliente.consultor_id == current_user.id)

        termo = request.args.get('q', '').strip()

        clientes_todos = q.order_by(Cliente.nome.asc()).all()

        pendentes, contatados, precisa_retornar = [], [], []
        agora = datetime.now()
        limite_min_90_120 = agora - timedelta(days=120)
        limite_max_90_120 = agora - timedelta(days=90)
        filtrar_por_categoria_consultor = (current_user.tipo == 'consultor')
        ajustar_consultor_supervisor_pendentes = (current_user.tipo == 'supervisor' and aba == 'pendentes')
        mapa_nome_para_id = {}
        mapa_codigo_para_id = {}
        ids_usuarios_ativos = set()
        if filtrar_por_categoria_consultor or ajustar_consultor_supervisor_pendentes:
            usuarios_ativos = Usuario.query.filter(
                Usuario.ativo == True,
                Usuario.tipo.in_(["consultor", "televendas", "supervisor"])
            ).all()
            ids_usuarios_ativos = {u.id for u in usuarios_ativos if u and u.id}
            mapa_nome_para_id = {
                _normalizar_nome_consultor(u.nome): u.id
                for u in usuarios_ativos if u and u.nome
            }
            mapa_codigo_para_id = construir_mapa_codigo_para_id(mapa_nome_para_id)

        for c in clientes_todos:
            if current_user.tipo == 'supervisor_repr':
                codigo_rep_cliente = _normalizar_codigo_representante(
                    _codigo_representante_de_texto(c.representante_oracle or c.representante_nome)
                )
                if not codigo_rep_cliente or codigo_rep_cliente not in codigos_representantes_vinculados:
                    continue

            ligacoes_relevantes = (
                [l for l in c.ligacoes if l.consultor_id == current_user.id]
                if current_user.tipo in ('consultor', 'televendas')
                else list(c.ligacoes)
            )
            ligs = sorted(ligacoes_relevantes, key=lambda x: x.data_hora, reverse=True)
            ultima = ligs[0] if ligs else None
            total = len(ligs)
            origem_cliente = str(getattr(c, 'origem', '') or '').strip().lower()
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
                "representante_oracle": c.representante_oracle or '',
                "ultima_ligacao": ultima.data_hora if ultima else None,
                "ultima_ligacao_por": None,
                "total_ligacoes": total,
                "proxima_ligacao": c.proxima_ligacao,
                "origem": getattr(c, 'origem', None),
                "valor_total_365dias": c.valor_total_365dias,
                "valor_ultimo_pedido": c.valor_ultimo_pedido,
                "cd_cliente_oracle": c.cd_cliente_oracle,
                "categoria_consultor": c.categoria_consultor or '',
                "centralizadora": '',
                "consultor_id": consultor_id_view,
                "conceito": c.conceito or '',
                "municipio": c.municipio or '',
                "uf": c.uf or '',
                "contato": c.contato or '',
                "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
                "situacao_ultimo_pedido": c.situacao_ultimo_pedido or '',
                "em_atendimento_ativo": bool(c.em_atendimento_por),
                "em_atendimento_por_nome": None,
                "em_atendimento_ate": None,
            }

            if (
                filtrar_por_categoria_consultor
                and c.cd_cliente_oracle
                and c.categoria_consultor
                and origem_cliente != 'manual'
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
            if current_user.tipo in ('consultor', 'supervisor_repr') and origem_cliente == 'manual':
                pendentes.append(dados)
                continue

            if total == 0:
                # Evita misturar campanha 90-120d na aba operacional de pendentes
                # e no badge "Clientes Especiais" do consultor.
                if (
                    current_user.tipo in ('consultor', 'supervisor_repr')
                    and c.cd_cliente_oracle
                    and c.ultimo_pedido_oracle
                    and limite_min_90_120 <= c.ultimo_pedido_oracle <= limite_max_90_120
                ):
                    continue
                pendentes.append(dados)
            else:
                if c.proxima_ligacao or (ultima and ultima.resultado == 'retornar'):
                    dados["retorno_atrasado"] = bool(c.proxima_ligacao and (agora >= c.proxima_ligacao))
                    precisa_retornar.append(dados)
                else:
                    contatados.append(dados)

        total_pendentes_badge = len(pendentes)
        total_contatados_badge = len(contatados)
        total_retornar_badge = len(precisa_retornar)

        # Busca textual só na listagem atual (não afeta badges).
        if termo:
            termo_lower = termo.lower()

            def _match_termo(item):
                return any(
                    termo_lower in str(item.get(chave) or '').lower()
                    for chave in ('nome', 'cnpj', 'telefone', 'representante_nome', 'representante_oracle')
                )

            pendentes_view = [c for c in pendentes if _match_termo(c)]
            contatados_view = [c for c in contatados if _match_termo(c)]
            precisa_retornar_view = [c for c in precisa_retornar if _match_termo(c)]
        else:
            pendentes_view = pendentes
            contatados_view = contatados
            precisa_retornar_view = precisa_retornar

        if aba == 'pendentes':
            # Ordenar pendentes por valor (maior para menor)
            clientes = sorted(
                pendentes_view,
                key=lambda x: (
                    float(x.get('valor_total_365dias') or 0),
                    float(x.get('valor_ultimo_pedido') or 0)
                ), 
                reverse=True
            )
        elif aba == 'retornar':
            # Ordenar retornar por data, depois por valor
            clientes = sorted(
                precisa_retornar_view,
                key=lambda x: (
                    x['proxima_ligacao'] or datetime.max,
                    float(x.get('valor_total_365dias') or 0),
                    float(x.get('valor_ultimo_pedido') or 0)
                )
            )
        else:
            # Ordenar contatados por valor (maior para menor)
            clientes = sorted(
                contatados_view,
                key=lambda x: (
                    float(x.get('valor_total_365dias') or 0),
                    float(x.get('valor_ultimo_pedido') or 0)
                ), 
                reverse=True
            )
            filtro = request.args.get('filtro')
            if filtro == 'antigos':
                limite = datetime.now() - timedelta(days=30)
                clientes = [c for c in clientes if c['ultima_ligacao'] and c['ultima_ligacao'] < limite]
            elif filtro == 'recentes':
                limite = datetime.now() - timedelta(days=7)
                clientes = [c for c in clientes if c['ultima_ligacao'] and c['ultima_ligacao'] >= limite]

        consultores = (Usuario.query
                       .filter_by(tipo='consultor', ativo=True)
                       .order_by(Usuario.nome.asc())
                       .all() if current_user.tipo == 'supervisor' else None)

        stats = {}
        if current_user.tipo in ('consultor', 'televendas'):
            hoje_date = datetime.now().date()
            desde7 = datetime.now() - timedelta(days=7)
            desde30 = datetime.now() - timedelta(days=30)

            stats['total_clientes'] = Cliente.query.filter_by(
                consultor_id=current_user.id, ativo=True
            ).count()

            stats['ligacoes_hoje'] = db.session.query(func.count(Ligacao.id)).filter(
                Ligacao.consultor_id == current_user.id,
                func.date(Ligacao.data_hora) == hoje_date
            ).scalar() or 0

            stats['ligacoes_semana'] = db.session.query(func.count(Ligacao.id)).filter(
                Ligacao.consultor_id == current_user.id,
                Ligacao.data_hora >= desde7
            ).scalar() or 0

            stats['ligacoes_mes'] = db.session.query(func.count(Ligacao.id)).filter(
                Ligacao.consultor_id == current_user.id,
                Ligacao.data_hora >= desde30
            ).scalar() or 0

            stats['meta_diaria'] = current_user.meta_diaria or 10
            stats['progresso_meta'] = round(
                (stats['ligacoes_hoje'] / stats['meta_diaria'] * 100) if stats['meta_diaria'] > 0 else 0, 1
            )

            vendas_30 = db.session.query(func.count(Ligacao.id)).filter(
                Ligacao.consultor_id == current_user.id,
                Ligacao.data_hora >= desde30,
                Ligacao.resultado == 'comprou'
            ).scalar() or 0
            positivos_30 = db.session.query(func.count(Ligacao.id)).filter(
                Ligacao.consultor_id == current_user.id,
                Ligacao.data_hora >= desde30,
                Ligacao.resultado.in_(('comprou', 'relacionamento', 'retornar'))
            ).scalar() or 0

            stats['taxa_conversao'] = round(
                (vendas_30 / stats['ligacoes_mes'] * 100) if stats['ligacoes_mes'] > 0 else 0, 1
            )
            stats['positivos_30'] = int(positivos_30)
            stats['taxa_positiva_30'] = round(
                (positivos_30 / stats['ligacoes_mes'] * 100) if stats['ligacoes_mes'] > 0 else 0, 1
            )
            stats['converteu_30'] = int(vendas_30)

            receita_total = db.session.query(func.sum(Ligacao.valor_venda)).filter(
                Ligacao.consultor_id == current_user.id,
                Ligacao.data_hora >= desde30,
                Ligacao.resultado == 'comprou'
            ).scalar() or 0

            stats['receita_mes'] = formatar_dinheiro(receita_total)
            stats['clientes_90_120'] = int(total_oracle_badge or 0)
        
        # Gerar lista de meses/anos disponíveis para o filtro do consultor e televendas
        meses_disponiveis_consultor = []
        if current_user.tipo in ('consultor', 'televendas'):
            data_atual = datetime.now()
            meses_nomes = {
                1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
                5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
                9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
            }
            for i in range(12):
                data = data_atual - timedelta(days=30*i)
                meses_disponiveis_consultor.append({
                    "mes": data.month,
                    "ano": data.year,
                    "texto": f"{meses_nomes[data.month]}/{data.year}"
                })

        total_inativos_badge = 0
        if current_user.tipo in ('televendas', 'supervisor'):
            cache = _INATIVOS_COUNT_CACHE.get(current_user.id)
            if cache and cache.get("ts"):
                idade = (datetime.now() - cache["ts"]).total_seconds()
                if idade <= _INATIVOS_COUNT_CACHE_TTL_SECONDS:
                    total_inativos_badge = int(cache.get("count") or 0)
            if total_inativos_badge == 0:
                # Televendas vê todos os inativos na aba (sem filtro por consultor_id),
                # então o badge deve refletir o total global, não apenas os do usuário.
                consultor_inativos = current_user.id if (apenas_meus and current_user.tipo == 'consultor') else None
                total_inativos_badge = _total_inativos_badge(consultor_inativos)
                _INATIVOS_COUNT_CACHE[current_user.id] = {
                    "count": int(total_inativos_badge),
                    "ts": datetime.now(),
                }

        # Para consultores: converter para vista agrupada por representante
        # (mantendo contatados/retornar na lista simples original).
        if (
            (current_user.tipo in ('supervisor', 'consultor') and aba == 'pendentes') or
            (current_user.tipo in ('consultor', 'supervisor', 'supervisor_repr') and aba not in ('contatados', 'retornar', 'pendentes'))
        ):
            agora_grp = datetime.now()
            agrupar_por_consultor = (current_user.tipo == 'supervisor' and aba == 'pendentes')
            mapa_consultor_nome = {}
            if agrupar_por_consultor:
                mapa_consultor_nome = {
                    int(uid): (nome or '').strip()
                    for uid, nome in (
                        db.session.query(Usuario.id, Usuario.nome)
                        .filter(Usuario.ativo == True)
                        .all()
                    )
                }
            grupo_sem_nome = 'SEM CONSULTOR' if agrupar_por_consultor else 'SEM REPRESENTANTE'
            representantes_data_grp = {}
            for item in clientes:
                if agrupar_por_consultor:
                    consultor_id_item = item.get('consultor_id')
                    rep_nome = (
                        mapa_consultor_nome.get(int(consultor_id_item))
                        if consultor_id_item else None
                    ) or grupo_sem_nome
                else:
                    rep_nome = (
                        str(item.get('representante_oracle') or item.get('representante_nome') or '').strip()
                        or grupo_sem_nome
                    )
                if rep_nome not in representantes_data_grp:
                    representantes_data_grp[rep_nome] = {
                        'nome': rep_nome,
                        'clientes': [],
                        'total_clientes': 0,
                        'liberados': 0,
                        'inadimplentes': 0,
                        'sem_conceito': 0,
                        'ticket_medio': 0,
                        'dias_medio': 0,
                        'consultores_internos': {}
                    }
                representantes_data_grp[rep_nome]['clientes'].append(item)

            for _rep, dados_rep in representantes_data_grp.items():
                cls_r = dados_rep['clientes']
                dados_rep['total_clientes'] = len(cls_r)
                dados_rep['liberados'] = sum(1 for c in cls_r if c.get('conceito') == 'LIBERADO')
                dados_rep['inadimplentes'] = sum(1 for c in cls_r if c.get('conceito') == 'INADIMPLENTE')
                dados_rep['sem_conceito'] = sum(1 for c in cls_r if c.get('conceito') in ('', 'SEM CONCEITO', None))
                vals_r = [c.get('valor_ultimo_pedido', 0) for c in cls_r if c.get('valor_ultimo_pedido')]
                dados_rep['ticket_medio'] = sum(vals_r) / len(vals_r) if vals_r else 0
                dias_r = [
                    (agora_grp - c['ultimo_pedido_oracle']).days
                    for c in cls_r if c.get('ultimo_pedido_oracle')
                ]
                dados_rep['dias_medio'] = sum(dias_r) / len(dias_r) if dias_r else 0

            if current_user.tipo == 'consultor' and aba == 'pendentes':
                representantes_ordenados_grp = sorted(
                    representantes_data_grp.items(),
                    key=lambda x: (-x[1]['total_clientes'], x[0] == grupo_sem_nome, x[0])
                )
            else:
                representantes_ordenados_grp = sorted(
                    representantes_data_grp.items(),
                    key=lambda x: (-x[1]['total_clientes'], x[0] == grupo_sem_nome, x[0])
                )

            return render_template(
                'meus_clientes.html',
                representantes=representantes_ordenados_grp,
                usar_vista_agrupada=True,
                aba=aba,
                total_pendentes=total_pendentes_badge,
                total_contatados=total_contatados_badge,
                total_retornar=total_retornar_badge,
                total_inativos=total_inativos_badge,
                total_oracle=total_oracle_badge,
                total_proximos=total_proximos_badge,
                is_supervisor=(current_user.tipo == 'supervisor'),
                now=datetime.now,
                stats=stats,
                mostrar_novidades=not current_user.viu_novidades,
                banners_ativos=get_banners_ativos(),
                mes_filtro=mes_filtro,
                ano_filtro=ano_filtro,
                meses_disponiveis_consultor=meses_disponiveis_consultor,
            )

        return render_template(
            'meus_clientes.html',
            clientes=clientes,
            total_pendentes=total_pendentes_badge,
            total_contatados=total_contatados_badge,
            total_retornar=total_retornar_badge,
            total_inativos=total_inativos_badge,
            total_oracle=total_oracle_badge,
            total_proximos=total_proximos_badge,
            aba=aba,
            is_supervisor=(current_user.tipo == 'supervisor'),
            now=datetime.now,
            consultores=consultores,
            stats=stats,
            mostrar_novidades=not current_user.viu_novidades,
            banners_ativos=get_banners_ativos(),
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis_consultor=meses_disponiveis_consultor
        )

    # =============================================================================
    # PREENCHIMENTO MANUAL VIA CNPJ (ORACLE)
    # =============================================================================
    @app.route('/clientes/preencher-oracle-cnpj', methods=['POST'])
    @login_required
    def preencher_cliente_oracle_por_cnpj():
        try:
            payload = request.get_json(silent=True) or {}
            cnpj = so_digits(payload.get('cnpj'))
            if not cnpj or len(cnpj) < 7:
                return jsonify({"ok": False, "mensagem": "Informe um CNPJ valido (minimo 7 digitos)"}), 400

            from oracle_service import get_cliente_oracle_por_cnpj
            cliente_oracle = get_cliente_oracle_por_cnpj(cnpj)
            if not cliente_oracle:
                return jsonify({
                    "ok": True,
                    "encontrado": False,
                    "mensagem": "CNPJ nao encontrado no Oracle"
                })

            consultor_oracle = s(cliente_oracle.get("consultor"))
            nome_oracle = _extrair_nome_oracle_consultor(consultor_oracle)
            nome_oracle_norm = _normalizar_nome_consultor(nome_oracle)
            consultor_sugerido = None
            if nome_oracle_norm:
                candidatos = Usuario.query.filter(
                    Usuario.tipo.in_(["consultor", "televendas"]),
                    Usuario.ativo == True
                ).all()
                mapa_nome = {
                    _normalizar_nome_consultor(u.nome): u
                    for u in candidatos
                    if u and u.nome
                }
                consultor_sugerido = mapa_nome.get(nome_oracle_norm)
                if not consultor_sugerido:
                    primeiro_oracle = nome_oracle_norm.split()[0]
                    for nome_norm, usuario in mapa_nome.items():
                        if nome_norm and nome_norm.split()[0] == primeiro_oracle:
                            consultor_sugerido = usuario
                            break

            return jsonify({
                "ok": True,
                "encontrado": True,
                "dados": {
                    "cd_cliente_oracle": str(cliente_oracle.get("cd_cliente") or "").strip(),
                    "nome": s(cliente_oracle.get("cliente")),
                    "cnpj": so_digits(cliente_oracle.get("cnpj")) or cnpj,
                    "telefone": so_digits(cliente_oracle.get("telefone1")) or "",
                    "telefone2": so_digits(cliente_oracle.get("telefone2")) or "",
                    "representante_nome": s(cliente_oracle.get("representante")),
                    "representante_oracle": s(cliente_oracle.get("representante")),
                    "categoria_consultor": s(cliente_oracle.get("consultor")),
                    "conceito": s(cliente_oracle.get("conceito")),
                    "municipio": s(cliente_oracle.get("municipio")),
                    "uf": s(cliente_oracle.get("uf")),
                    "contato": s(cliente_oracle.get("contato")),
                    "consultor_id_sugerido": (consultor_sugerido.id if consultor_sugerido else None),
                    "consultor_nome_sugerido": (consultor_sugerido.nome if consultor_sugerido else None),
                }
            })
        except Exception as e:
            return jsonify({"ok": False, "mensagem": f"Erro ao buscar no Oracle: {str(e)}"}), 500

    # =============================================================================
    # CRIAR CLIENTE MANUALMENTE
    # =============================================================================
    @app.route('/clientes/<int:cliente_id>/sincronizar-oracle', methods=['POST'])
    @login_required
    def sincronizar_cliente_oracle_por_id(cliente_id: int):
        try:
            if current_user.tipo != 'supervisor':
                return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403

            cliente = db.session.get(Cliente, cliente_id)
            if not cliente:
                return jsonify({"ok": False, "mensagem": "Cliente nao encontrado"}), 404

            payload = request.get_json(silent=True) or {}
            cnpj = so_digits(payload.get('cnpj') or cliente.cnpj)
            if not cnpj:
                return jsonify({"ok": False, "mensagem": "Cliente sem CNPJ para sincronizar com Oracle"}), 400

            from oracle_service import get_cliente_oracle_por_cnpj
            cliente_oracle = get_cliente_oracle_por_cnpj(cnpj)
            if not cliente_oracle:
                return jsonify({"ok": False, "mensagem": "Cliente nao encontrado no Oracle para este CNPJ"}), 404

            nome_oracle = s(cliente_oracle.get('cliente')) or None
            telefone1 = so_digits(cliente_oracle.get('telefone1')) or None
            telefone2 = so_digits(cliente_oracle.get('telefone2')) or None
            representante = s(cliente_oracle.get('representante')) or None

            cliente.cnpj = so_digits(cliente_oracle.get('cnpj')) or cliente.cnpj
            if nome_oracle and (not cliente.nome or cliente.nome.strip() == ''):
                cliente.nome = nome_oracle[:200]
            if telefone1:
                cliente.telefone = telefone1
            if telefone2:
                cliente.telefone2 = telefone2
            if representante:
                cliente.representante_nome = representante
                cliente.representante_oracle = representante

            cliente.cd_cliente_oracle = str(cliente_oracle.get('cd_cliente') or '').strip() or cliente.cd_cliente_oracle
            cliente.categoria_consultor = s(cliente_oracle.get('consultor')) or cliente.categoria_consultor
            cliente.conceito = s(cliente_oracle.get('conceito')) or cliente.conceito
            cliente.municipio = s(cliente_oracle.get('municipio')) or cliente.municipio
            cliente.uf = s(cliente_oracle.get('uf')) or cliente.uf
            cliente.contato = s(cliente_oracle.get('contato')) or cliente.contato
            cliente.data_ultima_sincronizacao = datetime.now()

            db.session.add(cliente)
            db.session.commit()

            return jsonify({
                "ok": True,
                "mensagem": "Cliente sincronizado com Oracle",
                "cliente": {
                    "id": cliente.id,
                    "cd_cliente_oracle": cliente.cd_cliente_oracle,
                    "cnpj": cliente.cnpj,
                    "nome": cliente.nome,
                    "telefone": cliente.telefone,
                }
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro ao sincronizar cliente com Oracle: {str(e)}"}), 500

    @app.route('/clientes/sincronizar-manuais-oracle', methods=['POST'])
    @login_required
    def sincronizar_clientes_manuais_oracle():
        try:
            if current_user.tipo != 'supervisor':
                return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403

            from oracle_service import get_cliente_oracle_por_cnpj
            import logging
            logger = logging.getLogger(__name__)

            clientes_manuais = (
                Cliente.query
                .filter(
                    Cliente.ativo == True,
                    Cliente.origem == 'manual',
                    Cliente.cnpj.isnot(None)
                )
                .all()
            )

            total_base = len(clientes_manuais)
            logger.info(f"[Sync Manuais] Total de clientes manuais com CNPJ: {total_base}")
            
            atualizados = 0
            nao_encontrados = 0
            sem_cnpj = 0
            lista_nao_encontrados = []  # Log dos CNPJs não encontrados

            for cliente in clientes_manuais:
                cnpj = so_digits(cliente.cnpj)
                if not cnpj:
                    sem_cnpj += 1
                    continue

                # Log do CNPJ sendo buscado
                logger.info(f"[Sync Manuais] Buscando cliente '{cliente.nome}' com CNPJ: {cnpj}")
                
                # Tentar buscar com retry e delay para não esgotar o pool
                row = None
                max_tentativas = 3
                for tentativa in range(max_tentativas):
                    try:
                        row = get_cliente_oracle_por_cnpj(cnpj)
                        if row:
                            break
                        # Se não encontrou, não precisa retry
                        break
                    except Exception as e:
                        logger.warning(f"[Sync Manuais] Erro na tentativa {tentativa+1} para {cnpj}: {e}")
                        if tentativa < max_tentativas - 1:
                            import time
                            time.sleep(0.5)  # Aguardar 500ms antes de retry
                
                if not row:
                    nao_encontrados += 1
                    lista_nao_encontrados.append(f"{cliente.nome} ({cnpj})")
                    logger.warning(f"[Sync Manuais] NÃO ENCONTRADO: {cliente.nome} - CNPJ: {cnpj}")
                    continue

                logger.info(f"[Sync Manuais] ENCONTRADO: {cliente.nome} -> cd_cliente: {row.get('cd_cliente')}")
                
                nome_oracle = s(row.get('cliente')) or None
                telefone1 = so_digits(row.get('telefone1')) or None
                telefone2 = so_digits(row.get('telefone2')) or None
                representante = s(row.get('representante')) or None

                cliente.cnpj = so_digits(row.get('cnpj')) or cliente.cnpj
                if nome_oracle and (not cliente.nome or cliente.nome.strip() == ''):
                    cliente.nome = nome_oracle[:200]
                if telefone1:
                    cliente.telefone = telefone1
                if telefone2:
                    cliente.telefone2 = telefone2
                if representante:
                    cliente.representante_nome = representante
                    cliente.representante_oracle = representante

                cliente.cd_cliente_oracle = str(row.get('cd_cliente') or '').strip() or cliente.cd_cliente_oracle
                cliente.categoria_consultor = s(row.get('consultor')) or cliente.categoria_consultor
                cliente.conceito = s(row.get('conceito')) or cliente.conceito
                cliente.municipio = s(row.get('municipio')) or cliente.municipio
                cliente.uf = s(row.get('uf')) or cliente.uf
                cliente.contato = s(row.get('contato')) or cliente.contato
                cliente.data_ultima_sincronizacao = datetime.now()
                db.session.add(cliente)
                atualizados += 1

            db.session.commit()
            
            # Log resumo
            logger.info(f"[Sync Manuais] RESUMO: Total={total_base}, Atualizados={atualizados}, NaoEncontrados={nao_encontrados}, SemCNPJ={sem_cnpj}")
            if lista_nao_encontrados:
                logger.info(f"[Sync Manuais] Lista nao encontrados: {', '.join(lista_nao_encontrados[:10])}")  # Primeiros 10

            return jsonify({
                "ok": True,
                "mensagem": (
                    f"Sync manuais concluida. Base: {total_base} | "
                    f"Atualizados: {atualizados} | "
                    f"Nao encontrados no Oracle: {nao_encontrados} | "
                    f"Sem CNPJ valido: {sem_cnpj}"
                ),
                "total_base": total_base,
                "atualizados": atualizados,
                "nao_encontrados": nao_encontrados,
                "sem_cnpj": sem_cnpj,
                "nao_encontrados_lista": lista_nao_encontrados[:20]  # Retorna lista para debug
            })
        except Exception as e:
            db.session.rollback()
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"[Sync Manuais] ERRO: {str(e)}")
            return jsonify({"ok": False, "mensagem": f"Erro no sync manual com Oracle: {str(e)}"}), 500

    @app.route('/clientes/criar', methods=['POST'])
    @login_required
    def criar_cliente_manual():
        try:
            if current_user.tipo == 'supervisor_repr':
                return jsonify({"ok": False, "mensagem": "Usuários do tipo Supervisor de Representante não podem criar clientes (somente visualização)."}), 403

            payload = request.get_json(silent=True) or {}
            nome = s(payload.get('nome'))
            cnpj = so_digits(payload.get('cnpj')) or None
            telefone = so_digits(payload.get('telefone')) or None
            telefone2 = so_digits(payload.get('telefone2')) or None
            representante = s(payload.get('representante_nome')) or None
            cd_cliente_oracle = s(payload.get('cd_cliente_oracle')) or None
            representante_oracle = s(payload.get('representante_oracle')) or None
            categoria_consultor = s(payload.get('categoria_consultor')) or None
            conceito = s(payload.get('conceito')) or None
            municipio = s(payload.get('municipio')) or None
            uf = s(payload.get('uf')) or None
            contato = s(payload.get('contato')) or None

            if not nome:
                return jsonify({"ok": False, "mensagem": "Nome é obrigatório"}), 400

            consultor_id = None
            if current_user.tipo == 'supervisor':
                consultor_id = int(payload.get('consultor_id') or 0) or None
            if not consultor_id:
                consultor_id = current_user.id

            if cnpj:
                existente = Cliente.query.filter_by(cnpj=cnpj).first()
                if existente:
                    existente.nome = nome[:200]
                    existente.telefone = telefone
                    existente.telefone2 = telefone2
                    existente.representante_nome = representante
                    existente.consultor_id = consultor_id
                    existente.ativo = True
                    existente.origem = 'manual'
                    if cd_cliente_oracle:
                        existente.cd_cliente_oracle = cd_cliente_oracle
                    if representante_oracle:
                        existente.representante_oracle = representante_oracle
                    if categoria_consultor:
                        existente.categoria_consultor = categoria_consultor
                    if conceito:
                        existente.conceito = conceito
                    if municipio:
                        existente.municipio = municipio
                    if uf:
                        existente.uf = uf
                    if contato:
                        existente.contato = contato
                    db.session.add(existente)

                    n = Nota(
                        cliente_id=existente.id,
                        usuario_id=current_user.id,
                        texto=f"Cliente atualizado/reativado manualmente por {current_user.nome} em {datetime.now().strftime('%d/%m/%Y %H:%M')}."
                    )
                    db.session.add(n)

                    db.session.commit()
                    return jsonify({
                        "ok": True,
                        "mensagem": "Cliente atualizado (reativado) com sucesso!",
                        "cliente_id": existente.id
                    })

            novo = Cliente(
                nome=nome[:200],
                cnpj=cnpj,
                telefone=telefone,
                telefone2=telefone2,
                representante_nome=representante,
                consultor_id=consultor_id,
                ativo=True,
                origem='manual',
                cd_cliente_oracle=cd_cliente_oracle,
                representante_oracle=representante_oracle,
                categoria_consultor=categoria_consultor,
                conceito=conceito,
                municipio=municipio,
                uf=uf,
                contato=contato,
            )
            db.session.add(novo)
            db.session.flush()

            n = Nota(
                cliente_id=novo.id,
                usuario_id=current_user.id,
                texto=f"Cliente criado manualmente por {current_user.nome} em {datetime.now().strftime('%d/%m/%Y %H:%M')}."
            )
            db.session.add(n)

            db.session.commit()
            return jsonify({
                "ok": True,
                "mensagem": "Cliente criado com sucesso!",
                "cliente_id": novo.id
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    # =============================================================================
    # REGISTRAR LIGACAO
    # =============================================================================
    @app.route('/api/clientes/<int:cliente_id>/iniciar-contato', methods=['POST'])
    @login_required
    def iniciar_contato_cliente(cliente_id: int):
        try:
            if current_user.tipo == 'supervisor_repr':
                return jsonify({"ok": False, "mensagem": "Usuários do tipo Supervisor de Representante não podem iniciar contato (somente visualização)."}), 403

            payload = request.get_json(silent=True) or {}
            forcar = bool(payload.get('forcar'))
            aba_contexto = str(payload.get('aba') or '').strip().lower()
            cd_oracle_payload = str(payload.get('cd_cliente_oracle') or '').strip()

            cli = db.session.get(Cliente, cliente_id)
            if not cli:
                return jsonify({"ok": False, "mensagem": "Cliente no encontrado."}), 404

            if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permisso para este cliente."}), 403

            usa_lock_compartilhado_inativos = (aba_contexto == 'inativos')

            if usa_lock_compartilhado_inativos:
                cd_oracle_lock = str(cli.cd_cliente_oracle or cd_oracle_payload or '').strip()
                clientes_relacionados = []
                if cd_oracle_lock:
                    clientes_relacionados = (
                        Cliente.query
                        .filter(
                            Cliente.ativo == True,
                            Cliente.cd_cliente_oracle == cd_oracle_lock
                        )
                        .all()
                    )
                if not clientes_relacionados:
                    clientes_relacionados = [cli]

                bloqueado_por = None
                for cli_rel in clientes_relacionados:
                    if (
                        cli_rel.em_atendimento_por
                        and cli_rel.em_atendimento_por != current_user.id
                    ):
                        bloqueado_por = cli_rel
                        break

                if bloqueado_por and not forcar:
                    usuario_lock = db.session.get(Usuario, bloqueado_por.em_atendimento_por)
                    return jsonify({
                        "ok": False,
                        "bloqueado": True,
                        "em_atendimento_por_id": bloqueado_por.em_atendimento_por,
                        "em_atendimento_por_nome": (usuario_lock.nome if usuario_lock else "Outro usurio"),
                        "em_atendimento_ate": None,
                        "mensagem": "Cliente em atendimento por outro usurio."
                    }), 409

                for cli_rel in clientes_relacionados:
                    cli_rel.em_atendimento_por = current_user.id
                    cli_rel.em_atendimento_ate = None
                cli.em_atendimento_por = current_user.id
                cli.em_atendimento_ate = None
            else:
                bloqueio_ativo = (
                    cli.em_atendimento_por
                    and cli.em_atendimento_por != current_user.id
                )

                if bloqueio_ativo and not forcar:
                    usuario_lock = db.session.get(Usuario, cli.em_atendimento_por)
                    return jsonify({
                        "ok": False,
                        "bloqueado": True,
                        "em_atendimento_por_id": cli.em_atendimento_por,
                        "em_atendimento_por_nome": (usuario_lock.nome if usuario_lock else "Outro usurio"),
                        "em_atendimento_ate": None,
                        "mensagem": "Cliente em atendimento por outro usurio."
                    }), 409

                cli.em_atendimento_por = current_user.id
                cli.em_atendimento_ate = None
            db.session.commit()

            return jsonify({
                "ok": True,
                "em_atendimento_por_id": cli.em_atendimento_por,
                "em_atendimento_por_nome": current_user.nome,
                "em_atendimento_ate": None,
                "forcado": bool(forcar),
            })
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route('/api/inativos/locks', methods=['GET', 'POST'])
    @login_required
    def listar_locks_inativos():
        try:
            if current_user.tipo not in ('televendas', 'supervisor'):
                return jsonify({"ok": False, "mensagem": "Sem permissao"}), 403

            cds = []
            if request.method == 'POST':
                payload = request.get_json(silent=True) or {}
                for item in (payload.get('cds') or []):
                    cd = str(item or '').strip()
                    if cd:
                        cds.append(cd)
            else:
                cds_raw = request.args.get('cds') or ''
                for item in cds_raw.split(','):
                    cd = str(item or '').strip()
                    if cd:
                        cds.append(cd)

            # Remove duplicados mantendo ordem.
            cds = list(dict.fromkeys(cds))

            if not cds:
                return jsonify({"ok": True, "locks": {}})

            rows = (
                db.session.query(
                    Cliente.cd_cliente_oracle.label('cd_cliente_oracle'),
                    Cliente.em_atendimento_por.label('em_atendimento_por'),
                    Usuario.nome.label('usuario_nome')
                )
                .outerjoin(Usuario, Usuario.id == Cliente.em_atendimento_por)
                .filter(
                    Cliente.ativo == True,
                    Cliente.cd_cliente_oracle.in_(cds),
                    Cliente.em_atendimento_por.isnot(None),
                )
                .all()
            )

            locks = {}
            for row in rows:
                cd = str(row.cd_cliente_oracle or '').strip()
                if not cd:
                    continue
                if cd not in locks:
                    locks[cd] = {
                        "ativo": True,
                        "por_nome": (row.usuario_nome or "Outro usuario"),
                        "ate": None,
                    }

            return jsonify({"ok": True, "locks": locks})
        except Exception as e:
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route('/registrar-ligacao/<int:cliente_id>', methods=['POST'])
    def registrar_ligacao(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401

        # Bloquear supervisor_repr de registrar ligações
        if current_user.tipo == 'supervisor_repr':
            return jsonify({"ok": False, "mensagem": "Usuários do tipo Supervisor de Representante não podem registrar ligações (somente visualização)."}), 403

        try:
            payload = request.get_json(silent=True) or {}
            obs = s(payload.get('observacao'))
            contato_nome = s(payload.get('contato_nome'))
            resultado = s(payload.get('resultado') or 'nao_comprou')

            try:
                valor_venda = float(str(payload.get('valor_venda') or 0).replace(',', '.'))
            except:
                valor_venda = 0.0

            cli = db.session.get(Cliente, cliente_id)
            if not cli:
                return jsonify({"ok": False, "mensagem": "Cliente não encontrado."}), 404

            if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permissão para este cliente."}), 403

            agora = datetime.now()

            if resultado not in ('comprou', 'nao_comprou', 'retornar', 'sem_interesse', 'relacionamento', 'cliente_inativo'):
                resultado = 'nao_comprou'

            lig = Ligacao(
                cliente_id=cliente_id,
                consultor_id=current_user.id,
                data_hora=agora,
                observacao=obs or None,
                contato_nome=contato_nome or None,
                resultado=resultado,
                valor_venda=valor_venda
            )
            db.session.add(lig)

            # Em televendas, ao registrar contato o cliente passa para a carteira do usuario,
            # permitindo o fluxo entre Inativos -> Contatados/Retornar.
            if current_user.tipo == 'televendas' and cli.consultor_id != current_user.id:
                cli.consultor_id = current_user.id

            # Retorno opcional para QUALQUER resultado
            dias_retorno = None
            data_retorno = s(payload.get('data_retorno'))
            try:
                dias_retorno = int(payload.get('dias_retorno')) if payload.get('dias_retorno') else None
            except Exception:
                dias_retorno = None

            # Só agenda retorno se preencher algo
            if data_retorno:
                try:
                    d = datetime.strptime(data_retorno, "%Y-%m-%d").date()
                    cli.proxima_ligacao = datetime(d.year, d.month, d.day, 9, 0, 0)
                except Exception:
                    cli.proxima_ligacao = agora + timedelta(days=30)
            elif dias_retorno and dias_retorno > 0:
                cli.proxima_ligacao = agora + timedelta(days=dias_retorno)
            elif resultado == 'retornar':
                # Se marcou "retornar" sem preencher data/dias, agenda retorno padrão.
                cli.proxima_ligacao = agora + timedelta(days=30)
            else:
                # Se não preencher nada, não agenda retorno
                cli.proxima_ligacao = None

            # Libera trava de atendimento apos registrar a ligacao.
            cli.em_atendimento_por = None
            cli.em_atendimento_ate = None

            db.session.commit()

            msg = "Ligação registrada!"
            if cli.proxima_ligacao:
                msg = "Ligação registrada! Cliente marcado para retorno."
            elif resultado == 'comprou':
                msg = "Ligação registrada! Venda marcada como 'comprou'."

            return jsonify({
                "ok": True,
                "mensagem": msg,
                "proxima_ligacao": cli.proxima_ligacao.isoformat() if cli.proxima_ligacao else None,
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    # =============================================================================
    # EDITAR OBSERVACAO DE LIGACAO
    # =============================================================================
    @app.route('/editar-observacao/<int:ligacao_id>', methods=['POST'])
    @login_required
    def editar_observacao(ligacao_id: int):
        try:
            if current_user.tipo == 'supervisor_repr':
                return jsonify({"ok": False, "mensagem": "Usuários do tipo Supervisor de Representante não podem editar observações (somente visualização)."}), 403

            ligacao = db.session.get(Ligacao, ligacao_id)
            if not ligacao:
                return jsonify({"ok": False, "mensagem": "Ligação não encontrada"}), 404
            
            # Verificar permissão
            if current_user.tipo == 'consultor' and ligacao.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permissão"}), 403
            
            payload = request.get_json(silent=True) or {}
            nova_obs = s(payload.get('observacao'))
            
            ligacao.observacao = nova_obs or None
            db.session.commit()
            
            return jsonify({"ok": True, "mensagem": "Observação atualizada com sucesso!"})
            
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    # =============================================================================
    # EDITAR LIGACAO COMPLETA (RESULTADO, VALOR, OBSERVACAO)
    # =============================================================================
    @app.route('/editar-ligacao/<int:ligacao_id>', methods=['POST'])
    @login_required
    def editar_ligacao(ligacao_id: int):
        try:
            if current_user.tipo == 'supervisor_repr':
                return jsonify({"ok": False, "mensagem": "Usuários do tipo Supervisor de Representante não podem editar ligações (somente visualização)."}), 403

            ligacao = db.session.get(Ligacao, ligacao_id)
            if not ligacao:
                return jsonify({"ok": False, "mensagem": "Ligação não encontrada"}), 404
            
            # Verificar permissão: consultor e televendas só podem editar suas próprias ligações
            if current_user.tipo == 'consultor' and ligacao.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permissão para editar esta ligação"}), 403
            
            payload = request.get_json(silent=True) or {}
            
            # Editar resultado
            if 'resultado' in payload:
                novo_resultado = s(payload.get('resultado'))
                if novo_resultado in ('comprou', 'nao_comprou', 'retornar', 'sem_interesse', 'relacionamento', 'cliente_inativo'):
                    ligacao.resultado = novo_resultado
            
            # Editar valor da venda
            if 'valor_venda' in payload:
                try:
                    novo_valor = float(str(payload.get('valor_venda') or 0).replace(',', '.'))
                    ligacao.valor_venda = novo_valor
                except:
                    ligacao.valor_venda = 0.0
            
            # Editar observação
            if 'observacao' in payload:
                ligacao.observacao = s(payload.get('observacao')) or None
            
            db.session.commit()
            return jsonify({"ok": True, "mensagem": "Ligação atualizada com sucesso!"})
            
        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    # =============================================================================
    # OBTER DETALHES DA LIGACAO PARA EDICAO
    # =============================================================================
    @app.route('/api/detalhes-ligacao/<int:ligacao_id>')
    @login_required
    def api_detalhes_ligacao(ligacao_id: int):
        try:
            ligacao = db.session.get(Ligacao, ligacao_id)
            if not ligacao:
                return jsonify({"erro": "Ligação não encontrada"}), 404
            
            # Verificar permissão
            if current_user.tipo == 'consultor' and ligacao.consultor_id != current_user.id:
                return jsonify({"erro": "Sem permissão"}), 403
            
            return jsonify({
                "id": ligacao.id,
                "resultado": ligacao.resultado,
                "valor_venda": float(ligacao.valor_venda or 0),
                "valor_venda_fmt": formatar_dinheiro(ligacao.valor_venda),
                "observacao": ligacao.observacao,
                "contato_nome": ligacao.contato_nome,
                "data_hora": ligacao.data_hora.strftime("%d/%m/%Y %H:%M") if ligacao.data_hora else ""
            })
            
        except Exception as e:
            return jsonify({"erro": f"Erro: {str(e)}"}), 500

    # =============================================================================
    # HISTORICO LIGACOES
    # =============================================================================
    @app.route('/historico-ligacoes/<int:cliente_id>')
    def historico_ligacoes(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify([])

        try:
            cli = db.session.get(Cliente, cliente_id)
            if not cli:
                return jsonify([])

            if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
                return jsonify([])

            regs = (Ligacao.query
                    .options(joinedload(Ligacao.consultor))
                    .filter(Ligacao.cliente_id == cliente_id)
                    .order_by(Ligacao.data_hora.desc())
                    .all())

            out = []
            for r in regs:
                try:
                    dt = r.data_hora.strftime("%d/%m/%Y %H:%M") if r.data_hora else ""
                    consultor_nome = r.consultor.nome if getattr(r, "consultor", None) else ""
                    contato = s(r.contato_nome)
                    resultado = s(r.resultado)
                    try:
                        valor_num = float(r.valor_venda or 0)
                    except Exception:
                        valor_num = 0.0

                    out.append({
                        "id": r.id,  # NOVO: incluir ID da ligacao
                        "data_hora": dt,
                        "consultor": consultor_nome,
                        "contato_nome": contato,
                        "resultado": resultado,
                        "valor_venda": formatar_dinheiro(valor_num),
                        "observacao": s(r.observacao),
                        "pode_editar": (current_user.tipo == 'supervisor' or r.consultor_id == current_user.id)  # NOVO
                    })
                except Exception:
                    continue

            return jsonify(out)

        except Exception:
            return jsonify([])

    # =============================================================================
    # NOTAS RÁPIDAS
    # =============================================================================
    @app.route('/clientes/<int:cliente_id>/notas', methods=['GET'])
    def listar_notas(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify([])
        notas = (Nota.query
                 .options(joinedload(Nota.usuario))
                 .filter(Nota.cliente_id == cliente_id)
                 .order_by(Nota.data_criacao.desc())
                 .all())
        out = [{
            "id": n.id,
            "autor": n.usuario.nome if n.usuario else "",
            "texto": n.texto,
            "quando": n.data_criacao.strftime("%d/%m/%Y %H:%M")
        } for n in notas]
        return jsonify(out)


    @app.route('/clientes/<int:cliente_id>/notas', methods=['POST'])
    def adicionar_nota(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401
        
        # Bloquear supervisor_repr de adicionar notas
        if current_user.tipo == 'supervisor_repr':
            return jsonify({"ok": False, "mensagem": "Usuários do tipo Supervisor de Representante não podem adicionar notas (somente visualização)."}), 403
        
        texto = s((request.get_json(silent=True) or {}).get('texto'))
        if not texto:
            return jsonify({"ok": False, "mensagem": "Texto obrigatório"}), 400

        cli = db.session.get(Cliente, cliente_id)
        if not cli:
            return jsonify({"ok": False, "mensagem": "Cliente não encontrado"}), 404

        if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
            return jsonify({"ok": False, "mensagem": "Sem permissão"}), 403

        n = Nota(cliente_id=cliente_id, usuario_id=current_user.id, texto=texto)
        db.session.add(n)
        db.session.commit()
        return jsonify({"ok": True, "mensagem": "Nota adicionada!"})

    # =============================================================================
    # IMPORTACAO DE CLIENTES
    # =============================================================================
    @app.route('/importar-clientes', methods=['GET', 'POST'])
    def importar_clientes_view():
        if not current_user.is_authenticated:
            return redirect(url_for('login'))

        if current_user.tipo != 'supervisor':
            flash('Acesso permitido somente para supervisores.', 'danger')
            return redirect(url_for('meus_clientes'))

        if request.method == 'POST':
            consultor_id = request.form.get('consultor_id')
            arquivo = request.files.get('arquivo')

            if not consultor_id or not arquivo:
                flash('Selecione o consultor e o arquivo (.xlsx ou .csv).', 'warning')
                return redirect(url_for('importar_clientes_view'))

            consultor_id = int(consultor_id)
            filename = getattr(arquivo, "filename", "") or ""
            ext = (filename.rsplit('.', 1)[-1].lower() if '.' in filename else "")

            df = None
            try:
                if ext in ("xlsx", "xls") or not ext:
                    df = pd.read_excel(
                        arquivo,
                        dtype=str,
                        header=0,
                        keep_default_na=False,
                        na_filter=False,
                        engine="openpyxl"
                    )
                else:
                    raise ValueError("not excel")
            except Exception:
                try:
                    arquivo.seek(0)
                except Exception:
                    pass
                try:
                    df = pd.read_csv(
                        arquivo, sep=';', dtype=str,
                        encoding='utf-8', keep_default_na=False, na_filter=False
                    )
                except UnicodeDecodeError:
                    arquivo.seek(0)
                    df = pd.read_csv(
                        arquivo, sep=';', dtype=str,
                        encoding='latin1', keep_default_na=False, na_filter=False
                    )

            COL_TIPO          = 0
            COL_EMPRESA_CNPJ  = 1
            COL_CONSULTOR_TXT = 2
            COL_REPRESENTANTE = 3
            COL_NOME_CLIENTE  = 4
            COL_TELEFONE      = 5

            total_inseridos, pulados = 0, 0
            erros = []
            batch_size = 100  # Processar em lotes para melhor performance
            batch_clientes = []
            
            app.logger.info(f"Iniciando importação de {len(df)} registros")

            for i, row in df.iterrows():
                try:
                    tipo          = s(get_pos(row, COL_TIPO))
                    empresa_cnpj  = so_digits(get_pos(row, COL_EMPRESA_CNPJ))
                    consultor_txt = s(get_pos(row, COL_CONSULTOR_TXT))
                    representante = s(get_pos(row, COL_REPRESENTANTE))
                    nome_cliente  = s(get_pos(row, COL_NOME_CLIENTE))

                    raw_tel = get_pos(row, COL_TELEFONE)
                    if not s(raw_tel):
                        try:
                            for colname, val in row.items():
                                if colname and 'tel' in str(colname).lower():
                                    raw_tel = val
                                    break
                        except Exception:
                            pass
                    telefone = so_digits(raw_tel)
                    telefone = telefone if telefone else None

                    # Validação de dados
                    if nome_cliente and len(nome_cliente.strip()) < 2:
                        app.logger.warning(f"Linha {i+2}: Nome do cliente muito curto: '{nome_cliente}'")
                        erros.append(f"Linha {i+2}: Nome muito curto (mínimo 2 caracteres)")
                        pulados += 1
                        continue
                    
                    if empresa_cnpj and len(empresa_cnpj) < 11:
                        app.logger.warning(f"Linha {i+2}: CNPJ inválido: {empresa_cnpj}")
                        erros.append(f"Linha {i+2}: CNPJ inválido (mínimo 11 dígitos)")
                        pulados += 1
                        continue
                    
                    if telefone and (len(telefone) < 10 or len(telefone) > 11):
                        app.logger.warning(f"Linha {i+2}: Telefone com formato inválido: {telefone}")
                        erros.append(f"Linha {i+2}: Telefone inválido (10-11 dígitos)")
                        # Não pular, apenas limpar o telefone
                        telefone = None

                    if not any([tipo, empresa_cnpj, consultor_txt, representante, nome_cliente, telefone]):
                        continue

                    if not nome_cliente:
                        pulados += 1
                        continue

                    if empresa_cnpj:
                        try:
                            existente_ativo = Cliente.query.filter_by(cnpj=empresa_cnpj, ativo=True).first()
                            if existente_ativo:
                                mudou = False
                                if telefone and (not existente_ativo.telefone or existente_ativo.telefone != telefone):
                                    existente_ativo.telefone = telefone
                                    mudou = True
                                if nome_cliente and nome_cliente != existente_ativo.nome:
                                    existente_ativo.nome = nome_cliente[:200]
                                    mudou = True
                                if representante and representante != existente_ativo.representante_nome:
                                    existente_ativo.representante_nome = representante[:200]
                                    mudou = True
                                if consultor_id and existente_ativo.consultor_id != consultor_id:
                                    existente_ativo.consultor_id = consultor_id
                                    mudou = True
                                if existente_ativo.origem != 'importado_csv':
                                    existente_ativo.origem = 'importado_csv'
                                    mudou = True

                                if mudou:
                                    total_inseridos += 1
                                else:
                                    pulados += 1
                                continue

                            existente_inativo = Cliente.query.filter_by(cnpj=empresa_cnpj, ativo=False).first()
                            if existente_inativo:
                                existente_inativo.nome = nome_cliente[:200] or existente_inativo.nome
                                existente_inativo.telefone = telefone
                                existente_inativo.representante_nome = (representante[:200] or None)
                                existente_inativo.consultor_id = consultor_id
                                existente_inativo.ativo = True
                                existente_inativo.origem = 'importado_csv'
                                total_inseridos += 1
                                continue
                        except Exception as db_error:
                            app.logger.error(f"Erro de banco ao processar linha {i+2}: {str(db_error)}")
                            erros.append(f"Linha {i+2}: Erro de banco - {str(db_error)}")
                            continue

                    # Adicionar ao batch em vez de inserir imediatamente
                    try:
                        novo = Cliente(
                            nome=nome_cliente[:200],
                            cnpj=(empresa_cnpj[:18] or None),
                            telefone=telefone,
                            representante_nome=(representante[:200] or None),
                            consultor_id=consultor_id,
                            ativo=True,
                            origem='importado_csv'
                        )
                        batch_clientes.append(novo)
                        total_inseridos += 1
                        
                        # Processar batch quando atingir o tamanho limite
                        if len(batch_clientes) >= batch_size:
                            try:
                                db.session.add_all(batch_clientes)
                                db.session.flush()  # Flush sem commit para manter transação
                                app.logger.info(f"Processado batch de {len(batch_clientes)} clientes")
                                batch_clientes = []  # Limpar batch
                            except Exception as batch_error:
                                app.logger.error(f"Erro no batch processing: {str(batch_error)}")
                                db.session.rollback()
                                # Tentar inserir um por um se batch falhar
                                for cliente in batch_clientes:
                                    try:
                                        db.session.add(cliente)
                                        db.session.flush()
                                    except Exception as single_error:
                                        app.logger.warning(f"Erro em cliente individual: {str(single_error)}")
                                        erros.append(f"Erro ao inserir cliente: {str(single_error)}")
                                batch_clientes = []
                                
                    except ValueError as val_error:
                        app.logger.warning(f"Valor inválido na linha {i+2}: {str(val_error)}")
                        erros.append(f"Linha {i+2}: Valor inválido - {str(val_error)}")
                        continue
                    except Exception as create_error:
                        app.logger.error(f"Erro ao criar cliente linha {i+2}: {str(create_error)}")
                        erros.append(f"Linha {i+2}: Erro criação - {str(create_error)}")
                        continue

                except IndexError as idx_error:
                    app.logger.warning(f"Linha {i+2} com formato inválido: {str(idx_error)}")
                    erros.append(f"Linha {i+2}: Formato inválido - colunas insuficientes")
                    continue
                except Exception as e:
                    app.logger.error(f"Erro inesperado na linha {i+2}: {str(e)}")
                    erros.append(f"Linha {i+2}: {str(e)}")
                    continue

            # Processar batch restante
            if batch_clientes:
                try:
                    db.session.add_all(batch_clientes)
                    app.logger.info(f"Processado batch final de {len(batch_clientes)} clientes")
                except Exception as final_batch_error:
                    app.logger.error(f"Erro no batch final: {str(final_batch_error)}")
                    db.session.rollback()
                    for cliente in batch_clientes:
                        try:
                            db.session.add(cliente)
                            db.session.flush()
                        except Exception as single_error:
                            app.logger.warning(f"Erro em cliente individual final: {str(single_error)}")
                            erros.append(f"Erro ao inserir cliente final: {str(single_error)}")

            try:
                imp_nome = filename or "upload"
                db.session.execute(
                    text("INSERT INTO importacoes (arquivo_nome, consultor_id, registros_importados, data_importacao) "
                         "VALUES (:n, :c, :r, :d)"),
                    {"n": imp_nome, "c": consultor_id, "r": total_inseridos, "d": datetime.now()}
                )
            except Exception as import_error:
                app.logger.warning(f"Erro ao registrar importação: {str(import_error)}")

            try:
                db.session.commit()
                app.logger.info(f"Importação concluída: {total_inseridos} inseridos, {pulados} pulados, {len(erros)} erros")
            except Exception as commit_error:
                app.logger.error(f"Erro no commit final: {str(commit_error)}")
                db.session.rollback()
                flash('Erro ao salvar dados no banco. Nenhum dado foi importado.', 'danger')
                return redirect(url_for('importar_clientes_view'))

            msg = f'Importacao concluida! Inseridos/Atualizados/Reativados: {total_inseridos} - Pulados: {pulados}'
            if erros:
                msg += f' - Erros: {len(erros)} (mostrando ate 50)'
            flash(msg, 'success')
            for e in erros[:50]:
                flash(e, "warning")

            return redirect(url_for('meus_clientes'))

        consultores = Usuario.query.filter_by(tipo='consultor', ativo=True).order_by(Usuario.nome.asc()).all()
        return render_template('importar.html', consultores=consultores)

    # =============================================================================
    # LIMPAR (INATIVAR) CLIENTES DE UM CONSULTOR
    # =============================================================================
    @app.route('/limpar-clientes-consultor', methods=['POST'])
    @login_required
    def limpar_clientes_consultor():
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401

        if current_user.tipo != 'supervisor':
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            payload = request.get_json(silent=True) or {}
            consultor_id = payload.get('consultor_id')

            if not consultor_id:
                return jsonify({"ok": False, "mensagem": "Consultor não informado"}), 400

            clientes = Cliente.query.filter_by(consultor_id=consultor_id, ativo=True).all()
            for cli in clientes:
                cli.ativo = False

            db.session.commit()
            return jsonify({"ok": True, "mensagem": f"{len(clientes)} clientes removidos com sucesso."})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    # =============================================================================
    # FILTRAR RESULTADOS POR MES/ANO (SUPERVISOR)
    # =============================================================================
    @app.route('/api/resultados-por-mes')
    @login_required
    def api_resultados_por_mes():
        if current_user.tipo != 'supervisor':
            return jsonify({"erro": "Acesso negado"}), 403

        try:
            mes = int(request.args.get('mes', datetime.now().month))
            ano = int(request.args.get('ano', datetime.now().year))

            if mes < 1 or mes > 12:
                return jsonify({"ok": False, "erro": "Mês inválido"}), 400

            inicio = datetime(ano, mes, 1)
            fim = datetime(ano + (1 if mes == 12 else 0), (1 if mes == 12 else mes + 1), 1)

            # Subquery: agrega ligações do período por consultor.
            # Feito como subquery para que consultores com 0 ligações no mês
            # (mas com histórico em outros meses) apareçam com total=0.
            subq = (
                db.session.query(
                    Ligacao.consultor_id.label('cid'),
                    func.count(Ligacao.id).label('total'),
                    func.sum(case((Ligacao.resultado == 'comprou', 1), else_=0)).label('vendas'),
                    func.sum(case((Ligacao.resultado == 'comprou', Ligacao.valor_venda), else_=0)).label('receita'),
                )
                .filter(Ligacao.data_hora >= inicio, Ligacao.data_hora < fim)
                .group_by(Ligacao.consultor_id)
                .subquery()
            )

            rows = (
                db.session.query(
                    Usuario.id,
                    Usuario.nome,
                    func.coalesce(subq.c.total, 0).label('total'),
                    func.coalesce(subq.c.vendas, 0).label('vendas'),
                    func.coalesce(subq.c.receita, 0.0).label('receita'),
                )
                .outerjoin(subq, subq.c.cid == Usuario.id)
                .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
                .order_by(desc('receita'))
                .all()
            )

            resultado = []
            for uid, nome, total, vendas, receita in rows:
                total = int(total or 0)
                vendas = int(vendas or 0)
                receita = float(receita or 0)
                conv = _percent(vendas, total) if total else 0.0
                resultado.append({
                    "id": uid,
                    "nome": nome,
                    "total_ligacoes": total,
                    "vendas": vendas,
                    "conversao": round(conv, 1),
                    "receita": receita,
                    "receita_fmt": formatar_dinheiro(receita),
                })

            return jsonify({"ok": True, "mes": mes, "ano": ano, "consultores": resultado})

        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    
    # FILTRAR MINHAS LIGACOES POR MES/ANO (CONSULTOR)

    @app.route('/api/minhas-ligacoes-por-mes')
    @login_required
    def api_minhas_ligacoes_por_mes():
        if current_user.tipo not in ('consultor', 'televendas'):
            return jsonify({"erro": "Acesso negado"}), 403
        
        try:
            mes = int(request.args.get('mes', datetime.now().month))
            ano = int(request.args.get('ano', datetime.now().year))
            
            # Buscar ligações do consultor no mês/ano específico
            ligacoes = (
                db.session.query(Ligacao)
                .filter(Ligacao.consultor_id == current_user.id)
                .filter(extract('month', Ligacao.data_hora) == mes)
                .filter(extract('year', Ligacao.data_hora) == ano)
                .order_by(Ligacao.data_hora.desc())
                .all()
            )
            
            resultado = []
            for lig in ligacoes:
                resultado.append({
                    "id": lig.id,
                    "cliente_id": lig.cliente_id,
                    "cliente_nome": lig.cliente.nome if lig.cliente else "N/A",
                    "data_hora": lig.data_hora.strftime("%d/%m/%Y %H:%M"),
                    "resultado": lig.resultado,
                    "valor_venda": float(lig.valor_venda or 0),
                    "valor_venda_fmt": formatar_dinheiro(lig.valor_venda),
                    "observacao": lig.observacao
                })
            
            # Estatísticas do mês
            total_ligacoes = len(resultado)
            vendas = len([l for l in resultado if l["resultado"] == "comprou"])
            positivos = len([l for l in resultado if l["resultado"] in ("comprou", "relacionamento", "retornar")])
            receita_total = sum([l["valor_venda"] for l in resultado if l["resultado"] == "comprou"])
            taxa_conversao = _percent(vendas, total_ligacoes) if total_ligacoes else 0
            taxa_positiva = _percent(positivos, total_ligacoes) if total_ligacoes else 0
            
            return jsonify({
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
                    "taxa_positiva": round(taxa_positiva, 1)
                }
            })
            
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    # =============================================================================
# RELATORIO POR E-MAIL
# =============================================================================
