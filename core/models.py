from datetime import datetime

from flask_login import UserMixin

from core.extensions import db


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(512), nullable=False)
    tipo = db.Column(
        db.Enum("consultor", "supervisor", "televendas", "supervisor_repr", "representante"),
        default="consultor",
    )
    ativo = db.Column(db.Boolean, default=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.now)
    meta_diaria = db.Column(db.Integer, default=10)
    viu_novidades = db.Column(db.Boolean, default=False)
    codigo_supervisor_tg650 = db.Column(db.String(20), nullable=True)
    codigo_representante = db.Column(db.String(20), nullable=True)


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
    origem = db.Column(db.Enum("importado_csv", "manual", "oracle_sync"), default="manual", nullable=False)

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


class PremiacaoReativacao(db.Model):
    """Registro de premiações pagas a representantes por reativação de clientes"""
    __tablename__ = "premiacoes_reativacao"
    id = db.Column(db.Integer, primary_key=True)
    cd_representante = db.Column(db.String(50), nullable=False, index=True)
    nome_representante = db.Column(db.String(200), nullable=False)
    cd_cliente = db.Column(db.String(50), nullable=False)
    nome_cliente = db.Column(db.String(200), nullable=False)
    cd_pedido = db.Column(db.String(50), nullable=False)
    data_pedido = db.Column(db.Date, nullable=False)
    valor_pedido = db.Column(db.Numeric(12, 2), nullable=False)
    valor_premiacao = db.Column(db.Numeric(12, 2), default=50.00)
    data_pagamento = db.Column(db.DateTime, nullable=True)
    pago_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    observacao = db.Column(db.Text, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.now)

    pago_por = db.relationship("Usuario", foreign_keys=[pago_por_id])

    __table_args__ = (
        db.UniqueConstraint('cd_representante', 'cd_pedido', name='uq_premiacao_pedido'),
    )


class ConfiguracaoPremiacaoExtra(db.Model):
    """Configurações da campanha Premiação Extra Mês — totalmente separada da Reativação Premiada."""
    __tablename__ = "configuracoes_premiacao_extra"
    id = db.Column(db.Integer, primary_key=True)

    # Período de referência
    ano_ref  = db.Column(db.Integer,  nullable=False, default=lambda: datetime.now().year)
    mes_ref  = db.Column(db.Integer,  nullable=False, default=lambda: datetime.now().month)

    # Regra 1 — Meta % com prazo intermediário
    dia_corte_intermediario = db.Column(db.Integer,       nullable=False, default=22)
    pct_meta_intermediaria  = db.Column(db.Numeric(5, 2), nullable=False, default=75.00)   # ex: 75 %
    bonus_meta_no_prazo     = db.Column(db.Numeric(5, 2), nullable=False, default=1.00)    # ex: 1 %
    bonus_meta_fim_mes      = db.Column(db.Numeric(5, 2), nullable=False, default=0.30)    # ex: 0.3 %

    # Regra 2 — Meta de atendimentos
    bonus_atendimentos      = db.Column(db.Numeric(5, 2), nullable=False, default=0.20)    # ex: 0.2 %

    # Regra 3 — Inativos +6 meses
    pct_comissao_inativo    = db.Column(db.Numeric(5, 2), nullable=False, default=6.00)    # ex: 6 %
    min_itens_inativo       = db.Column(db.Integer,       nullable=False, default=3)

    # Regra 4 — Clientes 151-180 dias (valor fixo por pedido)
    valor_premio_151_180    = db.Column(db.Numeric(12, 2), nullable=False, default=50.00)

    # Controle
    ativo          = db.Column(db.Boolean,  nullable=False, default=True)
    atualizado_em  = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    atualizado_por = db.relationship("Usuario", foreign_keys=[atualizado_por_id])


class CnpjRaizOcultoInativos(db.Model):
    __tablename__ = "cnpjs_raiz_ocultos_inativos"
    id = db.Column(db.Integer, primary_key=True)
    cnpj_raiz = db.Column(db.String(8), nullable=False, unique=True)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    observacao = db.Column(db.String(255), nullable=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    atualizado_por = db.relationship("Usuario", foreign_keys=[atualizado_por_id])


class ConfiguracaoCampanha(db.Model):
    """Configurações dinâmicas da campanha de reativação premiada"""
    __tablename__ = "configuracoes_campanha"
    id = db.Column(db.Integer, primary_key=True)
    campanha_nome = db.Column(db.String(100), nullable=False, default="Reativação Premiada")
    valor_premiacao = db.Column(db.Numeric(12, 2), nullable=False, default=50.00)
    dias_inatividade_inicio = db.Column(db.Integer, nullable=False, default=151)
    dias_inatividade_fim = db.Column(db.Integer, nullable=False, default=180)
    tipo_lista = db.Column(db.String(50), nullable=False, default="proximos_inativacao")
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    atualizado_por_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)

    atualizado_por = db.relationship("Usuario", foreign_keys=[atualizado_por_id])
