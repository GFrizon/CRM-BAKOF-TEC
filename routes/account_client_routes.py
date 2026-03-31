from datetime import datetime, timedelta
import unicodedata

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload
from werkzeug.security import check_password_hash, generate_password_hash

from core.extensions import db
from core.helpers import s
from core.models import Cliente, Ligacao, Usuario


def _normalizar_nome(txt: str) -> str:
    if not txt:
        return ""
    base = unicodedata.normalize("NFKD", str(txt))
    base = "".join(c for c in base if not unicodedata.combining(c))
    return " ".join(base.upper().strip().split())


def _extrair_nome_oracle_consultor(valor_oracle: str) -> str:
    if not valor_oracle:
        return ""
    partes = [p.strip() for p in str(valor_oracle).split("-") if p.strip()]
    if len(partes) >= 2:
        return partes[1]
    return partes[0] if partes else ""


def _resolver_consultor_id_por_categoria(
    categoria_oracle: str,
    mapa_codigo_para_id: dict,
    mapa_nome_para_id: dict,
):
    texto = str(categoria_oracle or "").strip()
    if not texto:
        return None

    codigo = ""
    if "-" in texto:
        codigo = texto.split("-", 1)[0].strip()
    elif " - " in texto:
        codigo = texto.split(" - ", 1)[0].strip()

    nome_oracle = _extrair_nome_oracle_consultor(texto)
    nome_norm = _normalizar_nome(nome_oracle)
    if nome_norm and nome_norm in mapa_nome_para_id:
        return mapa_nome_para_id[nome_norm]
    if codigo and codigo in mapa_codigo_para_id:
        return mapa_codigo_para_id[codigo]
    return None


def register_account_client_routes(app):
    @app.route('/minha-conta')
    @login_required
    def minha_conta():
        stats = {}

        if current_user.tipo in ('consultor', 'televendas'):
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
            apenas_meus = True if current_user.tipo in ('consultor', 'televendas') else (request.args.get('meus') == '1')


            # Oracle/Inativos: fonte de verdade no Oracle; MySQL local apenas para
            # vinculo (id/consultor) e estatisticas de ligacoes.
            if aba in ('oracle', 'inativos'):
                if aba == 'inativos' and current_user.tipo == 'consultor':
                    return jsonify({"ok": False, "erro": "Sem permissao para aba inativos"}), 403

                if aba == 'oracle':
                    from oracle_service import get_clientes_oracle as _get_clientes_oracle_aba
                    clientes_oracle_raw = _get_clientes_oracle_aba()
                else:
                    from oracle_service import get_clientes_inativos_oracle as _get_clientes_inativos_aba
                    clientes_oracle_raw = _get_clientes_inativos_aba()

                clientes_oracle_por_cd = {}
                for row in clientes_oracle_raw:
                    cd = str(row.get('cd_cliente') or '').strip()
                    if not cd:
                        continue
                    atual = clientes_oracle_por_cd.get(cd)
                    if not atual:
                        clientes_oracle_por_cd[cd] = row
                        continue
                    dt_novo = row.get('dt_pedido')
                    dt_atual = atual.get('dt_pedido')
                    if dt_novo and (not dt_atual or dt_novo > dt_atual):
                        clientes_oracle_por_cd[cd] = row

                clientes_oracle = list(clientes_oracle_por_cd.values())
                codigos_oracle = [str(c.get('cd_cliente')).strip() for c in clientes_oracle if c.get('cd_cliente')]
                filtrar_oracle_por_categoria = (current_user.tipo == 'consultor')
                mapa_nome_para_id_oracle = {}
                mapa_codigo_para_id_oracle = {}
                if filtrar_oracle_por_categoria:
                    usuarios_ativos = Usuario.query.filter(
                        Usuario.ativo == True,
                        Usuario.tipo.in_(["consultor", "televendas", "supervisor"])
                    ).all()
                    mapa_nome_para_id_oracle = {
                        _normalizar_nome(u.nome): u.id
                        for u in usuarios_ativos if u and u.nome
                    }
                    codigos_referencia = {
                        "100": "Roseleia Basso",
                        "002": "Rodrigo Crespan",
                        "007": "Janine de Mello",
                        "012": "Sandra Vendruscolo da Silva",
                        "001": "Elisabete Haus",
                        "003": "Iara Sponchiado",
                        "004": "Odete Luza",
                        "005": "Carla Siduoski",
                        "006": "Sibele Froner",
                        "010": "Sibele Froner",
                        "999": "Daniela Da Rosa",
                    }
                    for codigo, nome_ref in codigos_referencia.items():
                        uid = mapa_nome_para_id_oracle.get(_normalizar_nome(nome_ref))
                        if uid:
                            mapa_codigo_para_id_oracle[codigo] = uid

                clientes_locais_por_cd = {}
                stats_ligacoes_por_cliente_id = {}
                if codigos_oracle:
                    q_locais = Cliente.query.filter(
                        Cliente.cd_cliente_oracle.in_(codigos_oracle),
                        Cliente.ativo == True
                    )
                    if apenas_meus:
                        q_locais = q_locais.filter(Cliente.consultor_id == current_user.id)
                    clientes_locais = q_locais.all()
                    clientes_locais_por_cd = {
                        str(c.cd_cliente_oracle): c
                        for c in clientes_locais if c.cd_cliente_oracle
                    }

                    ids_locais = [c.id for c in clientes_locais if c.id]
                    if ids_locais:
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
                                "total_ligacoes": int(row.total_ligacoes or 0),
                                "ultima_ligacao": row.ultima_ligacao.strftime("%d/%m/%Y %H:%M") if row.ultima_ligacao else None
                            }
                            for row in ligacoes_agg
                        }

                termo_lower = (termo or "").lower()
                clientes = []
                for row in clientes_oracle:
                    cd = str(row.get('cd_cliente') or '').strip()
                    if not cd:
                        continue
                    consultor_oracle = str(row.get('consultor') or '').strip()

                    cli_local = clientes_locais_por_cd.get(cd)
                    if apenas_meus and not cli_local:
                        continue
                    if filtrar_oracle_por_categoria and consultor_oracle:
                        consultor_esperado = _resolver_consultor_id_por_categoria(
                            consultor_oracle,
                            mapa_codigo_para_id=mapa_codigo_para_id_oracle,
                            mapa_nome_para_id=mapa_nome_para_id_oracle,
                        )
                        if consultor_esperado and consultor_esperado != current_user.id:
                            continue

                    if termo_lower:
                        base_busca = " ".join([
                            str(row.get('cliente') or ''),
                            str(row.get('cnpj') or ''),
                            str(row.get('telefone1') or ''),
                            str(row.get('telefone2') or ''),
                            str(row.get('representante') or ''),
                            str(row.get('consultor') or ''),
                            str(row.get('conceito') or ''),
                            str(row.get('municipio') or ''),
                            str(row.get('uf') or ''),
                        ]).lower()
                        if termo_lower not in base_busca:
                            continue

                    stats_lig = stats_ligacoes_por_cliente_id.get(cli_local.id, {}) if cli_local and cli_local.id else {}
                    clientes.append({
                        "id": cli_local.id if cli_local else None,
                        "cd_cliente_oracle": cd,
                        "nome": row.get('cliente', ''),
                        "cnpj": row.get('cnpj', ''),
                        "telefone": (cli_local.telefone if cli_local and cli_local.telefone else (row.get('telefone1') or row.get('telefone2'))),
                        "telefone2": (cli_local.telefone2 if cli_local else row.get('telefone2')),
                        "representante_nome": row.get('representante', ''),
                        "categoria_consultor": row.get('consultor', ''),
                        "conceito": row.get('conceito', ''),
                        "municipio": row.get('municipio', ''),
                        "uf": row.get('uf', ''),
                        "contato": row.get('contato', ''),
                        "ultima_ligacao": stats_lig.get("ultima_ligacao"),
                        "total_ligacoes": stats_lig.get("total_ligacoes", 0),
                        "proxima_ligacao": (cli_local.proxima_ligacao.strftime("%d/%m/%Y %H:%M") if cli_local and cli_local.proxima_ligacao else None),
                        "valor_total_365dias": float(getattr(cli_local, 'valor_total_365dias', 0) or 0),
                        "valor_ultimo_pedido": float(getattr(cli_local, 'valor_ultimo_pedido', 0) or 0),
                        "origem": (getattr(cli_local, 'origem', None) if cli_local else ('oracle_inativos' if aba == 'inativos' else 'oracle')),
                    })

                clientes = sorted(clientes, key=lambda x: (x.get("nome") or "").lower())
                return jsonify({
                    "ok": True,
                    "clientes": clientes,
                    "total": len(clientes)
                })

            q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
            if apenas_meus:
                q = q.filter(Cliente.consultor_id == current_user.id)
            if current_user.tipo == 'consultor' and aba == 'pendentes':
                limite_min_90_120 = datetime.now() - timedelta(days=120)
                limite_max_90_120 = datetime.now() - timedelta(days=90)
                q = q.filter(~and_(
                    Cliente.cd_cliente_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.isnot(None),
                    Cliente.ultimo_pedido_oracle.between(limite_min_90_120, limite_max_90_120),
                ))

            if termo:
                like = f"%{termo}%"
                q = q.filter(or_(
                    Cliente.nome.like(like),
                    Cliente.cnpj.like(like),
                    Cliente.telefone.like(like),
                    Cliente.representante_nome.like(like),
                    Cliente.representante_oracle.like(like)
                ))

            clientes_todos = q.order_by(Cliente.nome.asc()).all()

            pendentes, contatados, precisa_retornar = [], [], []
            agora = datetime.now()
            filtrar_por_categoria_consultor = (current_user.tipo == 'consultor')
            mapa_nome_para_id = {}
            mapa_codigo_para_id = {}
            if filtrar_por_categoria_consultor:
                usuarios_ativos = Usuario.query.filter(
                    Usuario.ativo == True,
                    Usuario.tipo.in_(["consultor", "televendas", "supervisor"])
                ).all()
                mapa_nome_para_id = {
                    _normalizar_nome(u.nome): u.id
                    for u in usuarios_ativos if u and u.nome
                }
                codigos_referencia = {
                    "100": "Roseleia Basso",
                    "002": "Rodrigo Crespan",
                    "007": "Janine de Mello",
                    "012": "Sandra Vendruscolo da Silva",
                    "001": "Elisabete Haus",
                    "003": "Iara Sponchiado",
                    "004": "Odete Luza",
                    "005": "Carla Siduoski",
                    "006": "Sibele Froner",
                    "010": "Sibele Froner",
                    "999": "Daniela Da Rosa",
                }
                for codigo, nome_ref in codigos_referencia.items():
                    uid = mapa_nome_para_id.get(_normalizar_nome(nome_ref))
                    if uid:
                        mapa_codigo_para_id[codigo] = uid

            for c in clientes_todos:
                ligacoes_relevantes = (
                    [l for l in c.ligacoes if l.consultor_id == current_user.id]
                    if current_user.tipo in ('consultor', 'televendas')
                    else list(c.ligacoes)
                )
                ligs = sorted(ligacoes_relevantes, key=lambda x: x.data_hora, reverse=True)
                ultima = ligs[0] if ligs else None
                total = len(ligs)
                origem_cliente = str(getattr(c, 'origem', '') or '').strip().lower()
                dados = {
                    "id": c.id,
                    "nome": c.nome,
                    "cnpj": c.cnpj,
                    "telefone": c.telefone,
                    "representante_nome": (c.representante_oracle or c.representante_nome),
                    "ultima_ligacao": ultima.data_hora.strftime("%d/%m/%Y %H:%M") if ultima else None,
                    "total_ligacoes": total,
                    "proxima_ligacao": c.proxima_ligacao.strftime("%d/%m/%Y %H:%M") if c.proxima_ligacao else None,
                    "valor_total_365dias": float(c.valor_total_365dias or 0),
                    "valor_ultimo_pedido": float(c.valor_ultimo_pedido or 0),
                    "origem": getattr(c, 'origem', None),
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

                # Mesma regra da tela principal: cliente manual do consultor
                # aparece em "Clientes Especiais" independentemente de ligacoes.
                if current_user.tipo == 'consultor' and origem_cliente == 'manual':
                    pendentes.append(dados)
                    continue

                if total == 0:
                    pendentes.append(dados)
                else:
                    if c.proxima_ligacao or (ultima and ultima.resultado == 'retornar'):
                        dados["retorno_atrasado"] = bool(c.proxima_ligacao and (agora >= c.proxima_ligacao))
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

            if current_user.tipo in ('consultor', 'televendas') and cliente.consultor_id != current_user.id:
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
