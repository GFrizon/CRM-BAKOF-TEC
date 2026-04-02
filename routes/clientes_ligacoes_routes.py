from datetime import datetime, timedelta

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.helpers import formatar_dinheiro, get_pos, s, so_digits
from core.models import Cliente, Ligacao, Nota, SyncResumoDiario, Usuario
from routes.clientes_ligacoes.access_control import (
    bloquear_escrita_supervisor_repr,
    resposta_supervisor_repr_somente_leitura,
)
from routes.clientes_ligacoes.analytics_api import (
    consultar_ligacoes_consultor_mes,
    consultar_resultados_consultores_mes,
    parse_mes_ano,
)
from routes.clientes_ligacoes.agrupamento_view import montar_representantes_agrupados
from routes.clientes_ligacoes.badges import (
    calcular_total_inativos_badge_com_cache,
    _total_oracle_badge,
    _total_oracle_badge_supervisor_repr,
    _total_proximos_badge,
)
from routes.clientes_ligacoes.client_metrics import carregar_stats_e_locks_por_cliente_id
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
    _cliente_tem_representante_vinculado,
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
from routes.clientes_ligacoes.import_helpers import (
    carregar_dataframe_importacao,
    extrair_campos_linha,
    validar_campos_linha,
)
from routes.clientes_ligacoes.interaction_serializers import (
    serializar_detalhes_ligacao,
    serializar_historico_ligacoes,
    serializar_notas,
)
from routes.clientes_ligacoes.lista_operacional import (
    filtrar_listas_por_termo,
    ordenar_clientes_por_aba,
)
from routes.clientes_ligacoes.ligacao_helpers import (
    aplicar_payload_edicao_ligacao,
    calcular_proxima_ligacao,
    mensagem_sucesso_ligacao,
    normalizar_resultado_ligacao,
    parse_valor_venda,
)
from routes.clientes_ligacoes.lock_helpers import (
    buscar_locks_por_cd_oracle,
    extrair_cds_da_requisicao,
    tentar_assumir_lock_cliente,
)
from routes.clientes_ligacoes.oracle_sync_helpers import (
    aplicar_dados_oracle_no_cliente,
    montar_payload_cliente_oracle,
    sugerir_consultor_por_categoria_oracle,
)
from routes.clientes_ligacoes.proximos_tab import preparar_contexto_proximos_inativacao
from routes.clientes_ligacoes.proximos_totais import calcular_totais_abas_proximos
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
                _, mapa_nome_para_id_oracle = carregar_mapa_nome_para_id_usuarios_ativos()
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
                    locks_por_cliente_id, stats_ligacoes_por_cliente_id = carregar_stats_e_locks_por_cliente_id(
                        ids_locais
                    )

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

            consultores_oracle = extrair_consultores_dos_grupos(representantes_data)

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
            total_oracle, stats_oracle = calcular_stats_gerais_grupos(representantes_data)

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

            clientes_oracle_inativos = carregar_clientes_inativos_enriquecidos(app.logger)
            filtrar_inativos_por_categoria = (current_user.tipo == 'consultor')
            mapa_nome_para_id_inativos = {}
            mapa_codigo_para_id_inativos = {}
            if filtrar_inativos_por_categoria:
                _, mapa_nome_para_id_inativos = carregar_mapa_nome_para_id_usuarios_ativos()
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
                            Usuario.nome.label('usuario_nome')
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
                        cd_lock = str(row.cd_cliente_oracle or '').strip()
                        if not cd_lock:
                            continue
                        if cd_lock not in locks_por_cd_oracle:
                            locks_por_cd_oracle[cd_lock] = {
                                'ativo': True,
                                'por_nome': (row.usuario_nome or 'Outro usuario'),
                                'ate': None,
                            }
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
            
            consultores_inativos = extrair_consultores_dos_grupos(representantes_data)

            total_inativos, stats_inativos = calcular_stats_gerais_grupos(representantes_data)
            _INATIVOS_COUNT_CACHE[current_user.id] = {
                "count": total_inativos,
                "ts": datetime.now()
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
            representantes_ordenados_px, total_proximos_count, stats_proximos = (
                preparar_contexto_proximos_inativacao(
                    current_user,
                    codigos_representantes_vinculados,
                )
            )

            total_pendentes_px, total_contatados_px, total_retornar_px = calcular_totais_abas_proximos(
                current_user,
                codigos_representantes_vinculados,
            )

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
        mes_filtro, ano_filtro = parse_filtro_mes_ano(request.args, current_user.tipo)

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
            usuarios_ativos, mapa_nome_para_id = carregar_mapa_nome_para_id_usuarios_ativos()
            ids_usuarios_ativos = {u.id for u in usuarios_ativos if u and u.id}
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
            request.args.get('filtro'),
        )

        consultores = (Usuario.query
                       .filter_by(tipo='consultor', ativo=True)
                       .order_by(Usuario.nome.asc())
                       .all() if current_user.tipo == 'supervisor' else None)

        stats = montar_stats_consultor_televendas(current_user, total_oracle_badge)
        
        # Gerar lista de meses/anos disponíveis para o filtro do consultor e televendas
        meses_disponiveis_consultor = montar_meses_disponiveis(current_user.tipo)

        total_inativos_badge = calcular_total_inativos_badge_com_cache(
            current_user=current_user,
            apenas_meus=apenas_meus,
            cache_store=_INATIVOS_COUNT_CACHE,
            cache_ttl_seconds=_INATIVOS_COUNT_CACHE_TTL_SECONDS,
        )

        # Para consultores: converter para vista agrupada por representante
        # (mantendo contatados/retornar na lista simples original).
        if (
            (current_user.tipo in ('supervisor', 'consultor') and aba == 'pendentes') or
            (current_user.tipo in ('consultor', 'supervisor', 'supervisor_repr') and aba not in ('contatados', 'retornar', 'pendentes'))
        ):
            representantes_ordenados_grp = montar_representantes_agrupados(
                clientes=clientes,
                tipo_usuario=current_user.tipo,
                aba=aba,
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

            consultor_sugerido = sugerir_consultor_por_categoria_oracle(cliente_oracle.get("consultor"))

            return jsonify({
                "ok": True,
                "encontrado": True,
                "dados": montar_payload_cliente_oracle(cnpj, cliente_oracle, consultor_sugerido),
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

            aplicar_dados_oracle_no_cliente(cliente, cliente_oracle)

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
                
                aplicar_dados_oracle_no_cliente(cliente, row)
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
                return resposta_supervisor_repr_somente_leitura(
                    "Usuários do tipo Supervisor de Representante não podem criar clientes (somente visualização)."
                )

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
                return resposta_supervisor_repr_somente_leitura(
                    "Usuários do tipo Supervisor de Representante não podem iniciar contato (somente visualização)."
                )

            payload = request.get_json(silent=True) or {}
            forcar = bool(payload.get('forcar'))
            aba_contexto = str(payload.get('aba') or '').strip().lower()
            cd_oracle_payload = str(payload.get('cd_cliente_oracle') or '').strip()

            cli = db.session.get(Cliente, cliente_id)
            if not cli:
                return jsonify({"ok": False, "mensagem": "Cliente no encontrado."}), 404

            if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permisso para este cliente."}), 403

            ok_lock, conflito = tentar_assumir_lock_cliente(
                cli=cli,
                current_user_id=current_user.id,
                aba_contexto=aba_contexto,
                cd_oracle_payload=cd_oracle_payload,
                forcar=forcar,
            )
            if not ok_lock and conflito:
                return jsonify(conflito), 409
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

            cds = extrair_cds_da_requisicao(request)

            if not cds:
                return jsonify({"ok": True, "locks": {}})

            locks = buscar_locks_por_cd_oracle(cds)

            return jsonify({"ok": True, "locks": locks})
        except Exception as e:
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route('/registrar-ligacao/<int:cliente_id>', methods=['POST'])
    def registrar_ligacao(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401

        # Bloquear supervisor_repr de registrar ligações
        if current_user.tipo == 'supervisor_repr':
            return resposta_supervisor_repr_somente_leitura(
                "Usuários do tipo Supervisor de Representante não podem registrar ligações (somente visualização)."
            )

        try:
            payload = request.get_json(silent=True) or {}
            obs = s(payload.get('observacao'))
            contato_nome = s(payload.get('contato_nome'))
            resultado = normalizar_resultado_ligacao(s(payload.get('resultado') or 'nao_comprou'))
            valor_venda = parse_valor_venda(payload.get('valor_venda'))

            cli = db.session.get(Cliente, cliente_id)
            if not cli:
                return jsonify({"ok": False, "mensagem": "Cliente não encontrado."}), 404

            if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permissão para este cliente."}), 403

            agora = datetime.now()

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

            data_retorno = s(payload.get('data_retorno'))
            cli.proxima_ligacao = calcular_proxima_ligacao(
                agora=agora,
                resultado=resultado,
                data_retorno_raw=data_retorno,
                dias_retorno_raw=payload.get('dias_retorno'),
            )

            # Libera trava de atendimento apos registrar a ligacao.
            cli.em_atendimento_por = None
            cli.em_atendimento_ate = None

            db.session.commit()

            msg = mensagem_sucesso_ligacao(resultado, cli.proxima_ligacao)

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
                return resposta_supervisor_repr_somente_leitura(
                    "Usuários do tipo Supervisor de Representante não podem editar observações (somente visualização)."
                )

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
                return resposta_supervisor_repr_somente_leitura(
                    "Usuários do tipo Supervisor de Representante não podem editar ligações (somente visualização)."
                )

            ligacao = db.session.get(Ligacao, ligacao_id)
            if not ligacao:
                return jsonify({"ok": False, "mensagem": "Ligação não encontrada"}), 404
            
            # Verificar permissão: consultor e televendas só podem editar suas próprias ligações
            if current_user.tipo == 'consultor' and ligacao.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permissão para editar esta ligação"}), 403
            
            payload = request.get_json(silent=True) or {}
            aplicar_payload_edicao_ligacao(ligacao, payload, s)
            
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
            
            return jsonify(serializar_detalhes_ligacao(ligacao, formatar_dinheiro))
            
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

            return jsonify(
                serializar_historico_ligacoes(
                    registros=regs,
                    current_user_tipo=current_user.tipo,
                    current_user_id=current_user.id,
                    normalizador_texto=s,
                    formatar_dinheiro_fn=formatar_dinheiro,
                )
            )

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
        return jsonify(serializar_notas(notas))


    @app.route('/clientes/<int:cliente_id>/notas', methods=['POST'])
    def adicionar_nota(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401
        
        # Bloquear supervisor_repr de adicionar notas
        if current_user.tipo == 'supervisor_repr':
            return resposta_supervisor_repr_somente_leitura(
                "Usuários do tipo Supervisor de Representante não podem adicionar notas (somente visualização)."
            )
        
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

            df = carregar_dataframe_importacao(arquivo, ext)

            total_inseridos, pulados = 0, 0
            erros = []
            batch_size = 100  # Processar em lotes para melhor performance
            batch_clientes = []
            
            app.logger.info(f"Iniciando importação de {len(df)} registros")

            for i, row in df.iterrows():
                try:
                    campos = extrair_campos_linha(row, get_pos, s, so_digits)
                    tipo = campos.get("tipo")
                    empresa_cnpj = campos.get("empresa_cnpj")
                    representante = campos.get("representante")
                    nome_cliente = campos.get("nome_cliente")
                    valido, telefone, erro_validacao = validar_campos_linha(campos)
                    if not valido:
                        if erro_validacao:
                            if erro_validacao.startswith("Nome muito curto"):
                                app.logger.warning(f"Linha {i+2}: Nome do cliente muito curto: '{nome_cliente}'")
                            elif erro_validacao.startswith("CNPJ inválido"):
                                app.logger.warning(f"Linha {i+2}: CNPJ inválido: {empresa_cnpj}")
                            elif erro_validacao.startswith("Nome vazio"):
                                pass
                            erros.append(f"Linha {i+2}: {erro_validacao}")
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
            mes, ano = parse_mes_ano(request.args)
            payload, status = consultar_resultados_consultores_mes(mes, ano)
            return jsonify(payload), status

        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    
    # FILTRAR MINHAS LIGACOES POR MES/ANO (CONSULTOR)

    @app.route('/api/minhas-ligacoes-por-mes')
    @login_required
    def api_minhas_ligacoes_por_mes():
        if current_user.tipo not in ('consultor', 'televendas'):
            return jsonify({"erro": "Acesso negado"}), 403
        
        try:
            mes, ano = parse_mes_ano(request.args)
            return jsonify(consultar_ligacoes_consultor_mes(current_user.id, mes, ano))
            
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    # =============================================================================
# RELATORIO POR E-MAIL
# =============================================================================
