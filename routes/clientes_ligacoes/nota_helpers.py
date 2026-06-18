from core.extensions import db
from core.models import Nota
from sqlalchemy.orm import joinedload


def buscar_notas_cliente(cliente_id):
    return (
        Nota.query
        .options(joinedload(Nota.usuario))
        .filter(Nota.cliente_id == cliente_id)
        .order_by(Nota.data_criacao.desc())
        .all()
    )


def criar_nota_cliente(cliente_id, usuario_id, texto):
    nota = Nota(cliente_id=cliente_id, usuario_id=usuario_id, texto=texto)
    db.session.add(nota)
    db.session.commit()
    return nota
