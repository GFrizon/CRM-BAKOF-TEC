from datetime import datetime

from flask import jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func

from core.models import Cliente, Ligacao


def register_cranio_routes(app):
    def _base_clientes_visiveis():
        q = Cliente.query.filter(Cliente.ativo == True)
        if current_user.tipo in ("consultor", "televendas"):
            q = q.filter(Cliente.consultor_id == current_user.id)
        return q

    def _resumo_hoje():
        hoje = datetime.now().date()
        q_lig = Ligacao.query.filter(func.date(Ligacao.data_hora) == hoje)
        if current_user.tipo in ("consultor", "televendas"):
            q_lig = q_lig.filter(Ligacao.consultor_id == current_user.id)
        total_lig = q_lig.count()
        total_vendas = q_lig.filter(Ligacao.resultado == "comprou").count()
        total_retornar = q_lig.filter(Ligacao.resultado == "retornar").count()
        return (
            f"Hoje: {total_lig} ligacoes, {total_vendas} vendas e {total_retornar} retornos."
        )

    def _retornos_atrasados():
        agora = datetime.now()
        q = _base_clientes_visiveis().filter(
            Cliente.proxima_ligacao.isnot(None),
            Cliente.proxima_ligacao < agora,
        )
        qtd = q.count()
        if qtd == 0:
            return "Nao ha clientes com retorno atrasado no momento."
        nomes = [c.nome for c in q.order_by(Cliente.proxima_ligacao.asc()).limit(5).all()]
        return f"Voce tem {qtd} retornos atrasados. Priorize: {', '.join(nomes)}."

    def _proximos_ligar():
        q = _base_clientes_visiveis().filter(Cliente.proxima_ligacao.isnot(None))
        if current_user.tipo in ("consultor", "televendas"):
            q = q.filter(Cliente.consultor_id == current_user.id)
        itens = q.order_by(Cliente.proxima_ligacao.asc()).limit(5).all()
        if not itens:
            return "Nao encontrei proximos retornos agendados."
        lista = ", ".join(f"{c.nome} ({c.proxima_ligacao.strftime('%d/%m %H:%M')})" for c in itens if c.proxima_ligacao)
        return f"Proximos clientes para ligar: {lista}."

    def _buscar_cliente(texto: str):
        termo = (texto or "").strip()
        if not termo:
            return "Diga o nome ou CNPJ para eu buscar cliente."
        q = _base_clientes_visiveis().filter(
            (Cliente.nome.ilike(f"%{termo}%")) | (Cliente.cnpj.ilike(f"%{termo}%"))
        )
        itens = q.order_by(Cliente.nome.asc()).limit(8).all()
        if not itens:
            return f"Nao encontrei cliente para '{termo}'."
        linhas = []
        for c in itens:
            ult = c.ultimo_pedido_oracle.strftime("%d/%m/%Y") if c.ultimo_pedido_oracle else "-"
            linhas.append(f"{c.nome} | CNPJ {c.cnpj or '-'} | Ult. pedido {ult}")
        return "Encontrei:\n- " + "\n- ".join(linhas)

    def _resolver_pergunta(pergunta: str):
        p = (pergunta or "").strip().lower()
        if not p:
            return "Manda sua pergunta. Ex.: 'meu resumo de hoje' ou 'retornos atrasados'."

        if any(k in p for k in ("resumo", "hoje", "dia")):
            return _resumo_hoje()
        if any(k in p for k in ("retorno atrasado", "atrasado", "atrasados")):
            return _retornos_atrasados()
        if any(k in p for k in ("proximo", "próximo", "ligar agora", "quem ligar")):
            return _proximos_ligar()
        if any(k in p for k in ("buscar", "cliente", "cnpj")):
            termo = pergunta.replace("buscar", "").replace("cliente", "").strip()
            return _buscar_cliente(termo)

        return (
            "Ainda nao entendi essa pergunta. Tenta assim:\n"
            "- resumo de hoje\n"
            "- retornos atrasados\n"
            "- proximos para ligar\n"
            "- buscar cliente <nome ou cnpj>"
        )

    @app.route("/cranio")
    @login_required
    def cranio_page():
        return render_template("cranio.html")

    @app.route("/api/cranio/perguntar", methods=["POST"])
    @login_required
    def cranio_perguntar():
        try:
            payload = request.get_json(silent=True) or {}
            pergunta = str(payload.get("pergunta") or "").strip()
            resposta = _resolver_pergunta(pergunta)
            return jsonify({"ok": True, "resposta": resposta})
        except Exception as e:
            return jsonify({"ok": False, "erro": str(e)}), 500

