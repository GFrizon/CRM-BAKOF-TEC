from datetime import datetime, timedelta

from flask import jsonify, request
from flask_login import current_user, login_required

from core.extensions import db
from core.models import Cliente, Usuario
from oracle_service import get_clientes_oracle, test_oracle_connection


def register_oracle_routes(app):
    @app.route('/test-oracle')
    @login_required
    def test_oracle_route():
        """Rota de teste para conexão Oracle"""
        if current_user.tipo != 'supervisor':
            return jsonify({"erro": "Acesso permitido somente para supervisores"}), 403

        try:
            success, message = test_oracle_connection()
            return jsonify({
                "success": success,
                "message": message,
                "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao testar conexão: {str(e)}"
            }), 500

    @app.route('/oracle-clientes-alvo')
    @login_required
    def oracle_clientes_alvo_route():
        """Rota para testar busca de clientes alvo no Oracle"""
        if current_user.tipo != 'supervisor':
            return jsonify({"erro": "Acesso permitido somente para supervisores"}), 403

        try:
            clientes = get_clientes_oracle()
            return jsonify({
                "success": True,
                "total": len(clientes),
                "clientes": clientes[:5],  # Mostra só os 5 primeiros para teste
                "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao buscar clientes: {str(e)}"
            }), 500

    @app.route('/sincronizar-oracle', methods=['POST'])
    @login_required
    def sincronizar_oracle():
        """Rota para sincronizar clientes do Oracle com o MySQL"""
        if current_user.tipo != 'supervisor':
            return jsonify({"erro": "Acesso permitido somente para supervisores"}), 403

        try:
            clientes_oracle = get_clientes_oracle()

            sincronizados = 0
            atualizados = 0
            erros = []

            for cliente_oracle in clientes_oracle:
                try:
                    cd_cliente = str(cliente_oracle.get('cd_cliente', ''))
                    if not cd_cliente:
                        continue

                    cliente_mysql = Cliente.query.filter_by(cd_cliente_oracle=cd_cliente).first()

                    if cliente_mysql:
                        cliente_mysql.categoria_consultor = cliente_oracle.get('consultor')
                        cliente_mysql.conceito = cliente_oracle.get('conceito')
                        cliente_mysql.representante_oracle = cliente_oracle.get('representante')
                        cliente_mysql.valor_ultimo_pedido = cliente_oracle.get('total_pedido')
                        cliente_mysql.situacao_ultimo_pedido = cliente_oracle.get('situacao')

                        dt_pedido = cliente_oracle.get('dt_pedido')
                        if dt_pedido:
                            cliente_mysql.ultimo_pedido_oracle = dt_pedido

                        cliente_mysql.data_ultima_sincronizacao = datetime.now()
                        atualizados += 1
                    else:
                        nome_consultor = cliente_oracle.get('consultor', '')
                        consultor = None

                        if nome_consultor:
                            if ' - ' in nome_consultor:
                                nome_consultor = nome_consultor.split(' - ', 1)[1]

                            consultor = Usuario.query.filter_by(
                                nome=nome_consultor.strip(),
                                tipo='consultor',
                                ativo=True
                            ).first()

                        if not consultor:
                            consultor = current_user

                        novo_cliente = Cliente(
                            nome=cliente_oracle.get('cliente', '')[:200],
                            cd_cliente_oracle=cd_cliente,
                            categoria_consultor=cliente_oracle.get('consultor'),
                            conceito=cliente_oracle.get('conceito'),
                            representante_oracle=cliente_oracle.get('representante'),
                            valor_ultimo_pedido=cliente_oracle.get('total_pedido'),
                            situacao_ultimo_pedido=cliente_oracle.get('situacao'),
                            consultor_id=consultor.id,
                            origem='importado_csv',
                            ativo=True
                        )

                        dt_pedido = cliente_oracle.get('dt_pedido')
                        if dt_pedido:
                            novo_cliente.ultimo_pedido_oracle = dt_pedido

                        novo_cliente.data_ultima_sincronizacao = datetime.now()
                        db.session.add(novo_cliente)
                        sincronizados += 1

                except Exception as e:
                    erros.append(f"Cliente {cd_cliente}: {str(e)}")
                    continue

            db.session.commit()

            return jsonify({
                "success": True,
                "message": "Sincronização concluída com sucesso!",
                "total_oracle": len(clientes_oracle),
                "sincronizados": sincronizados,
                "atualizados": atualizados,
                "erros": len(erros),
                "detalhes_erros": erros[:5],
                "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            })

        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao buscar detalhes: {str(e)}"
            }), 500

    @app.route('/detalhes-cliente-oracle/<int:cliente_id>')
    @login_required
    def detalhes_cliente_oracle(cliente_id: int):
        """Busca detalhes completos do cliente Oracle"""
        try:
            cliente = db.session.get(Cliente, cliente_id)
            if not cliente:
                return jsonify({"success": False, "message": "Cliente não encontrado"}), 404

            if current_user.tipo == 'consultor' and cliente.consultor_id != current_user.id:
                return jsonify({"success": False, "message": "Sem permissão para este cliente"}), 403

            if not cliente.cd_cliente_oracle:
                return jsonify({
                    "success": True,
                    "cliente": {
                        "id": cliente.id,
                        "nome": cliente.nome,
                        "cnpj": cliente.cnpj,
                        "telefone": cliente.telefone,
                        "telefone2": cliente.telefone2,
                        "representante_nome": cliente.representante_nome,
                        "origem": "manual"
                    },
                    "pedidos_oracle": [],
                    "mensagem": "Cliente não possui dados Oracle"
                })

            from oracle_service import get_itens_cliente_oracle, get_pedidos_cliente_oracle
            pedidos_oracle = get_pedidos_cliente_oracle(cliente.cd_cliente_oracle)
            itens_oracle = get_itens_cliente_oracle(cliente.cd_cliente_oracle)

            return jsonify({
                "success": True,
                "cliente": {
                    "id": cliente.id,
                    "nome": cliente.nome,
                    "cnpj": cliente.cnpj,
                    "telefone": cliente.telefone,
                    "telefone2": cliente.telefone2,
                    "cd_cliente_oracle": cliente.cd_cliente_oracle,
                    "categoria_consultor": cliente.categoria_consultor,
                    "conceito": cliente.conceito,
                    "ultimo_pedido_oracle": cliente.ultimo_pedido_oracle.strftime('%d/%m/%Y') if cliente.ultimo_pedido_oracle else None,
                    "valor_ultimo_pedido": float(cliente.valor_ultimo_pedido) if cliente.valor_ultimo_pedido else None,
                    "situacao_ultimo_pedido": cliente.situacao_ultimo_pedido,
                    "representante_oracle": cliente.representante_oracle,
                    "data_ultima_sincronizacao": cliente.data_ultima_sincronizacao.strftime('%d/%m/%Y %H:%M') if cliente.data_ultima_sincronizacao else None
                },
                "pedidos_oracle": pedidos_oracle,
                "itens_oracle": itens_oracle,
                "total_pedidos": len(pedidos_oracle),
                "total_itens": len(itens_oracle)
            })

        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao buscar detalhes: {str(e)}"
            }), 500

    @app.route('/test-filtros-oracle')
    @login_required
    def test_filtros_oracle():
        """Rota de teste para verificar filtros Oracle"""
        if current_user.tipo != 'supervisor':
            return "Acesso apenas para supervisores", 403

        periodo_oracle = request.args.get('periodo_oracle')
        conceito_filtro = request.args.get('conceito_filtro')
        consultor_filtro = request.args.get('consultor_filtro')

        q = Cliente.query.filter(
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ativo == True
        )

        if periodo_oracle:
            try:
                dias = int(periodo_oracle)
                data_limite = datetime.now() - timedelta(days=dias)
                q = q.filter(Cliente.ultimo_pedido_oracle <= data_limite)
            except ValueError:
                pass

        if conceito_filtro:
            q = q.filter(Cliente.conceito == conceito_filtro)

        if consultor_filtro:
            q = q.filter(Cliente.categoria_consultor.like(f'%{consultor_filtro}%'))

        clientes = q.all()

        html = f"""
        <h2>Teste de Filtros Oracle</h2>
        <p><strong>Filtros aplicados:</strong></p>
        <ul>
            <li>Período: {periodo_oracle or 'Todos'}</li>
            <li>Conceito: {conceito_filtro or 'Todos'}</li>
            <li>Consultor: {consultor_filtro or 'Todos'}</li>
        </ul>
        <p><strong>Resultados: {len(clientes)} clientes</strong></p>
        <table border="1" style="border-collapse: collapse; width: 100%;">
            <tr>
                <th>Nome</th>
                <th>Conceito</th>
                <th>Consultor</th>
                <th>Último Pedido</th>
            </tr>
        """

        for c in clientes[:10]:
            html += f"""
            <tr>
                <td>{c.nome}</td>
                <td>{c.conceito or '-'}</td>
                <td>{c.categoria_consultor or '-'}</td>
                <td>{c.ultimo_pedido_oracle or '-'}</td>
            </tr>
            """

        html += "</table>"

        if len(clientes) > 10:
            html += f"<p><em>Mostrando 10 de {len(clientes)} resultados</em></p>"

        return html

    @app.route('/sincronizar-oracle-async', methods=['POST'])
    @login_required
    def sincronizar_oracle_manual():
        """Sincronização manual dos clientes Oracle"""
        if current_user.tipo != 'supervisor':
            return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403

        try:
            from sincronizacao_automatica import sincronizacao_automatica_diaria

            import threading

            def sincronizar_background():
                try:
                    sincronizacao_automatica_diaria()
                except Exception as e:
                    print(f"Erro na sincronização background: {e}")

            thread = threading.Thread(target=sincronizar_background)
            thread.daemon = True
            thread.start()

            return jsonify({
                "ok": True,
                "mensagem": "Sincronização iniciada com sucesso! Aguarde alguns minutos para ver os resultados."
            })

        except Exception as e:
            return jsonify({"ok": False, "mensagem": f"Erro ao iniciar sincronização: {str(e)}"}), 500
