from datetime import datetime, timedelta

import pandas as pd
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, desc, extract, func, or_, text
from sqlalchemy.orm import joinedload

from core.extensions import db
from core.helpers import _percent, formatar_dinheiro, get_pos, s, so_digits
from core.models import Cliente, Ligacao, Nota, Usuario
from routes.supervisor_routes import get_banners_ativos


def register_clientes_ligacoes_routes(app):
    # =============================================================================
    # LISTAGEM DE CLIENTES
    # =============================================================================
    @app.route('/meus-clientes')
    def meus_clientes():
        if not current_user.is_authenticated:
            return redirect(url_for('login'))

        if current_user.tipo not in ('consultor', 'supervisor'):
            flash('Perfil sem acesso.', 'danger')
            return redirect(url_for('index'))

        aba = request.args.get('aba', 'pendentes')
        apenas_meus = True if current_user.tipo == 'consultor' else (request.args.get('meus') == '1')
        
        # Tratar aba Oracle
        if aba == 'oracle':
            # Para aba Oracle, mostrar apenas clientes com cd_cliente_oracle
            q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(
                Cliente.cd_cliente_oracle.isnot(None),
                Cliente.ativo == True
            )
            if apenas_meus:
                q = q.filter(Cliente.consultor_id == current_user.id)
            
            # Aplicar filtros Oracle
            periodo_oracle = request.args.get('periodo_oracle')
            conceito_filtro = request.args.get('conceito_filtro')
            consultor_filtro = request.args.get('consultor_filtro')
            
            # Filtro por período sem compra
            if periodo_oracle:
                try:
                    dias = int(periodo_oracle)
                    data_limite = datetime.now() - timedelta(days=dias)
                    q = q.filter(Cliente.ultimo_pedido_oracle <= data_limite)
                    app.logger.info(f"Filtro período aplicado: {dias} dias (data limite: {data_limite})")
                except ValueError:
                    app.logger.warning(f"Valor inválido para período: {periodo_oracle}")
                    pass  # Ignora se o valor não for válido
            
            if conceito_filtro:
                q = q.filter(Cliente.conceito == conceito_filtro)
                app.logger.info(f"Filtro conceito aplicado: {conceito_filtro}")
            
            if consultor_filtro:
                q = q.filter(Cliente.categoria_consultor.like(f'%{consultor_filtro}%'))
                app.logger.info(f"Filtro consultor aplicado: {consultor_filtro}")
            
            termo = s(request.args.get('q'))
            if termo:
                like = f"%{termo}%"
                q = q.filter(or_(
                    Cliente.nome.like(like),
                    Cliente.cnpj.like(like),
                    Cliente.telefone.like(like),
                    Cliente.representante_nome.like(like),
                    Cliente.categoria_consultor.like(like),
                    Cliente.conceito.like(like)
                ))
            
            # Manter ordenação original por nome (será feita após cálculo)
            clientes_oracle = q.order_by(Cliente.nome.asc()).all()
            
            # Agrupar clientes por representante_oracle
            representantes_data = {}
            for c in clientes_oracle:
                # Obter nome do representante Oracle
                representante = c.representante_oracle or 'SEM REPRESENTANTE'
                
                # Criar entrada para o representante se não existir
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
                        'consultores_internos': {}  # Mantém sincronização com consultores
                    }
                
                # Preparar dados do cliente mantendo sincronização
                ligs = sorted(c.ligacoes, key=lambda x: x.data_hora, reverse=True)
                ultima = ligs[0] if ligs else None
                total = len(ligs)
                
                dados_cliente = {
                    "id": c.id,
                    "nome": c.nome,
                    "cnpj": c.cnpj,
                    "telefone": c.telefone,
                    "representante_nome": c.representante_nome,
                    "ultima_ligacao": ultima.data_hora if ultima else None,
                    "total_ligacoes": total,
                    "proxima_ligacao": c.proxima_ligacao,
                    "origem": getattr(c, 'origem', None),
                    "cd_cliente_oracle": c.cd_cliente_oracle,
                    "categoria_consultor": c.categoria_consultor,  # Mantido!
                    "consultor_id": c.consultor_id,  # Mantido!
                    "conceito": c.conceito,
                    "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
                    "valor_ultimo_pedido": c.valor_ultimo_pedido,
                    "valor_total_365dias": c.valor_total_365dias,  # NOVO: Valor total 365 dias
                    "situacao_ultimo_pedido": c.situacao_ultimo_pedido,
                    "representante_oracle": c.representante_oracle,
                }
                
                # Adicionar cliente ao representante
                representantes_data[representante]['clientes'].append(dados_cliente)
                
                # Agrupar por consultor interno (opcional, para estatísticas)
                if c.consultor:
                    nome_consultor = c.consultor.nome
                    if nome_consultor not in representantes_data[representante]['consultores_internos']:
                        representantes_data[representante]['consultores_internos'][nome_consultor] = 0
                    representantes_data[representante]['consultores_internos'][nome_consultor] += 1
            
            # Calcular estatísticas por representante e ordenar clientes por valor
            for representante, dados in representantes_data.items():
                clientes_rep = dados['clientes']
                
                # Estatísticas básicas
                dados['total_clientes'] = len(clientes_rep)
                dados['liberados'] = sum(1 for c in clientes_rep if c.get('conceito') == 'LIBERADO')
                dados['inadimplentes'] = sum(1 for c in clientes_rep if c.get('conceito') == 'INADIMPLENTE')
                dados['sem_conceito'] = sum(1 for c in clientes_rep if c.get('conceito') in ['SEM CONCEITO', None])
                
                # Ticket médio
                valores = [c.get('valor_ultimo_pedido', 0) for c in clientes_rep if c.get('valor_ultimo_pedido')]
                dados['ticket_medio'] = sum(valores) / len(valores) if valores else 0
                
                # Dias médio sem pedido
                hoje = datetime.now()
                dias_sem_pedido = []
                for c in clientes_rep:
                    if c.get('ultimo_pedido_oracle'):
                        dias = (hoje - c['ultimo_pedido_oracle']).days
                        dias_sem_pedido.append(dias)
                dados['dias_medio'] = sum(dias_sem_pedido) / len(dias_sem_pedido) if dias_sem_pedido else 0
                
                # 🎯 ORDENAR CLIENTES POR VALOR TOTAL 365 DIAS (maior para menor)
                dados['clientes'] = sorted(
                    clientes_rep, 
                    key=lambda x: (
                        float(x.get('valor_total_365dias') or 0),  # Valor 365 dias
                        float(x.get('valor_ultimo_pedido') or 0)  # Backup: último pedido
                    ), 
                    reverse=True
                )
            
            # Converter para lista ordenada por número de clientes (maior para menor)
            representantes_ordenados = sorted(
                representantes_data.items(), 
                key=lambda x: x[1]['total_clientes'], 
                reverse=True
            )
            
            # Obter lista de consultores únicos para filtro
            consultores_oracle = []
            if representantes_data:
                consultores_set = set()
                for representante, dados in representantes_data.items():
                    for c in dados['clientes']:
                        if c.get('categoria_consultor'):
                            consultores_set.add(c.get('categoria_consultor'))
                
                # Criar objetos de consultor para o template
                for nome in sorted(consultores_set):
                    consultores_oracle.append({'nome': nome})
            
            # Calcular totais para as outras abas
            todos_clientes = Cliente.query.filter_by(ativo=True)
            if apenas_meus:
                todos_clientes = todos_clientes.filter(Cliente.consultor_id == current_user.id)
            
            total_pendentes = todos_clientes.filter(Cliente.id.notin_(
                db.session.query(Ligacao.cliente_id).filter(
                    Ligacao.consultor_id == current_user.id if apenas_meus else True
                )
            )).count()
            
            total_contatados = todos_clientes.filter(Cliente.id.in_(
                db.session.query(Ligacao.cliente_id).filter(
                    Ligacao.consultor_id == current_user.id if apenas_meus else True
                )
            )).filter(Cliente.proxima_ligacao.is_(None)).count()
            
            total_retornar = todos_clientes.filter(Cliente.proxima_ligacao.isnot(None)).count()
            total_oracle = sum(len(dados['clientes']) for dados in representantes_data.values())
            
            # Calcular estatísticas Oracle gerais (de todos os representantes)
            stats_oracle = {}
            total_clientes_oracle = 0
            total_liberados = 0
            total_inadimplentes = 0
            total_sem_conceito = 0
            todos_valores = []
            todos_dias = []
            
            for representante, dados in representantes_data.items():
                clientes_rep = dados['clientes']
                total_clientes_oracle += len(clientes_rep)
                total_liberados += dados['liberados']
                total_inadimplentes += dados['inadimplentes']
                total_sem_conceito += dados['sem_conceito']
                
                # Coletar valores e dias para cálculos gerais
                for c in clientes_rep:
                    if c.get('valor_ultimo_pedido'):
                        todos_valores.append(c.get('valor_ultimo_pedido'))
                    if c.get('ultimo_pedido_oracle'):
                        dias = (datetime.now() - c['ultimo_pedido_oracle']).days
                        todos_dias.append(dias)
            
            # Calcular estatísticas gerais
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
            
            # Renderizar template com dados Oracle
            return render_template('meus_clientes.html',
                                 representantes=representantes_ordenados,  # Nova variável
                                 aba=aba,
                                 total_pendentes=total_pendentes,
                                 total_contatados=total_contatados,
                                 total_retornar=total_retornar,
                                 total_oracle=total_oracle,
                                 is_supervisor=current_user.tipo == 'supervisor',
                                 stats={},
                                 stats_oracle=stats_oracle,
                                 consultores_oracle=consultores_oracle,
                                 q=request.args.get('q', ''),
                                 meses_disponiveis_consultor=[],
                                 mes_filtro=None,
                                 ano_filtro=None)
        
        # Parâmetros de filtro mensal para consultores
        mes_filtro = None
        ano_filtro = None
        if current_user.tipo == 'consultor':
            mes_filtro = request.args.get('mes')
            ano_filtro = request.args.get('ano')
            if mes_filtro:
                mes_filtro = int(mes_filtro)
            if ano_filtro:
                ano_filtro = int(ano_filtro)

        q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
        if apenas_meus:
            q = q.filter(Cliente.consultor_id == current_user.id)

        termo = s(request.args.get('q'))
        if termo:
            like = f"%{termo}%"
            q = q.filter(or_(
                Cliente.nome.like(like),
                Cliente.cnpj.like(like),
                Cliente.telefone.like(like),
                Cliente.representante_nome.like(like)
            ))

        clientes_todos = q.order_by(Cliente.nome.asc()).all()

        pendentes, contatados, precisa_retornar = [], [], []
        agora = datetime.now()

        for c in clientes_todos:
            ligs = sorted(c.ligacoes, key=lambda x: x.data_hora, reverse=True)
            ultima = ligs[0] if ligs else None
            total = len(ligs)
            dados = {
                "id": c.id,
                "nome": c.nome,
                "cnpj": c.cnpj,
                "telefone": c.telefone,
                "representante_nome": c.representante_nome,
                "ultima_ligacao": ultima.data_hora if ultima else None,
                "total_ligacoes": total,
                "proxima_ligacao": c.proxima_ligacao,
                "origem": getattr(c, 'origem', None),
            }

            if total == 0:
                pendentes.append(dados)
            else:
                if c.proxima_ligacao:
                    dados["retorno_atrasado"] = (agora >= c.proxima_ligacao)
                    precisa_retornar.append(dados)
                else:
                    contatados.append(dados)

        if aba == 'pendentes':
            clientes = pendentes
        elif aba == 'retornar':
            clientes = sorted(precisa_retornar, key=lambda x: (x['proxima_ligacao'] or datetime.max))
        else:
            clientes = contatados
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
        if current_user.tipo == 'consultor':
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

            stats['taxa_conversao'] = round(
                (vendas_30 / stats['ligacoes_mes'] * 100) if stats['ligacoes_mes'] > 0 else 0, 1
            )

            receita_total = db.session.query(func.sum(Ligacao.valor_venda)).filter(
                Ligacao.consultor_id == current_user.id,
                Ligacao.data_hora >= desde30,
                Ligacao.resultado == 'comprou'
            ).scalar() or 0

            def _fmt_money(v):
                try:
                    v = float(v or 0)
                    return f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
                except:
                    return "0,00"

            stats['receita_mes'] = _fmt_money(receita_total)
        
        # Gerar lista de meses/anos disponíveis para o filtro do consultor
        meses_disponiveis_consultor = []
        if current_user.tipo == 'consultor':
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

        return render_template(
            'meus_clientes.html',
            clientes=clientes,
            total_pendentes=len(pendentes),
            total_contatados=len(contatados),
            total_retornar=len(precisa_retornar),
            aba=aba,
            is_supervisor=(current_user.tipo == 'supervisor'),
            now=datetime.now,
            consultores=consultores,
            stats=stats,
            mostrar_novidades=not current_user.viu_novidades,  # NOVO
            banners_ativos=get_banners_ativos(),  # BANNERS
            # Filtro mensal para consultores
            mes_filtro=mes_filtro,
            ano_filtro=ano_filtro,
            meses_disponiveis_consultor=meses_disponiveis_consultor
        )

    # =============================================================================
    # CRIAR CLIENTE MANUALMENTE
    # =============================================================================
    @app.route('/clientes/criar', methods=['POST'])
    @login_required
    def criar_cliente_manual():
        try:
            payload = request.get_json(silent=True) or {}
            nome = s(payload.get('nome'))
            cnpj = so_digits(payload.get('cnpj')) or None
            telefone = so_digits(payload.get('telefone')) or None
            representante = s(payload.get('representante_nome')) or None

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
                    existente.representante_nome = representante
                    existente.consultor_id = consultor_id
                    existente.ativo = True
                    existente.origem = 'manual'
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
                representante_nome=representante,
                consultor_id=consultor_id,
                ativo=True,
                origem='manual'
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
    # REGISTRAR LIGAÇÃO
    # =============================================================================
    @app.route('/registrar-ligacao/<int:cliente_id>', methods=['POST'])
    def registrar_ligacao(cliente_id: int):
        if not current_user.is_authenticated:
            return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401

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
            else:
                # Se não preencher nada, não agenda retorno
                cli.proxima_ligacao = None

            db.session.commit()

            msg = "Ligação registrada!"
            if cli.proxima_ligacao:
                msg = "Ligação registrada! Cliente marcado para retorno."
            elif resultado == 'comprou':
                msg = "Ligação registrada! Venda marcada como 'comprou'."

            return jsonify({"ok": True, "mensagem": msg})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    # =============================================================================
    # EDITAR OBSERVAÇÃO DE LIGAÇÃO
    # =============================================================================
    @app.route('/editar-observacao/<int:ligacao_id>', methods=['POST'])
    @login_required
    def editar_observacao(ligacao_id: int):
        try:
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
    # EDITAR LIGAÇÃO COMPLETA (RESULTADO, VALOR, OBSERVAÇÃO)
    # =============================================================================
    @app.route('/editar-ligacao/<int:ligacao_id>', methods=['POST'])
    @login_required
    def editar_ligacao(ligacao_id: int):
        try:
            ligacao = db.session.get(Ligacao, ligacao_id)
            if not ligacao:
                return jsonify({"ok": False, "mensagem": "Ligação não encontrada"}), 404
            
            # Verificar permissão: consultor só pode editar suas próprias ligações
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
    # OBTER DETALHES DA LIGAÇÃO PARA EDIÇÃO
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
    # HISTÓRICO LIGAÇÕES
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
                        "id": r.id,  # 🆕 NOVO: incluir ID da ligação
                        "data_hora": dt,
                        "consultor": consultor_nome,
                        "contato_nome": contato,
                        "resultado": resultado,
                        "valor_venda": formatar_dinheiro(valor_num),
                        "observacao": s(r.observacao),
                        "pode_editar": (current_user.tipo == 'supervisor' or r.consultor_id == current_user.id)  # 🆕 NOVO
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
    # IMPORTAÇÃO DE CLIENTES
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

            msg = f'Importação concluída! Inseridos/Atualizados/Reativados: {total_inseridos} • Pulados: {pulados}'
            if erros:
                msg += f' • Erros: {len(erros)} (mostrando até 50)'
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
    # 🆕 FILTRAR RESULTADOS POR MÊS/ANO (SUPERVISOR)
    # =============================================================================
    @app.route('/api/resultados-por-mes')
    @login_required
    def api_resultados_por_mes():
        if current_user.tipo != 'supervisor':
            return jsonify({"erro": "Acesso negado"}), 403
        
        try:
            mes = int(request.args.get('mes', datetime.now().month))
            ano = int(request.args.get('ano', datetime.now().year))
            
            # Buscar ligações do mês/ano específico
            ligacoes = (
                db.session.query(
                    Usuario.id,
                    Usuario.nome,
                    func.count(Ligacao.id).label("total_ligacoes"),
                    func.sum(case((Ligacao.resultado == 'comprou', 1), else_=0)).label("vendas"),
                    func.sum(case((Ligacao.resultado == 'comprou', Ligacao.valor_venda), else_=0)).label("receita")
                )
                .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
                .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
                .filter(or_(
                    extract('month', Ligacao.data_hora) == mes,
                    Ligacao.id == None
                ))
                .filter(or_(
                    extract('year', Ligacao.data_hora) == ano,
                    Ligacao.id == None
                ))
                .group_by(Usuario.id, Usuario.nome)
                .order_by(desc("receita"))
                .all()
            )
            
            resultado = []
            for uid, nome, total, vendas, receita in ligacoes:
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
                    "receita_fmt": formatar_dinheiro(receita)
                })
            
            return jsonify({
                "ok": True,
                "mes": mes,
                "ano": ano,
                "consultores": resultado
            })
            
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    # =============================================================================
    # 🆕 FILTRAR MINHAS LIGAÇÕES POR MÊS/ANO (CONSULTOR)
    # =============================================================================
    @app.route('/api/minhas-ligacoes-por-mes')
    @login_required
    def api_minhas_ligacoes_por_mes():
        if current_user.tipo != 'consultor':
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
            receita_total = sum([l["valor_venda"] for l in resultado if l["resultado"] == "comprou"])
            taxa_conversao = _percent(vendas, total_ligacoes) if total_ligacoes else 0
            
            return jsonify({
                "ok": True,
                "mes": mes,
                "ano": ano,
                "ligacoes": resultado,
                "estatisticas": {
                    "total_ligacoes": total_ligacoes,
                    "vendas": vendas,
                    "receita_total": receita_total,
                    "receita_fmt": formatar_dinheiro(receita_total),
                    "taxa_conversao": round(taxa_conversao, 1)
                }
            })
            
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    # =============================================================================`r`n# RELATÓRIO POR E-MAIL`r`n# =============================================================================
