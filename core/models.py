from datetime import datetime

from flask_login import UserMixin

from core.extensions import db


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(512), nullable=False)
    tipo = db.Column(db.Enum("consultor", "supervisor"), default="consultor")
    ativo = db.Column(db.Boolean, default=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.now)
    meta_diaria = db.Column(db.Integer, default=10)
    viu_novidades = db.Column(db.Boolean, default=False)


class Cliente(db.Model):
    __tablename__ = "clientes"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(18))
    telefone = db.Column(db.String(20))
    telefone2 = db.Column(db.String(20))
    representante_nome = db.Column(db.String(200))
    consultor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data_cadastro = db.Column(db.DateTime, default=datetime.now)
    ativo = db.Column(db.Boolean, default=True)
    proxima_ligacao = db.Column(db.DateTime, nullable=True)
    origem = db.Column(db.Enum("importado_csv", "manual"), default="manual", nullable=False)

    cd_cliente_oracle = db.Column(db.String(50))
    categoria_consultor = db.Column(db.String(100))
    conceito = db.Column(db.String(20))
    ultimo_pedido_oracle = db.Column(db.DateTime)
    valor_ultimo_pedido = db.Column(db.Numeric(12, 2))
    situacao_ultimo_pedido = db.Column(db.String(50))
    representante_oracle = db.Column(db.String(200))
    municipio = db.Column(db.String(120))
    uf = db.Column(db.String(2))
    contato = db.Column(db.String(200))
    valor_total_365dias = db.Column(db.Numeric(12, 2))
    data_ultima_sincronizacao = db.Column(db.DateTime)

    consultor = db.relationship("Usuario", backref="meus_clientes", foreign_keys=[consultor_id])


class Ligacao(db.Model):
    __tablename__ = "ligacoes"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    consultor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)
    data_hora = db.Column(db.DateTime, default=datetime.now)
    observacao = db.Column(db.Text)
    contato_nome = db.Column(db.String(200))
    resultado = db.Column(
        db.Enum(
            "comprou",
            "nao_comprou",
            "retornar",
            "sem_interesse",
            "relacionamento",
            "cliente_inativo",
        ),
        default="nao_comprou",
    )
    valor_venda = db.Column(db.Numeric(12, 2), default=0)

    cliente = db.relationship("Cliente", backref="ligacoes", foreign_keys=[cliente_id])
    consultor = db.relationship("Usuario", backref="ligacoes", foreign_keys=[consultor_id])


class Nota(db.Model):
    __tablename__ = "notas"
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    texto = db.Column(db.Text, nullable=False)
    data_criacao = db.Column(db.DateTime, default=datetime.now, index=True)

    cliente = db.relationship("Cliente", foreign_keys=[cliente_id])
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])


class Banner(db.Model):
    __tablename__ = "banners"
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.Enum("info", "warning", "success", "danger"), default="info")
    ativo = db.Column(db.Boolean, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.now)
    data_expiracao = db.Column(db.DateTime, nullable=True)
    criado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False)

    criador = db.relationship("Usuario", foreign_keys=[criado_por])
