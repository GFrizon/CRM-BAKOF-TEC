"""
Rotas para campanhas de premiação.
Supervisores: acesso completo (pagar, configurar).
Consultores: somente leitura.
"""
from datetime import datetime, timedelta
from calendar import monthrange

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from core.extensions import db
from core.models import ConfiguracaoCampanha, PremiacaoReativacao
from oracle_service import get_pedidos_reativacao_oracle
from services.proximos_inativacao_snapshot_service import (
    carregar_primeiro_snapshot_proximos_inativacao_mes,
    rows_snapshot_proximos_inativacao,
)

_TIPOS_CAMPANHA = ("supervisor", "consultor")


LISTAS_CAMPANHA = {
    "proximos_inativacao": {
        "nome": "Próximos da inativação",
        "descricao": "151 a 180 dias sem pedido",
        "dias_inicio": 151,
        "dias_fim": 180,
    },
    "sem_pedido_90_150": {
        "nome": "Sem pedido 90 - 150 dias",
        "descricao": "90 a 150 dias sem pedido",
        "dias_inicio": 90,
        "dias_fim": 150,
    },
    "inativos": {
        "nome": "Inativos",
        "descricao": "181 a 1095 dias sem pedido",
        "dias_inicio": 181,
        "dias_fim": 1095,
    },
}

ALIASES_TIPO_LISTA = {
    "quente": "sem_pedido_90_150",
}


def _corrigir_texto_legado(texto):
    if not isinstance(texto, str) or not texto:
        return texto

    marcadores = ("Ã", "Â", "�", "├")
    if not any(marcador in texto for marcador in marcadores):
        return texto

    def _score(valor):
        return sum(valor.count(marcador) for marcador in marcadores)

    melhor = texto
    for origem in ("cp437", "cp850", "latin1", "cp1252"):
        try:
            candidato = texto.encode(origem).decode("utf-8")
        except UnicodeError:
            continue
        if _score(candidato) < _score(melhor):
            melhor = candidato
    return melhor


def _normalizar_tipo_lista(tipo_lista):
    tipo_lista = ALIASES_TIPO_LISTA.get(tipo_lista, tipo_lista)
    if tipo_lista not in LISTAS_CAMPANHA:
        return "proximos_inativacao"
    return tipo_lista


def _aplicar_lista_na_config(config, tipo_lista):
    tipo_lista = _normalizar_tipo_lista(tipo_lista)
    lista = LISTAS_CAMPANHA[tipo_lista]
    config.tipo_lista = tipo_lista
    config.dias_inatividade_inicio = lista["dias_inicio"]
    config.dias_inatividade_fim = lista["dias_fim"]
    return lista


def _formatar_moeda_br(valor):
    try:
        numero = float(valor or 0)
    except (TypeError, ValueError):
        numero = 0.0
    texto = f"{numero:,.2f}"
    return f"R$ {texto.replace(',', 'X').replace('.', ',').replace('X', '.')}"


def register_campanhas_routes(app):
    """Registra rotas de campanhas na aplicação Flask."""

    def _get_config_campanha():
        """Busca a configuração da campanha."""
        config = ConfiguracaoCampanha.query.order_by(ConfiguracaoCampanha.id.asc()).first()
        if not config:
            config = ConfiguracaoCampanha(
                campanha_nome="Reativação Premiada",
                valor_premiacao=50.00,
                tipo_lista="proximos_inativacao",
                dias_inatividade_inicio=151,
                dias_inatividade_fim=180,
            )
            db.session.add(config)
            db.session.commit()
            return config

        alterado = False
        nome_corrigido = _corrigir_texto_legado(config.campanha_nome)
        if nome_corrigido != config.campanha_nome:
            config.campanha_nome = nome_corrigido
            alterado = True

        tipo_normalizado = _normalizar_tipo_lista(config.tipo_lista)
        if tipo_normalizado != config.tipo_lista:
            _aplicar_lista_na_config(config, tipo_normalizado)
            alterado = True

        if alterado:
            db.session.commit()

        return config

    @app.route("/campanhas")
    @login_required
    def campanhas_index():
        """Página índice com lista de campanhas disponíveis."""
        if current_user.tipo not in _TIPOS_CAMPANHA:
            flash("Acesso não autorizado.", "danger")
            return redirect(url_for("meus_clientes"))
        eh_supervisor = current_user.tipo == "supervisor"
        return render_template("campanhas/index.html", eh_supervisor=eh_supervisor)

    @app.route("/campanhas/reativacao-premiada")
    @login_required
    def campanha_reativacao_premiada():
        """Página principal da campanha de reativação premiada."""
        if current_user.tipo not in _TIPOS_CAMPANHA:
            flash("Acesso não autorizado.", "danger")
            return redirect(url_for("meus_clientes"))

        config = _get_config_campanha()
        lista_campanha = _aplicar_lista_na_config(config, config.tipo_lista)
        valor_premio = float(config.valor_premiacao or 50)

        hoje = datetime.now()
        mes_ref = (request.args.get("mes_ref") or hoje.strftime("%Y-%m")).strip()

        try:
            ano_ref, mes_ref_num = [int(p) for p in mes_ref.split("-", 1)]
            data_inicio_dt = datetime(ano_ref, mes_ref_num, 1)
            if mes_ref_num == 12:
                data_fim_dt = datetime(ano_ref + 1, 1, 1)
            else:
                data_fim_dt = datetime(ano_ref, mes_ref_num + 1, 1)
        except ValueError:
            data_inicio = request.args.get("data_inicio", hoje.replace(day=1).strftime("%Y-%m-%d"))
            data_fim = request.args.get("data_fim", (hoje + timedelta(days=1)).strftime("%Y-%m-%d"))
            try:
                data_inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
                data_fim_dt = datetime.strptime(data_fim, "%Y-%m-%d")
                mes_ref = data_inicio_dt.strftime("%Y-%m")
            except ValueError:
                data_inicio_dt = hoje.replace(day=1)
                data_fim_dt = hoje + timedelta(days=1)
                mes_ref = data_inicio_dt.strftime("%Y-%m")
            ano_ref = data_inicio_dt.year
            mes_ref_num = data_inicio_dt.month

        data_inicio = data_inicio_dt.strftime("%Y-%m-%d")
        data_fim = (data_fim_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        try:
            pedidos_oracle = get_pedidos_reativacao_oracle(
                data_inicio_dt,
                data_fim_dt,
                lista_campanha["dias_inicio"],
                lista_campanha["dias_fim"],
            )
        except Exception as e:
            app.logger.error("Erro ao buscar pedidos de reativação: %s", e)
            pedidos_oracle = []
            flash("Erro ao consultar pedidos do Oracle. Tente novamente.", "warning")

        premiacoes_pagas = (
            PremiacaoReativacao.query
            .filter(
                PremiacaoReativacao.data_pedido >= data_inicio_dt.date(),
                PremiacaoReativacao.data_pedido < data_fim_dt.date(),
            )
            .all()
        )

        pedidos_pagos_set = {
            (p.cd_representante, p.cd_pedido)
            for p in premiacoes_pagas
            if p.data_pagamento is not None
        }

        representantes = {}
        for pedido in pedidos_oracle:
            cd_rep = pedido.get("cd_representante", "SEM_CODIGO")
            nome_rep = pedido.get("nome_representante", "Sem Representante")

            if cd_rep not in representantes:
                representantes[cd_rep] = {
                    "cd_representante": cd_rep,
                    "nome_representante": nome_rep,
                    "pedidos": [],
                    "total_reativacoes": 0,
                    "total_pedidos": 0.0,
                    "total_premio": 0.0,
                    "ja_pagos": 0,
                    "pendentes": 0,
                }

            rep = representantes[cd_rep]
            rep["pedidos"].append({
                "cd_cliente": pedido.get("cd_cliente"),
                "nome_cliente": pedido.get("nome_cliente"),
                "cnpj": pedido.get("cnpj"),
                "municipio": pedido.get("municipio"),
                "uf": pedido.get("uf"),
                "contato": pedido.get("contato"),
                "ultimo_pedido_antigo": pedido.get("ultimo_pedido_antigo"),
                "valor_ultimo_pedido_antigo": float(pedido.get("valor_ultimo_pedido_antigo") or 0),
                "cd_pedido": pedido.get("cd_pedido"),
                "data_novo_pedido": pedido.get("data_novo_pedido"),
                "valor_novo_pedido": float(pedido.get("valor_novo_pedido") or 0),
                "situacao_novo_pedido": pedido.get("situacao_novo_pedido"),
                "cond_pagto_novo_pedido": pedido.get("cond_pagto_novo_pedido"),
                "controle_novo_pedido": pedido.get("controle_novo_pedido"),
                "controle_pedido_anterior": pedido.get("controle_pedido_anterior"),
                "desc_controle_novo_pedido": pedido.get("desc_controle_novo_pedido"),
                "desc_controle_pedido_anterior": pedido.get("desc_controle_pedido_anterior"),
                "conceito": pedido.get("conceito"),
                "pago": (cd_rep, pedido.get("cd_pedido")) in pedidos_pagos_set,
            })
            rep["total_reativacoes"] += 1
            rep["total_pedidos"] += float(pedido.get("valor_novo_pedido") or 0)
            rep["total_premio"] += valor_premio

            if (cd_rep, pedido.get("cd_pedido")) in pedidos_pagos_set:
                rep["ja_pagos"] += 1
            else:
                rep["pendentes"] += 1

        representantes_ordenados = sorted(
            representantes.values(),
            key=lambda x: x["total_premio"],
            reverse=True,
        )

        total_geral = {
            "reativacoes": sum(r["total_reativacoes"] for r in representantes.values()),
            "valor_pedidos": sum(r["total_pedidos"] for r in representantes.values()),
            "premio_total": sum(r["total_premio"] for r in representantes.values()),
            "representantes": len(representantes),
        }

        eh_supervisor = current_user.tipo == "supervisor"

        return render_template(
            "campanhas/reativacao_premiada.html",
            representantes=representantes_ordenados,
            total_geral=total_geral,
            data_inicio=data_inicio,
            data_fim=data_fim,
            mes_ref=mes_ref,
            hoje=hoje.strftime("%Y-%m-%d"),
            campanha_config=config,
            lista_campanha=lista_campanha,
            valor_premio=valor_premio,
            formatar_moeda_br=_formatar_moeda_br,
            eh_supervisor=eh_supervisor,
            ano_ref=ano_ref,
            mes_ref_num=mes_ref_num,
        )

    @app.route("/campanhas/reativacao-premiada/proximos-snapshot")
    @login_required
    def campanha_proximos_snapshot():
        """Retorna JSON com clientes próximos da inativação do snapshot do início do mês."""
        if current_user.tipo not in _TIPOS_CAMPANHA:
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        try:
            ano = int(request.args.get("ano", datetime.now().year))
            mes = int(request.args.get("mes", datetime.now().month))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "mensagem": "Parâmetros inválidos"}), 400

        snapshot = carregar_primeiro_snapshot_proximos_inativacao_mes(ano, mes)
        if not snapshot:
            return jsonify({
                "ok": True,
                "disponivel": False,
                "data_ref": None,
                "total": 0,
                "itens": [],
            })

        itens = rows_snapshot_proximos_inativacao(snapshot)
        itens_serializados = []
        for item in itens:
            row = dict(item)
            dt = row.get("dt_pedido")
            row["dt_pedido"] = dt.strftime("%d/%m/%Y") if dt else None
            itens_serializados.append(row)

        return jsonify({
            "ok": True,
            "disponivel": True,
            "data_ref": snapshot.get("data_ref"),
            "total": len(itens_serializados),
            "itens": itens_serializados,
        })

    @app.route("/campanhas/reativacao-premiada/marcar-pago", methods=["POST"])
    @login_required
    def campanha_marcar_pago():
        """Marca uma reativação como paga."""
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        data = request.get_json() or {}
        cd_representante = data.get("cd_representante")
        cd_pedido = data.get("cd_pedido")
        cd_cliente = data.get("cd_cliente")
        nome_representante = data.get("nome_representante")
        nome_cliente = data.get("nome_cliente")
        data_pedido = data.get("data_pedido")
        valor_pedido = data.get("valor_pedido")
        observacao = data.get("observacao", "")

        if not all([cd_representante, cd_pedido, cd_cliente]):
            return jsonify({"ok": False, "mensagem": "Dados incompletos"}), 400

        try:
            existente = PremiacaoReativacao.query.filter_by(
                cd_representante=cd_representante,
                cd_pedido=cd_pedido,
            ).first()

            if existente:
                if existente.data_pagamento:
                    return jsonify({"ok": False, "mensagem": "Esta premiação já foi marcada como paga"}), 400
                existente.data_pagamento = datetime.now()
                existente.pago_por_id = current_user.id
                existente.observacao = observacao
            else:
                data_pedido_dt = (
                    datetime.strptime(data_pedido, "%Y-%m-%d").date()
                    if data_pedido
                    else datetime.now().date()
                )

                nova_premiacao = PremiacaoReativacao(
                    cd_representante=cd_representante,
                    nome_representante=nome_representante or "Desconhecido",
                    cd_cliente=cd_cliente,
                    nome_cliente=nome_cliente or "Desconhecido",
                    cd_pedido=cd_pedido,
                    data_pedido=data_pedido_dt,
                    valor_pedido=valor_pedido or 0,
                    valor_premiacao=_get_config_campanha().valor_premiacao,
                    data_pagamento=datetime.now(),
                    pago_por_id=current_user.id,
                    observacao=observacao,
                )
                db.session.add(nova_premiacao)

            db.session.commit()
            return jsonify({"ok": True, "mensagem": "Premiação marcada como paga com sucesso!"})

        except Exception as e:
            db.session.rollback()
            app.logger.error("Erro ao marcar premiação como paga: %s", e)
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/campanhas/reativacao-premiada/marcar-nao-pago", methods=["POST"])
    @login_required
    def campanha_marcar_nao_pago():
        """Remove marcação de pago de uma reativação."""
        if current_user.tipo != "supervisor":
            return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

        data = request.get_json() or {}
        cd_representante = data.get("cd_representante")
        cd_pedido = data.get("cd_pedido")

        if not all([cd_representante, cd_pedido]):
            return jsonify({"ok": False, "mensagem": "Dados incompletos"}), 400

        try:
            premiacao = PremiacaoReativacao.query.filter_by(
                cd_representante=cd_representante,
                cd_pedido=cd_pedido,
            ).first()

            if not premiacao:
                return jsonify({"ok": False, "mensagem": "Registro não encontrado"}), 404

            premiacao.data_pagamento = None
            premiacao.pago_por_id = None
            db.session.commit()

            return jsonify({"ok": True, "mensagem": "Marcação de pago removida com sucesso!"})

        except Exception as e:
            db.session.rollback()
            app.logger.error("Erro ao remover marcação de pago: %s", e)
            return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

    @app.route("/campanhas/configuracao", methods=["GET", "POST"])
    @login_required
    def campanha_configuracao():
        """Tela de configuração da campanha (supervisor only)."""
        if current_user.tipo != "supervisor":
            flash("Acesso permitido apenas para supervisores.", "danger")
            return redirect(url_for("campanha_reativacao_premiada"))

        config = _get_config_campanha()
        lista_campanha = _aplicar_lista_na_config(config, config.tipo_lista)

        if request.method == "POST":
            config.campanha_nome = request.form.get("campanha_nome", config.campanha_nome)
            config.valor_premiacao = float(request.form.get("valor_premiacao", 50))
            lista_campanha = _aplicar_lista_na_config(
                config,
                request.form.get("tipo_lista", "proximos_inativacao"),
            )
            config.ativo = request.form.get("ativo") == "on"
            config.atualizado_por_id = current_user.id
            db.session.commit()
            flash("Configurações salvas com sucesso!", "success")
            return redirect(url_for("campanha_configuracao"))

        return render_template(
            "campanhas/configuracao.html",
            campanha_config=config,
            lista_campanha=lista_campanha,
            listas_campanha=LISTAS_CAMPANHA,
            formatar_moeda_br=_formatar_moeda_br,
        )

    @app.route("/campanhas/reativacao-premiada/historico")
    @login_required
    def campanha_historico_premiacoes():
        """Histórico de premiações pagas."""
        if current_user.tipo not in _TIPOS_CAMPANHA:
            flash("Acesso não autorizado.", "danger")
            return redirect(url_for("meus_clientes"))

        hoje = datetime.now()
        data_inicio = request.args.get("data_inicio", (hoje - timedelta(days=90)).strftime("%Y-%m-%d"))
        data_fim = request.args.get("data_fim", (hoje + timedelta(days=1)).strftime("%Y-%m-%d"))

        try:
            data_inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
            data_fim_dt = datetime.strptime(data_fim, "%Y-%m-%d")
        except ValueError:
            data_inicio_dt = hoje - timedelta(days=90)
            data_fim_dt = hoje + timedelta(days=1)

        premiacoes = (
            PremiacaoReativacao.query
            .filter(
                PremiacaoReativacao.data_pagamento.isnot(None),
                PremiacaoReativacao.data_pagamento >= data_inicio_dt,
                PremiacaoReativacao.data_pagamento < data_fim_dt,
            )
            .order_by(PremiacaoReativacao.data_pagamento.desc())
            .all()
        )

        resumo_por_representante = {}
        for p in premiacoes:
            cd = p.cd_representante
            if cd not in resumo_por_representante:
                resumo_por_representante[cd] = {
                    "nome": p.nome_representante,
                    "quantidade": 0,
                    "total": 0.0,
                }
            resumo_por_representante[cd]["quantidade"] += 1
            resumo_por_representante[cd]["total"] += float(p.valor_premiacao or 50)

        total_pago = sum(r["total"] for r in resumo_por_representante.values())

        return render_template(
            "campanhas/historico_premiacoes.html",
            premiacoes=premiacoes,
            resumo=resumo_por_representante,
            total_pago=total_pago,
            data_inicio=data_inicio,
            data_fim=data_fim,
            formatar_moeda_br=_formatar_moeda_br,
        )
