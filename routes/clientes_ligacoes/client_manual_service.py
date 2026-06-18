from datetime import datetime

from core.extensions import db
from core.helpers import s, so_digits
from core.models import Cliente, Nota
from routes.clientes_ligacoes.cache_invalidation import invalidar_caches_listagens_clientes


def criar_ou_atualizar_cliente_manual(payload, current_user):
    nome = s(payload.get("nome"))
    cnpj = so_digits(payload.get("cnpj")) or None
    telefone = so_digits(payload.get("telefone")) or None
    telefone2 = so_digits(payload.get("telefone2")) or None
    representante = s(payload.get("representante_nome")) or None
    cd_cliente_oracle = s(payload.get("cd_cliente_oracle")) or None
    representante_oracle = s(payload.get("representante_oracle")) or None
    categoria_consultor = s(payload.get("categoria_consultor")) or None
    conceito = s(payload.get("conceito")) or None
    municipio = s(payload.get("municipio")) or None
    uf = s(payload.get("uf")) or None
    contato = s(payload.get("contato")) or None

    if not nome:
        return {"ok": False, "mensagem": "Nome é obrigatório"}, 400

    consultor_id = None
    if current_user.tipo == "supervisor":
        consultor_id = int(payload.get("consultor_id") or 0) or None
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
            existente.origem = "manual"
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
                texto=(
                    f"Cliente atualizado/reativado manualmente por {current_user.nome} "
                    f"em {datetime.now().strftime('%d/%m/%Y %H:%M')}."
                ),
            )
            db.session.add(n)

            db.session.commit()
            invalidar_caches_listagens_clientes("criacao/reativacao de cliente manual")
            return {
                "ok": True,
                "mensagem": "Cliente atualizado (reativado) com sucesso!",
                "cliente_id": existente.id,
            }, 200

    novo = Cliente(
        nome=nome[:200],
        cnpj=cnpj,
        telefone=telefone,
        telefone2=telefone2,
        representante_nome=representante,
        consultor_id=consultor_id,
        ativo=True,
        origem="manual",
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
        texto=f"Cliente criado manualmente por {current_user.nome} em {datetime.now().strftime('%d/%m/%Y %H:%M')}.",
    )
    db.session.add(n)

    db.session.commit()
    invalidar_caches_listagens_clientes("criacao de cliente manual")
    return {
        "ok": True,
        "mensagem": "Cliente criado com sucesso!",
        "cliente_id": novo.id,
    }, 200
