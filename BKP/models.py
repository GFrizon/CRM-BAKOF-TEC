# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class Usuario(UserMixin, db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    tipo = db.Column(db.Enum('consultor', 'supervisor'), default='consultor', nullable=False)
    ativo = db.Column(db.Boolean, default=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    # Flask-Login precisa desse property (já herdado de UserMixin)
    # def get_id(self): return str(self.id)

class Cliente(db.Model):
    __tablename__ = 'clientes'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(18))
    telefone = db.Column(db.String(20))
    email = db.Column(db.String(100))
    endereco = db.Column(db.Text)
    consultor_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    data_cadastro = db.Column(db.DateTime, default=datetime.utcnow)

    consultor = db.relationship('Usuario', backref='meus_clientes')

class Ligacao(db.Model):
    __tablename__ = 'ligacoes'
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    consultor_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    data_hora = db.Column(db.DateTime, default=datetime.utcnow)
    observacao = db.Column(db.Text)
    status = db.Column(db.String(50), default='realizada')

    cliente = db.relationship('Cliente', backref='ligacoes')
    consultor = db.relationship('Usuario', backref='ligacoes')

class Importacao(db.Model):
    __tablename__ = 'importacoes'
    id = db.Column(db.Integer, primary_key=True)
    arquivo_nome = db.Column(db.String(255))
    consultor_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    registros_importados = db.Column(db.Integer, default=0)
    data_importacao = db.Column(db.DateTime, default=datetime.utcnow)

    consultor = db.relationship('Usuario', backref='importacoes')
