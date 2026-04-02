from datetime import datetime

from flask_login import UserMixin

from core.extensions import db


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(512), nullable=False)
    tipo = db.Column(db.Enum("consultor", "supervisor", "televendas", "supervisor_repr"), default="consultor")
    ativo = db.Column(db.Boolean, default=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.now)
    meta_diaria = db.Column(db.Integer, default=10)
    viu_novidades = db.Column(db.Boolean, default=False)
    codigo_supervisor_tg650 = db.Column(db.String(20), nullable=True)


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
    em_atendimento_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    em_atendimento_ate = db.Column(db.DateTime, nullable=True)

    consultor = db.relationship("Usuario", backref="meus_clientes", foreign_keys=[consultor_id])
    atendimento_usuario = db.relationship("Usuario", foreign_keys=[em_atendimento_por])


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


class SyncResumoDiario(db.Model):
    __tablename__ = "sync_resumo_diario"
    id = db.Column(db.Integer, primary_key=True)
    data_ref = db.Column(db.Date, nullable=False, unique=True, index=True)
    inativos_entraram = db.Column(db.Integer, nullable=False, default=0)
    inativos_sairam = db.Column(db.Integer, nullable=False, default=0)
    total_inativos = db.Column(db.Integer, nullable=False, default=0)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)


class SupervisorRepresentanteVinculo(db.Model):
    __tablename__ = "supervisor_representante_vinculos"
    id = db.Column(db.Integer, primary_key=True)
    supervisor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    codigo_representante = db.Column(db.String(50), nullable=False, index=True)
    nome_representante = db.Column(db.String(200), nullable=True)
    ativo = db.Column(db.Boolean, default=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.now)
    sincronizado_tg650 = db.Column(db.Boolean, default=False)
    codigo_supervisor_tg650 = db.Column(db.String(20), nullable=True)

    supervisor = db.relationship("Usuario", backref="vinculos_representantes", foreign_keys=[supervisor_id])

    __table_args__ = (
        db.UniqueConstraint('supervisor_id', 'codigo_representante', name='uq_supervisor_representante'),
    )
