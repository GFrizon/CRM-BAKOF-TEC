from datetime import datetime, timedelta

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from core.extensions import db
from core.helpers import s
from core.models import Cliente, Ligacao


def register_account_client_routes(app):
    @app.route('/minha-conta')
    @login_required
    def minha_conta():
        stats = {}

        if current_user.tipo == 'consultor':
            hoje = datetime.now().date()
            desde30 = datetime.now() - timedelta(days=30)

            stats['total_clientes'] = Cliente.query.filter_by(
                consultor_id=current_user.id,
                ativo=True
            ).count()

            stats['total_ligacoes'] = Ligacao.query.filter(
                Ligacao.consultor_id == current_user.id,
                Ligacao.data_hora >= desde30
            ).count()

            stats['ligacoes_hoje'] = Ligacao.query.filter(
                Ligacao.consultor_id == current_user.id,
                func.date(Ligacao.data_hora) == hoje
            ).count()

            meta = current_user.meta_diaria or 10
            stats['progresso_meta'] = round(
                (stats['ligacoes_hoje'] / meta * 100) if meta > 0 else 0,
                1
            )

        return render_template('minha_conta.html', **stats)

    @app.route('/alterar-senha', methods=['POST'])
    @login_required
    def alterar_senha():
        try:
            payload = request.get_json(silent=True) or {}
            senha_atual = payload.get('senha_atual') or ""
            nova_senha = payload.get('nova_senha') or ""
            confirma_senha = payload.get('confirma_senha') or ""

            if not senha_atual or not nova_senha or not confirma_senha:
                return jsonify({"ok": False, "mensagem": "Todos os campos são obrigatórios"}), 400

            if not check_password_hash(current_user.senha_hash, senha_atual):
                return jsonify({"ok": False, "mensagem": "Senha atual incorreta"}), 400

            if nova_senha != confirma_senha:
                return jsonify({"ok": False, "mensagem": "As senhas não conferem"}), 400

            if len(nova_senha) < 6:
                return jsonify({"ok": False, "mensagem": "A nova senha deve ter no mínimo 6 caracteres"}), 400

            current_user.senha_hash = generate_password_hash(nova_senha)
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Senha alterada com sucesso!"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route('/api/busca-clientes')
    @login_required
    def api_busca_clientes():
        if not current_user.is_authenticated:
            return jsonify({"erro": "Não autenticado"}), 401

        try:
            termo = s(request.args.get('q'))
            aba = request.args.get('aba', 'pendentes')
            apenas_meus = True if current_user.tipo == 'consultor' else (request.args.get('meus') == '1')

            q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
            if apenas_meus:
                q = q.filter(Cliente.consultor_id == current_user.id)

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
                    "ultima_ligacao": ultima.data_hora.strftime("%d/%m/%Y %H:%M") if ultima else None,
                    "total_ligacoes": total,
                    "proxima_ligacao": c.proxima_ligacao.strftime("%d/%m/%Y %H:%M") if c.proxima_ligacao else None,
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

            return jsonify({
                "ok": True,
                "clientes": clientes,
                "total": len(clientes)
            })

        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

    @app.route('/remover-cliente/<int:cliente_id>', methods=['POST'])
    @login_required
    def remover_cliente(cliente_id):
        try:
            cliente = db.session.get(Cliente, cliente_id)
            if not cliente:
                return jsonify({"ok": False, "mensagem": "Cliente não encontrado"}), 404

            if current_user.tipo == 'consultor' and cliente.consultor_id != current_user.id:
                return jsonify({"ok": False, "mensagem": "Sem permissão"}), 403

            payload = request.get_json(silent=True) or {}
            motivo = s(payload.get('motivo'))

            cliente.ativo = False

            if motivo:
                lig = Ligacao(
                    cliente_id=cliente_id,
                    consultor_id=current_user.id,
                    data_hora=datetime.now(),
                    observacao=f"CLIENTE REMOVIDO: {motivo}",
                    resultado='sem_interesse'
                )
                db.session.add(lig)

            db.session.commit()

            return jsonify({"ok": True, "mensagem": f"Cliente {cliente.nome} removido com sucesso"})

        except Exception as e:
            db.session.rollback()
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500
