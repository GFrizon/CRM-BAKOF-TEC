from datetime import datetime, timedelta
import logging
import threading
import unicodedata

from flask import jsonify, request
from flask_login import current_user, login_required

from core.extensions import db
from core.models import Cliente, Usuario
from routes.clientes_ligacoes.cache_invalidation import invalidar_caches_listagens_clientes
from sqlalchemy import or_
from oracle_service import get_clientes_inativos_oracle, get_clientes_oracle, get_dias_media_recebimento_oracle, get_valor_total_365dias, test_oracle_connection
from telefone_utils import identificar_ddd_padrao, padronizar_telefone

logger = logging.getLogger(__name__)


def register_oracle_routes(app):
    sync_lock = threading.Lock()
    sync_state = {
        "running": False,
        "started_at": None,
        "finished_at": None,
        "last_success": None,
        "last_error": None,
    }

    def _iniciar_sync_oracle_background():
        """Inicia sync em background e evita execucoes concorrentes."""
        if not sync_lock.acquire(blocking=False):
            return False, "Sincronizacao ja esta em andamento. Aguarde terminar."

        from sincronizacao_automatica import sincronizacao_automatica_diaria

        sync_state["running"] = True
        sync_state["started_at"] = datetime.now()
        sync_state["finished_at"] = None
        sync_state["last_error"] = None

        def sincronizar_background():
            try:
                ok = sincronizacao_automatica_diaria()
                sync_state["last_success"] = bool(ok)
                if not ok:
                    sync_state["last_error"] = "Sincronizacao finalizou com falha."
            except Exception as e:
                sync_state["last_success"] = False
                sync_state["last_error"] = str(e)
                logger.exception("Erro na sincronizacao background")
            finally:
                sync_state["running"] = False
                sync_state["finished_at"] = datetime.now()
                sync_lock.release()
                invalidar_caches_listagens_clientes("finalizacao da sincronizacao oracle em background")

        try:
            thread = threading.Thread(target=sincronizar_background)
            thread.daemon = True
            thread.start()
            return True, "Sincronizacao iniciada com sucesso! Aguarde alguns minutos para ver os resultados."
        except Exception as e:
            sync_state["running"] = False
            sync_state["finished_at"] = datetime.now()
            sync_state["last_success"] = False
            sync_state["last_error"] = f"Nao foi possivel iniciar a thread de sincronizacao: {e}"
            sync_lock.release()
            return False, "Falha ao iniciar sincronizacao em background."

    def _limpar_texto(valor):
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

    def _normalizar_nome(valor: str) -> str:
        if not valor:
            return ""
        txt = unicodedata.normalize("NFKD", str(valor))
        txt = "".join(c for c in txt if not unicodedata.combining(c))
        return " ".join(txt.upper().strip().split())

    def _extrair_nome_consultor_oracle(valor: str) -> str:
        if not valor:
            return ""
        # Ex.: "005 - CARLA - C42" -> "CARLA"
        partes = [p.strip() for p in str(valor).split("-") if p.strip()]
        if len(partes) >= 2:
            return partes[1]
        return partes[0] if partes else ""

    def _resolver_consultor_oracle(valor_oracle: str, mapa_nome: dict, mapa_primeiro_nome: dict):
        nome_oracle = _extrair_nome_consultor_oracle(valor_oracle)
        if not nome_oracle:
            return None

        nome_norm = _normalizar_nome(nome_oracle)
        if not nome_norm:
            return None

        consultor = mapa_nome.get(nome_norm)
        if consultor:
            return consultor

        primeiro = nome_norm.split()[0]
        return mapa_primeiro_nome.get(primeiro)

    def _montar_resposta_detalhes_oracle(
        cliente,
        cd_cliente_oracle: str,
        janela_dias: int = 365,
        data_inicio: datetime = None,
        data_fim: datetime = None,
    ):
        from oracle_service import (
            get_cliente_oracle_por_codigo,
            get_centralizadora_cliente_oracle,
            get_itens_cliente_oracle,
            get_pedidos_cliente_oracle,
            get_pedidos_cliente_periodo_oracle,
        )

        origem_cliente = str(getattr(cliente, "origem", "") or "").strip().lower() if cliente else ""
        eh_especial = origem_cliente in ("manual", "importado_csv")

        usar_periodo_fechado = bool(data_inicio and data_fim and data_inicio < data_fim)
        if usar_periodo_fechado:
            pedidos_oracle = get_pedidos_cliente_periodo_oracle(
                cd_cliente_oracle,
                data_inicio=data_inicio,
                data_fim=data_fim,
                modo_especial=eh_especial,
            )
        else:
            pedidos_oracle = get_pedidos_cliente_oracle(
                cd_cliente_oracle,
                janela_dias=janela_dias,
                modo_especial=eh_especial,
            )
        itens_oracle = get_itens_cliente_oracle(
            cd_cliente_oracle,
            janela_dias=janela_dias,
            modo_especial=eh_especial,
        )
        detalhes_oracle = get_cliente_oracle_por_codigo(cd_cliente_oracle) or {}
        centralizadora = get_centralizadora_cliente_oracle(cd_cliente_oracle) or {}
        pagamento_medio_map = get_dias_media_recebimento_oracle([cd_cliente_oracle])
        pagamento_medio_dias = pagamento_medio_map.get(str(cd_cliente_oracle or "").strip())

        def _pick(local_val, oracle_val):
            if local_val is None:
                return oracle_val
            if isinstance(local_val, str) and not local_val.strip():
                return oracle_val
            return local_val

        ultimo_pedido_lista = pedidos_oracle[0] if pedidos_oracle else {}

        if cliente:
            if eh_especial:
                dt_pedido = (
                    ultimo_pedido_lista.get("dt_pedido")
                    or detalhes_oracle.get("dt_pedido")
                    or cliente.ultimo_pedido_oracle
                )
                valor_ultimo = (
                    ultimo_pedido_lista.get("total_pedido")
                    or detalhes_oracle.get("total_pedido")
                    or cliente.valor_ultimo_pedido
                )
                situacao_ultimo = (
                    ultimo_pedido_lista.get("situacao")
                    or detalhes_oracle.get("situacao")
                    or cliente.situacao_ultimo_pedido
                )
            else:
                dt_pedido = cliente.ultimo_pedido_oracle or detalhes_oracle.get("dt_pedido")
                valor_ultimo = _pick(cliente.valor_ultimo_pedido, detalhes_oracle.get("total_pedido"))
                situacao_ultimo = _pick(cliente.situacao_ultimo_pedido, detalhes_oracle.get("situacao"))
            cliente_payload = {
                "id": cliente.id,
                "nome": _pick(cliente.nome, detalhes_oracle.get("cliente")),
                "cnpj": _pick(cliente.cnpj, detalhes_oracle.get("cnpj")),
                "telefone": _pick(cliente.telefone, detalhes_oracle.get("telefone1")),
                "telefone2": _pick(cliente.telefone2, detalhes_oracle.get("telefone2")),
                "cd_cliente_oracle": cliente.cd_cliente_oracle or cd_cliente_oracle,
                "categoria_consultor": _pick(cliente.categoria_consultor, detalhes_oracle.get("consultor")),
                "conceito": _pick(cliente.conceito, detalhes_oracle.get("conceito")),
                "ultimo_pedido_oracle": dt_pedido.strftime('%d/%m/%Y') if dt_pedido else None,
                "valor_ultimo_pedido": float(valor_ultimo) if valor_ultimo is not None else None,
                "situacao_ultimo_pedido": situacao_ultimo,
                "representante_oracle": _pick(cliente.representante_oracle, detalhes_oracle.get("representante")),
                "municipio": _pick(cliente.municipio, detalhes_oracle.get("municipio")),
                "uf": _pick(cliente.uf, detalhes_oracle.get("uf")),
                "contato": _pick(cliente.contato, detalhes_oracle.get("contato")),
                "data_ultima_sincronizacao": cliente.data_ultima_sincronizacao.strftime('%d/%m/%Y %H:%M') if cliente.data_ultima_sincronizacao else None,
                "cd_centralizado": centralizadora.get("cd_centralizado"),
                "nome_centralizadora": centralizadora.get("nome_centralizadora"),
                "pagamento_medio_dias": pagamento_medio_dias,
            }
        else:
            dt_pedido = ultimo_pedido_lista.get("dt_pedido") or detalhes_oracle.get("dt_pedido")
            valor_ultimo = ultimo_pedido_lista.get("total_pedido") or detalhes_oracle.get("total_pedido")
            situacao_ultimo = ultimo_pedido_lista.get("situacao") or detalhes_oracle.get("situacao")
            cliente_payload = {
                "id": None,
                "nome": detalhes_oracle.get("cliente") or f"Cliente Oracle {cd_cliente_oracle}",
                "cnpj": detalhes_oracle.get("cnpj"),
                "telefone": detalhes_oracle.get("telefone1"),
                "telefone2": detalhes_oracle.get("telefone2"),
                "cd_cliente_oracle": cd_cliente_oracle,
                "categoria_consultor": detalhes_oracle.get("consultor"),
                "conceito": detalhes_oracle.get("conceito"),
                "ultimo_pedido_oracle": dt_pedido.strftime('%d/%m/%Y') if dt_pedido else None,
                "valor_ultimo_pedido": float(valor_ultimo) if valor_ultimo is not None else None,
                "situacao_ultimo_pedido": situacao_ultimo,
                "representante_oracle": detalhes_oracle.get("representante"),
                "municipio": detalhes_oracle.get("municipio"),
                "uf": detalhes_oracle.get("uf"),
                "contato": detalhes_oracle.get("contato"),
                "data_ultima_sincronizacao": None,
                "cd_centralizado": centralizadora.get("cd_centralizado"),
                "nome_centralizadora": centralizadora.get("nome_centralizadora"),
                "pagamento_medio_dias": pagamento_medio_dias,
            }

        return jsonify({
            "success": True,
            "cliente": cliente_payload,
            "pedidos_oracle": pedidos_oracle,
            "itens_oracle": itens_oracle,
            "janela_dias": janela_dias,
            "periodo_inicio": data_inicio.strftime("%Y-%m-%d") if data_inicio else None,
            "periodo_fim": data_fim.strftime("%Y-%m-%d") if data_fim else None,
            "total_pedidos": len(pedidos_oracle),
            "total_itens": len(itens_oracle)
        })

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

    @app.route('/clientes-inativos-oracle')
    @login_required
    def clientes_inativos_oracle_route():
        """Rota para testar busca de clientes inativos (181 dias a 2 anos) no Oracle"""
        if current_user.tipo not in ('televendas', 'supervisor'):
            return jsonify({"erro": "Acesso permitido somente para televendas e supervisores"}), 403

        try:
            clientes = get_clientes_inativos_oracle()
            return jsonify({
                "success": True,
                "total": len(clientes),
                "clientes": clientes[:5],  # Mostra só os 5 primeiros para teste
                "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            })
        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao buscar clientes inativos: {str(e)}"
            }), 500

    @app.route('/sincronizar-oracle', methods=['POST'])
    @login_required
    def sincronizar_oracle():
        """Dispara sincronizacao Oracle em background e retorna imediatamente."""
        if current_user.tipo != 'supervisor':
            return jsonify({"erro": "Acesso permitido somente para supervisores"}), 403

        try:
            ok, msg = _iniciar_sync_oracle_background()
            if not ok:
                return jsonify({
                    "success": False,
                    "message": msg
                }), 409

            return jsonify({
                "success": True,
                "message": msg
            })

        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro na sincronizacao: {str(e)}"
            }), 500

    @app.route('/sincronizar-oracle-status', methods=['GET'])
    @login_required
    def sincronizar_oracle_status():
        if current_user.tipo != 'supervisor':
            return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403

        return jsonify({
            "ok": True,
            "running": bool(sync_state["running"]),
            "started_at": (sync_state["started_at"].strftime("%d/%m/%Y %H:%M:%S") if sync_state["started_at"] else None),
            "finished_at": (sync_state["finished_at"].strftime("%d/%m/%Y %H:%M:%S") if sync_state["finished_at"] else None),
            "last_success": sync_state["last_success"],
            "last_error": sync_state["last_error"],
        })
    @app.route('/detalhes-cliente-oracle/<int:cliente_id>')
    @login_required
    def detalhes_cliente_oracle(cliente_id: int):
        """Busca detalhes completos do cliente Oracle"""
        try:
            def _parse_data_iso(valor: str):
                txt = str(valor or '').strip()
                if not txt:
                    return None
                try:
                    return datetime.strptime(txt[:10], '%Y-%m-%d')
                except Exception:
                    return None

            janela_dias = request.args.get('janela_dias', type=int) or 365
            cd_cliente_override = str(request.args.get('cd_cliente_oracle') or '').strip()
            if janela_dias < 30:
                janela_dias = 30
            if janela_dias > 730:
                janela_dias = 730
            data_inicio = _parse_data_iso(request.args.get('data_inicio'))
            data_fim = _parse_data_iso(request.args.get('data_fim'))
            if data_inicio and data_fim and data_inicio >= data_fim:
                return jsonify({"success": False, "message": "Periodo invalido"}), 400

            cliente = db.session.get(Cliente, cliente_id)
            if not cliente:
                return jsonify({"success": False, "message": "Cliente não encontrado"}), 404

            if current_user.tipo == 'consultor' and cliente.consultor_id != current_user.id:
                return jsonify({"success": False, "message": "Sem permissão para este cliente"}), 403
            
            # Supervisor_repr pode visualizar detalhes (somente leitura)
            if current_user.tipo == 'supervisor_repr':
                pass  # Permitir visualização

            cd_cliente_resolvido = (
                cd_cliente_override
                if cd_cliente_override
                else str(cliente.cd_cliente_oracle or '').strip()
            )

            if not cd_cliente_resolvido:
                try:
                    from oracle_service import get_cliente_oracle_por_cnpj
                    row_oracle = get_cliente_oracle_por_cnpj(str(cliente.cnpj or '').strip())
                    cd_fallback = str((row_oracle or {}).get('cd_cliente') or '').strip()
                    if cd_fallback:
                        cd_cliente_resolvido = cd_fallback
                except Exception:
                    cd_cliente_resolvido = ''

            if not cd_cliente_resolvido:
                return jsonify({
                    "success": True,
                    "cliente": {
                        "id": cliente.id,
                        "nome": cliente.nome,
                        "cnpj": cliente.cnpj,
                        "telefone": cliente.telefone,
                        "telefone2": cliente.telefone2,
                        "representante_nome": cliente.representante_nome,
                        "municipio": cliente.municipio,
                        "uf": cliente.uf,
                        "contato": cliente.contato,
                        "origem": "manual"
                    },
                    "pedidos_oracle": [],
                    "mensagem": "Cliente não possui dados Oracle"
                })

            return _montar_resposta_detalhes_oracle(
                cliente,
                cd_cliente_resolvido,
                janela_dias=janela_dias,
                data_inicio=data_inicio,
                data_fim=data_fim,
            )

        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao buscar detalhes: {str(e)}"
            }), 500

    @app.route('/detalhes-cliente-oracle-cd/<cd_cliente>')
    @login_required
    def detalhes_cliente_oracle_por_codigo(cd_cliente: str):
        """Busca detalhes Oracle por código do cliente (fallback quando não há ID local)."""
        try:
            def _parse_data_iso(valor: str):
                txt = str(valor or '').strip()
                if not txt:
                    return None
                try:
                    return datetime.strptime(txt[:10], '%Y-%m-%d')
                except Exception:
                    return None

            janela_dias = request.args.get('janela_dias', type=int) or 365
            if janela_dias < 30:
                janela_dias = 30
            if janela_dias > 730:
                janela_dias = 730
            data_inicio = _parse_data_iso(request.args.get('data_inicio'))
            data_fim = _parse_data_iso(request.args.get('data_fim'))
            if data_inicio and data_fim and data_inicio >= data_fim:
                return jsonify({"success": False, "message": "Periodo invalido"}), 400

            cd_cliente_limpo = str(cd_cliente or '').strip()
            if not cd_cliente_limpo:
                return jsonify({"success": False, "message": "Código do cliente inválido"}), 400

            cliente = Cliente.query.filter_by(cd_cliente_oracle=cd_cliente_limpo).first()

            if cliente and current_user.tipo == 'consultor' and cliente.consultor_id != current_user.id:
                return jsonify({"success": False, "message": "Sem permissão para este cliente"}), 403

            if not cliente and current_user.tipo == 'consultor':
                return jsonify({"success": False, "message": "Sem permissão para este cliente"}), 403
            
            # Supervisor_repr pode visualizar detalhes (somente leitura)
            if current_user.tipo == 'supervisor_repr':
                pass  # Permitir visualização

            return _montar_resposta_detalhes_oracle(
                cliente,
                cd_cliente_limpo,
                janela_dias=janela_dias,
                data_inicio=data_inicio,
                data_fim=data_fim,
            )

        except Exception as e:
            return jsonify({
                "success": False,
                "message": f"Erro ao buscar detalhes: {str(e)}"
            }), 500

    @app.route('/garantir-cliente-local-oracle', methods=['POST'])
    @login_required
    def garantir_cliente_local_oracle():
        """Garante vínculo local para permitir registro de ligação em clientes Oracle."""
        try:
            if current_user.tipo not in ('televendas', 'supervisor'):
                return jsonify({"success": False, "message": "Sem permissao"}), 403

            payload = request.get_json(silent=True) or {}
            cd_cliente = str(payload.get('cd_cliente_oracle') or '').strip()
            if not cd_cliente:
                return jsonify({"success": False, "message": "Codigo Oracle obrigatorio"}), 400

            cliente_existente = Cliente.query.filter_by(cd_cliente_oracle=cd_cliente, ativo=True).first()
            if cliente_existente:
                if current_user.tipo == 'consultor' and cliente_existente.consultor_id != current_user.id:
                    return jsonify({"success": False, "message": "Cliente vinculado a outro usuario"}), 403
                return jsonify({
                    "success": True,
                    "cliente": {
                        "id": cliente_existente.id,
                        "nome": cliente_existente.nome,
                        "telefone": cliente_existente.telefone or cliente_existente.telefone2
                    }
                })

            nome = str(payload.get('nome') or f"Cliente Oracle {cd_cliente}").strip()[:200]
            telefone = _limpar_texto(payload.get('telefone'))
            telefone2 = _limpar_texto(payload.get('telefone2'))
            cnpj = _limpar_texto(payload.get('cnpj'))
            representante_nome = _limpar_texto(payload.get('representante_nome'))
            categoria_consultor = _limpar_texto(payload.get('categoria_consultor'))
            conceito = _limpar_texto(payload.get('conceito'))
            municipio = _limpar_texto(payload.get('municipio'))
            uf = _limpar_texto(payload.get('uf'))
            contato = _limpar_texto(payload.get('contato'))
            representante_oracle = _limpar_texto(payload.get('representante_oracle'))

            novo_cliente = Cliente(
                nome=nome or f"Cliente Oracle {cd_cliente}",
                cnpj=cnpj,
                telefone=telefone or telefone2,
                telefone2=telefone2,
                representante_nome=representante_nome,
                consultor_id=current_user.id,
                ativo=True,
                origem='importado_csv',
                cd_cliente_oracle=cd_cliente,
                categoria_consultor=categoria_consultor,
                conceito=conceito,
                representante_oracle=representante_oracle,
                municipio=municipio,
                uf=uf,
                contato=contato,
            )

            db.session.add(novo_cliente)
            db.session.commit()
            invalidar_caches_listagens_clientes("garantia de cliente local oracle")

            return jsonify({
                "success": True,
                "cliente": {
                    "id": novo_cliente.id,
                    "nome": novo_cliente.nome,
                    "telefone": novo_cliente.telefone or novo_cliente.telefone2
                }
            })

        except Exception as e:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": f"Erro ao garantir cliente local: {str(e)}"
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
        """Sincronizacao manual dos clientes Oracle"""
        if current_user.tipo != 'supervisor':
            return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403

        try:
            ok, msg = _iniciar_sync_oracle_background()
            if not ok:
                return jsonify({"ok": False, "mensagem": msg}), 409

            return jsonify({
                "ok": True,
                "mensagem": msg
            })

        except Exception as e:
            return jsonify({"ok": False, "mensagem": f"Erro ao iniciar sincronizacao: {str(e)}"}), 500
