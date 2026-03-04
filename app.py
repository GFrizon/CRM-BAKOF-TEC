import os
os.environ["OTEL_SDK_DISABLED"] = "true"

from dotenv import load_dotenv

APP_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(APP_DIR, ".env"))

from datetime import datetime, timedelta, date
import re
import time
import pandas as pd

from flask import (
    Flask, request, render_template, redirect, url_for,
    flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, login_user, logout_user, login_required,
    current_user, UserMixin
)
from sqlalchemy.orm import joinedload
from sqlalchemy import func, desc, case, or_, text, extract
from werkzeug.security import check_password_hash, generate_password_hash

from flask_mail import Mail, Message
from apscheduler.schedulers.background import BackgroundScheduler

# Importar serviço Oracle
from oracle_service import test_oracle_connection, get_clientes_oracle

# Fuso horário São Paulo
os.environ['TZ'] = 'America/Sao_Paulo'
try:
    time.tzset()
except AttributeError:
    pass

# =============================================================================
# CONFIG DB / MAIL
# =============================================================================
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "controle_ligacoes")
SECRET_KEY = os.getenv("SECRET_KEY", "troque-esta-chave-por-uma-bem-grande")

DB_URI = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.office365.com")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "indicadores@bakof.com.br")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
MAIL_DEFAULT_NAME = os.getenv("MAIL_DEFAULT_NAME", "Indicadores Bakof")
MAIL_DEFAULT_FROM = os.getenv("MAIL_DEFAULT_FROM", "indicadores@bakof.com.br")
MAIL_RECIPIENTS = [e.strip() for e in os.getenv(
    "MAIL_RECIPIENTS",
    "gabriel.frizon@bakof.com.br"
).split(",") if e.strip()]

# =============================================================================
# APP
# =============================================================================
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config.update(
    MAIL_SERVER=MAIL_SERVER,
    MAIL_PORT=MAIL_PORT,
    MAIL_USE_TLS=MAIL_USE_TLS,
    MAIL_USE_SSL=MAIL_USE_SSL,
    MAIL_USERNAME=MAIL_USERNAME,
    MAIL_PASSWORD=MAIL_PASSWORD,
    MAIL_DEFAULT_SENDER=(MAIL_DEFAULT_NAME, MAIL_DEFAULT_FROM),
)
mail = Mail(app)

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Registrar filtro Jinja2 para formatação de dinheiro
@app.template_filter('formatar_dinheiro')
def formatar_dinheiro_filter(valor):
    try:
        v = float(valor or 0)
        return f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return "R$ 0,00"

# =============================================================================
# MODELOS
# =============================================================================
class Usuario(UserMixin, db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha_hash = db.Column(db.String(512), nullable=False)
    tipo = db.Column(db.Enum('consultor', 'supervisor'), default='consultor')
    ativo = db.Column(db.Boolean, default=True)
    data_cadastro = db.Column(db.DateTime, default=datetime.now)
    meta_diaria = db.Column(db.Integer, default=10)
    viu_novidades = db.Column(db.Boolean, default=False)  # 🆕 NOVO


class Cliente(db.Model):
    __tablename__ = 'clientes'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    cnpj = db.Column(db.String(18))
    telefone = db.Column(db.String(20))
    telefone2 = db.Column(db.String(20))
    representante_nome = db.Column(db.String(200))
    consultor_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    data_cadastro = db.Column(db.DateTime, default=datetime.now)
    ativo = db.Column(db.Boolean, default=True)
    proxima_ligacao = db.Column(db.DateTime, nullable=True)
    origem = db.Column(db.Enum('importado_csv', 'manual'), default='manual', nullable=False)
    
    # CAMPOS ORACLE PARA INTEGRACAO CRM
    cd_cliente_oracle = db.Column(db.String(50))  # PK do Oracle
    categoria_consultor = db.Column(db.String(100))  # Consultor vinculado
    conceito = db.Column(db.String(20))  # LIBERADO/INADIMPLENTE/SEM_CONCEITO
    ultimo_pedido_oracle = db.Column(db.DateTime)  # Data último pedido
    valor_ultimo_pedido = db.Column(db.Numeric(12, 2))  # Valor último pedido
    situacao_ultimo_pedido = db.Column(db.String(50))  # Situação pedido
    representante_oracle = db.Column(db.String(200))  # Representante Oracle
    valor_total_365dias = db.Column(db.Numeric(12, 2))  # Valor total últimos 365 dias
    data_ultima_sincronizacao = db.Column(db.DateTime)  # Controle sincronização

    consultor = db.relationship('Usuario', backref='meus_clientes', foreign_keys=[consultor_id])


class Ligacao(db.Model):
    __tablename__ = 'ligacoes'
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    consultor_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    data_hora = db.Column(db.DateTime, default=datetime.now)
    observacao = db.Column(db.Text)
    contato_nome = db.Column(db.String(200))
    resultado = db.Column(db.Enum('comprou', 'nao_comprou', 'retornar', 'sem_interesse', 'relacionamento', 'cliente_inativo'),
                          default='nao_comprou')
    valor_venda = db.Column(db.Numeric(12, 2), default=0)

    cliente = db.relationship('Cliente', backref='ligacoes', foreign_keys=[cliente_id])
    consultor = db.relationship('Usuario', backref='ligacoes', foreign_keys=[consultor_id])


class Nota(db.Model):
    __tablename__ = 'notas'
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False, index=True)
    texto = db.Column(db.Text, nullable=False)
    data_criacao = db.Column(db.DateTime, default=datetime.now, index=True)

    cliente = db.relationship('Cliente', foreign_keys=[cliente_id])
    usuario = db.relationship('Usuario', foreign_keys=[usuario_id])


class Banner(db.Model):
    __tablename__ = 'banners'
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(200), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    tipo = db.Column(db.Enum('info', 'warning', 'success', 'danger'), default='info')
    ativo = db.Column(db.Boolean, default=True)
    data_criacao = db.Column(db.DateTime, default=datetime.now)
    data_expiracao = db.Column(db.DateTime, nullable=True)
    criado_por = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    
    criador = db.relationship('Usuario', foreign_keys=[criado_por])


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

# =============================================================================
# HELPERS
# =============================================================================
def s(v):
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def so_digits(v):
    return re.sub(r"\D+", "", s(v))


def formatar_dinheiro(valor):
    try:
        v = float(valor or 0)
        return f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except:
        return "0,00"


def get_pos(row, idx):
    try:
        return row.iloc[idx]
    except Exception:
        return ""


def _kfmt(n):
    try:
        n = float(n or 0)
    except:
        n = 0
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(int(n))


def _percent(a, b):
    try:
        a = float(a or 0)
        b = float(b or 0)
        return (a / b * 100) if b else 0.0
    except:
        return 0.0

# =============================================================================
# LOGIN / BASE
# =============================================================================
@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard_supervisor' if current_user.tipo == 'supervisor' else 'meus_clientes'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = s(request.form.get('email'))
        senha = request.form.get('senha') or ""

        user = Usuario.query.filter_by(email=email, ativo=True).first()
        if not user:
            flash('Usuário não encontrado ou inativo.', 'danger')
            return render_template('login.html')

        try:
            okpwd = check_password_hash(user.senha_hash, senha)
        except Exception:
            okpwd = False

        if not okpwd:
            flash('Senha inválida.', 'danger')
            return render_template('login.html')

        login_user(user, remember=False, duration=timedelta(hours=4))
        flash('Login realizado com sucesso!', 'success')
        return redirect(url_for('index'))

    return render_template('login.html')


@app.route('/logout')
def logout():
    if current_user.is_authenticated:
        logout_user()
        flash('Você saiu do sistema.', 'info')
    return redirect(url_for('login'))

# =============================================================================
# 🆕 MARCAR NOVIDADES COMO VISTAS
# =============================================================================
@app.route('/marcar-novidades-vistas', methods=['POST'])
@login_required
def marcar_novidades_vistas():
    try:
        current_user.viu_novidades = True
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": str(e)}), 500

# =============================================================================
# LISTAGEM DE CLIENTES
# =============================================================================
@app.route('/meus-clientes')
def meus_clientes():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))

    if current_user.tipo not in ('consultor', 'supervisor'):
        flash('Perfil sem acesso.', 'danger')
        return redirect(url_for('index'))

    aba = request.args.get('aba', 'pendentes')
    apenas_meus = True if current_user.tipo == 'consultor' else (request.args.get('meus') == '1')
    
    # Tratar aba Oracle
    if aba == 'oracle':
        # Para aba Oracle, mostrar apenas clientes com cd_cliente_oracle
        q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(
            Cliente.cd_cliente_oracle.isnot(None),
            Cliente.ativo == True
        )
        if apenas_meus:
            q = q.filter(Cliente.consultor_id == current_user.id)
        
        # Aplicar filtros Oracle
        periodo_oracle = request.args.get('periodo_oracle')
        conceito_filtro = request.args.get('conceito_filtro')
        consultor_filtro = request.args.get('consultor_filtro')
        
        # Filtro por período sem compra
        if periodo_oracle:
            try:
                dias = int(periodo_oracle)
                data_limite = datetime.now() - timedelta(days=dias)
                q = q.filter(Cliente.ultimo_pedido_oracle <= data_limite)
                app.logger.info(f"Filtro período aplicado: {dias} dias (data limite: {data_limite})")
            except ValueError:
                app.logger.warning(f"Valor inválido para período: {periodo_oracle}")
                pass  # Ignora se o valor não for válido
        
        if conceito_filtro:
            q = q.filter(Cliente.conceito == conceito_filtro)
            app.logger.info(f"Filtro conceito aplicado: {conceito_filtro}")
        
        if consultor_filtro:
            q = q.filter(Cliente.categoria_consultor.like(f'%{consultor_filtro}%'))
            app.logger.info(f"Filtro consultor aplicado: {consultor_filtro}")
        
        termo = s(request.args.get('q'))
        if termo:
            like = f"%{termo}%"
            q = q.filter(or_(
                Cliente.nome.like(like),
                Cliente.cnpj.like(like),
                Cliente.telefone.like(like),
                Cliente.representante_nome.like(like),
                Cliente.categoria_consultor.like(like),
                Cliente.conceito.like(like)
            ))
        
        # Manter ordenação original por nome (será feita após cálculo)
        clientes_oracle = q.order_by(Cliente.nome.asc()).all()
        
        # Agrupar clientes por representante_oracle
        representantes_data = {}
        for c in clientes_oracle:
            # Obter nome do representante Oracle
            representante = c.representante_oracle or 'SEM REPRESENTANTE'
            
            # Criar entrada para o representante se não existir
            if representante not in representantes_data:
                representantes_data[representante] = {
                    'nome': representante,
                    'clientes': [],
                    'total_clientes': 0,
                    'liberados': 0,
                    'inadimplentes': 0,
                    'sem_conceito': 0,
                    'ticket_medio': 0,
                    'dias_medio': 0,
                    'consultores_internos': {}  # Mantém sincronização com consultores
                }
            
            # Preparar dados do cliente mantendo sincronização
            ligs = sorted(c.ligacoes, key=lambda x: x.data_hora, reverse=True)
            ultima = ligs[0] if ligs else None
            total = len(ligs)
            
            dados_cliente = {
                "id": c.id,
                "nome": c.nome,
                "cnpj": c.cnpj,
                "telefone": c.telefone,
                "representante_nome": c.representante_nome,
                "ultima_ligacao": ultima.data_hora if ultima else None,
                "total_ligacoes": total,
                "proxima_ligacao": c.proxima_ligacao,
                "origem": getattr(c, 'origem', None),
                "cd_cliente_oracle": c.cd_cliente_oracle,
                "categoria_consultor": c.categoria_consultor,  # Mantido!
                "consultor_id": c.consultor_id,  # Mantido!
                "conceito": c.conceito,
                "ultimo_pedido_oracle": c.ultimo_pedido_oracle,
                "valor_ultimo_pedido": c.valor_ultimo_pedido,
                "valor_total_365dias": c.valor_total_365dias,  # NOVO: Valor total 365 dias
                "situacao_ultimo_pedido": c.situacao_ultimo_pedido,
                "representante_oracle": c.representante_oracle,
            }
            
            # Adicionar cliente ao representante
            representantes_data[representante]['clientes'].append(dados_cliente)
            
            # Agrupar por consultor interno (opcional, para estatísticas)
            if c.consultor:
                nome_consultor = c.consultor.nome
                if nome_consultor not in representantes_data[representante]['consultores_internos']:
                    representantes_data[representante]['consultores_internos'][nome_consultor] = 0
                representantes_data[representante]['consultores_internos'][nome_consultor] += 1
        
        # Calcular estatísticas por representante e ordenar clientes por valor
        for representante, dados in representantes_data.items():
            clientes_rep = dados['clientes']
            
            # Estatísticas básicas
            dados['total_clientes'] = len(clientes_rep)
            dados['liberados'] = sum(1 for c in clientes_rep if c.get('conceito') == 'LIBERADO')
            dados['inadimplentes'] = sum(1 for c in clientes_rep if c.get('conceito') == 'INADIMPLENTE')
            dados['sem_conceito'] = sum(1 for c in clientes_rep if c.get('conceito') in ['SEM CONCEITO', None])
            
            # Ticket médio
            valores = [c.get('valor_ultimo_pedido', 0) for c in clientes_rep if c.get('valor_ultimo_pedido')]
            dados['ticket_medio'] = sum(valores) / len(valores) if valores else 0
            
            # Dias médio sem pedido
            hoje = datetime.now()
            dias_sem_pedido = []
            for c in clientes_rep:
                if c.get('ultimo_pedido_oracle'):
                    dias = (hoje - c['ultimo_pedido_oracle']).days
                    dias_sem_pedido.append(dias)
            dados['dias_medio'] = sum(dias_sem_pedido) / len(dias_sem_pedido) if dias_sem_pedido else 0
            
            # 🎯 ORDENAR CLIENTES POR VALOR TOTAL 365 DIAS (maior para menor)
            dados['clientes'] = sorted(
                clientes_rep, 
                key=lambda x: (
                    float(x.get('valor_total_365dias') or 0),  # Valor 365 dias
                    float(x.get('valor_ultimo_pedido') or 0)  # Backup: último pedido
                ), 
                reverse=True
            )
        
        # Converter para lista ordenada por número de clientes (maior para menor)
        representantes_ordenados = sorted(
            representantes_data.items(), 
            key=lambda x: x[1]['total_clientes'], 
            reverse=True
        )
        
        # Obter lista de consultores únicos para filtro
        consultores_oracle = []
        if representantes_data:
            consultores_set = set()
            for representante, dados in representantes_data.items():
                for c in dados['clientes']:
                    if c.get('categoria_consultor'):
                        consultores_set.add(c.get('categoria_consultor'))
            
            # Criar objetos de consultor para o template
            for nome in sorted(consultores_set):
                consultores_oracle.append({'nome': nome})
        
        # Calcular totais para as outras abas
        todos_clientes = Cliente.query.filter_by(ativo=True)
        if apenas_meus:
            todos_clientes = todos_clientes.filter(Cliente.consultor_id == current_user.id)
        
        total_pendentes = todos_clientes.filter(Cliente.id.notin_(
            db.session.query(Ligacao.cliente_id).filter(
                Ligacao.consultor_id == current_user.id if apenas_meus else True
            )
        )).count()
        
        total_contatados = todos_clientes.filter(Cliente.id.in_(
            db.session.query(Ligacao.cliente_id).filter(
                Ligacao.consultor_id == current_user.id if apenas_meus else True
            )
        )).filter(Cliente.proxima_ligacao.is_(None)).count()
        
        total_retornar = todos_clientes.filter(Cliente.proxima_ligacao.isnot(None)).count()
        total_oracle = sum(len(dados['clientes']) for dados in representantes_data.values())
        
        # Calcular estatísticas Oracle gerais (de todos os representantes)
        stats_oracle = {}
        total_clientes_oracle = 0
        total_liberados = 0
        total_inadimplentes = 0
        total_sem_conceito = 0
        todos_valores = []
        todos_dias = []
        
        for representante, dados in representantes_data.items():
            clientes_rep = dados['clientes']
            total_clientes_oracle += len(clientes_rep)
            total_liberados += dados['liberados']
            total_inadimplentes += dados['inadimplentes']
            total_sem_conceito += dados['sem_conceito']
            
            # Coletar valores e dias para cálculos gerais
            for c in clientes_rep:
                if c.get('valor_ultimo_pedido'):
                    todos_valores.append(c.get('valor_ultimo_pedido'))
                if c.get('ultimo_pedido_oracle'):
                    dias = (datetime.now() - c['ultimo_pedido_oracle']).days
                    todos_dias.append(dias)
        
        # Calcular estatísticas gerais
        ticket_medio_geral = sum(todos_valores) / len(todos_valores) if todos_valores else 0
        dias_medio_geral = sum(todos_dias) / len(todos_dias) if todos_dias else 0
        
        stats_oracle = {
            'liberados': total_liberados,
            'inadimplentes': total_inadimplentes,
            'sem_conceito': total_sem_conceito,
            'ticket_medio': ticket_medio_geral,
            'dias_sem_pedido': int(dias_medio_geral),
            'perc_liberados': round((total_liberados / total_clientes_oracle) * 100, 1) if total_clientes_oracle > 0 else 0,
            'perc_inadimplentes': round((total_inadimplentes / total_clientes_oracle) * 100, 1) if total_clientes_oracle > 0 else 0,
            'perc_sem_conceito': round((total_sem_conceito / total_clientes_oracle) * 100, 1) if total_clientes_oracle > 0 else 0
        }
        
        # Renderizar template com dados Oracle
        return render_template('meus_clientes.html',
                             representantes=representantes_ordenados,  # Nova variável
                             aba=aba,
                             total_pendentes=total_pendentes,
                             total_contatados=total_contatados,
                             total_retornar=total_retornar,
                             total_oracle=total_oracle,
                             is_supervisor=current_user.tipo == 'supervisor',
                             stats={},
                             stats_oracle=stats_oracle,
                             consultores_oracle=consultores_oracle,
                             q=request.args.get('q', ''),
                             meses_disponiveis_consultor=[],
                             mes_filtro=None,
                             ano_filtro=None)
    
    # Parâmetros de filtro mensal para consultores
    mes_filtro = None
    ano_filtro = None
    if current_user.tipo == 'consultor':
        mes_filtro = request.args.get('mes')
        ano_filtro = request.args.get('ano')
        if mes_filtro:
            mes_filtro = int(mes_filtro)
        if ano_filtro:
            ano_filtro = int(ano_filtro)

    q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
    if apenas_meus:
        q = q.filter(Cliente.consultor_id == current_user.id)

    termo = s(request.args.get('q'))
    if termo:
        like = f"%{termo}%"
        q = q.filter(or_(
            Cliente.nome.like(like),
            Cliente.cnpj.like(like),
            Cliente.telefone.like(like),
            Cliente.representante_nome.like(like)
        ))

    clientes_todos = q.order_by(Cliente.nome.asc()).all()

    pendentes, contatados, precisa_retornar = [], [], []
    agora = datetime.now()

    for c in clientes_todos:
        ligs = sorted(c.ligacoes, key=lambda x: x.data_hora, reverse=True)
        ultima = ligs[0] if ligs else None
        total = len(ligs)
        dados = {
            "id": c.id,
            "nome": c.nome,
            "cnpj": c.cnpj,
            "telefone": c.telefone,
            "representante_nome": c.representante_nome,
            "ultima_ligacao": ultima.data_hora if ultima else None,
            "total_ligacoes": total,
            "proxima_ligacao": c.proxima_ligacao,
            "origem": getattr(c, 'origem', None),
        }

        if total == 0:
            pendentes.append(dados)
        else:
            if c.proxima_ligacao:
                dados["retorno_atrasado"] = (agora >= c.proxima_ligacao)
                precisa_retornar.append(dados)
            else:
                contatados.append(dados)

    if aba == 'pendentes':
        clientes = pendentes
    elif aba == 'retornar':
        clientes = sorted(precisa_retornar, key=lambda x: (x['proxima_ligacao'] or datetime.max))
    else:
        clientes = contatados
        filtro = request.args.get('filtro')
        if filtro == 'antigos':
            limite = datetime.now() - timedelta(days=30)
            clientes = [c for c in clientes if c['ultima_ligacao'] and c['ultima_ligacao'] < limite]
        elif filtro == 'recentes':
            limite = datetime.now() - timedelta(days=7)
            clientes = [c for c in clientes if c['ultima_ligacao'] and c['ultima_ligacao'] >= limite]

    consultores = (Usuario.query
                   .filter_by(tipo='consultor', ativo=True)
                   .order_by(Usuario.nome.asc())
                   .all() if current_user.tipo == 'supervisor' else None)

    stats = {}
    if current_user.tipo == 'consultor':
        hoje_date = datetime.now().date()
        desde7 = datetime.now() - timedelta(days=7)
        desde30 = datetime.now() - timedelta(days=30)

        stats['total_clientes'] = Cliente.query.filter_by(
            consultor_id=current_user.id, ativo=True
        ).count()

        stats['ligacoes_hoje'] = db.session.query(func.count(Ligacao.id)).filter(
            Ligacao.consultor_id == current_user.id,
            func.date(Ligacao.data_hora) == hoje_date
        ).scalar() or 0

        stats['ligacoes_semana'] = db.session.query(func.count(Ligacao.id)).filter(
            Ligacao.consultor_id == current_user.id,
            Ligacao.data_hora >= desde7
        ).scalar() or 0

        stats['ligacoes_mes'] = db.session.query(func.count(Ligacao.id)).filter(
            Ligacao.consultor_id == current_user.id,
            Ligacao.data_hora >= desde30
        ).scalar() or 0

        stats['meta_diaria'] = current_user.meta_diaria or 10
        stats['progresso_meta'] = round(
            (stats['ligacoes_hoje'] / stats['meta_diaria'] * 100) if stats['meta_diaria'] > 0 else 0, 1
        )

        vendas_30 = db.session.query(func.count(Ligacao.id)).filter(
            Ligacao.consultor_id == current_user.id,
            Ligacao.data_hora >= desde30,
            Ligacao.resultado == 'comprou'
        ).scalar() or 0

        stats['taxa_conversao'] = round(
            (vendas_30 / stats['ligacoes_mes'] * 100) if stats['ligacoes_mes'] > 0 else 0, 1
        )

        receita_total = db.session.query(func.sum(Ligacao.valor_venda)).filter(
            Ligacao.consultor_id == current_user.id,
            Ligacao.data_hora >= desde30,
            Ligacao.resultado == 'comprou'
        ).scalar() or 0

        def _fmt_money(v):
            try:
                v = float(v or 0)
                return f"{v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
            except:
                return "0,00"

        stats['receita_mes'] = _fmt_money(receita_total)
    
    # Gerar lista de meses/anos disponíveis para o filtro do consultor
    meses_disponiveis_consultor = []
    if current_user.tipo == 'consultor':
        data_atual = datetime.now()
        meses_nomes = {
            1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
            5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
            9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
        }
        for i in range(12):
            data = data_atual - timedelta(days=30*i)
            meses_disponiveis_consultor.append({
                "mes": data.month,
                "ano": data.year,
                "texto": f"{meses_nomes[data.month]}/{data.year}"
            })

    return render_template(
        'meus_clientes.html',
        clientes=clientes,
        total_pendentes=len(pendentes),
        total_contatados=len(contatados),
        total_retornar=len(precisa_retornar),
        aba=aba,
        is_supervisor=(current_user.tipo == 'supervisor'),
        now=datetime.now,
        consultores=consultores,
        stats=stats,
        mostrar_novidades=not current_user.viu_novidades,  # NOVO
        banners_ativos=get_banners_ativos(),  # BANNERS
        # Filtro mensal para consultores
        mes_filtro=mes_filtro,
        ano_filtro=ano_filtro,
        meses_disponiveis_consultor=meses_disponiveis_consultor
    )

# =============================================================================
# CRIAR CLIENTE MANUALMENTE
# =============================================================================
@app.route('/clientes/criar', methods=['POST'])
@login_required
def criar_cliente_manual():
    try:
        payload = request.get_json(silent=True) or {}
        nome = s(payload.get('nome'))
        cnpj = so_digits(payload.get('cnpj')) or None
        telefone = so_digits(payload.get('telefone')) or None
        representante = s(payload.get('representante_nome')) or None

        if not nome:
            return jsonify({"ok": False, "mensagem": "Nome é obrigatório"}), 400

        consultor_id = None
        if current_user.tipo == 'supervisor':
            consultor_id = int(payload.get('consultor_id') or 0) or None
        if not consultor_id:
            consultor_id = current_user.id

        if cnpj:
            existente = Cliente.query.filter_by(cnpj=cnpj).first()
            if existente:
                existente.nome = nome[:200]
                existente.telefone = telefone
                existente.representante_nome = representante
                existente.consultor_id = consultor_id
                existente.ativo = True
                existente.origem = 'manual'
                db.session.add(existente)

                n = Nota(
                    cliente_id=existente.id,
                    usuario_id=current_user.id,
                    texto=f"Cliente atualizado/reativado manualmente por {current_user.nome} em {datetime.now().strftime('%d/%m/%Y %H:%M')}."
                )
                db.session.add(n)

                db.session.commit()
                return jsonify({
                    "ok": True,
                    "mensagem": "Cliente atualizado (reativado) com sucesso!",
                    "cliente_id": existente.id
                })

        novo = Cliente(
            nome=nome[:200],
            cnpj=cnpj,
            telefone=telefone,
            representante_nome=representante,
            consultor_id=consultor_id,
            ativo=True,
            origem='manual'
        )
        db.session.add(novo)
        db.session.flush()

        n = Nota(
            cliente_id=novo.id,
            usuario_id=current_user.id,
            texto=f"Cliente criado manualmente por {current_user.nome} em {datetime.now().strftime('%d/%m/%Y %H:%M')}."
        )
        db.session.add(n)

        db.session.commit()
        return jsonify({
            "ok": True,
            "mensagem": "Cliente criado com sucesso!",
            "cliente_id": novo.id
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

# =============================================================================
# REGISTRAR LIGAÇÃO
# =============================================================================
@app.route('/registrar-ligacao/<int:cliente_id>', methods=['POST'])
def registrar_ligacao(cliente_id: int):
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401

    try:
        payload = request.get_json(silent=True) or {}
        obs = s(payload.get('observacao'))
        contato_nome = s(payload.get('contato_nome'))
        resultado = s(payload.get('resultado') or 'nao_comprou')

        try:
            valor_venda = float(str(payload.get('valor_venda') or 0).replace(',', '.'))
        except:
            valor_venda = 0.0

        cli = db.session.get(Cliente, cliente_id)
        if not cli:
            return jsonify({"ok": False, "mensagem": "Cliente não encontrado."}), 404

        if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
            return jsonify({"ok": False, "mensagem": "Sem permissão para este cliente."}), 403

        agora = datetime.now()

        if resultado not in ('comprou', 'nao_comprou', 'retornar', 'sem_interesse', 'relacionamento', 'cliente_inativo'):
            resultado = 'nao_comprou'

        lig = Ligacao(
            cliente_id=cliente_id,
            consultor_id=current_user.id,
            data_hora=agora,
            observacao=obs or None,
            contato_nome=contato_nome or None,
            resultado=resultado,
            valor_venda=valor_venda
        )
        db.session.add(lig)

        # Retorno opcional para QUALQUER resultado
        dias_retorno = None
        data_retorno = s(payload.get('data_retorno'))
        try:
            dias_retorno = int(payload.get('dias_retorno')) if payload.get('dias_retorno') else None
        except Exception:
            dias_retorno = None

        # Só agenda retorno se preencher algo
        if data_retorno:
            try:
                d = datetime.strptime(data_retorno, "%Y-%m-%d").date()
                cli.proxima_ligacao = datetime(d.year, d.month, d.day, 9, 0, 0)
            except Exception:
                cli.proxima_ligacao = agora + timedelta(days=30)
        elif dias_retorno and dias_retorno > 0:
            cli.proxima_ligacao = agora + timedelta(days=dias_retorno)
        else:
            # Se não preencher nada, não agenda retorno
            cli.proxima_ligacao = None

        db.session.commit()

        msg = "Ligação registrada!"
        if cli.proxima_ligacao:
            msg = "Ligação registrada! Cliente marcado para retorno."
        elif resultado == 'comprou':
            msg = "Ligação registrada! Venda marcada como 'comprou'."

        return jsonify({"ok": True, "mensagem": msg})

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

# =============================================================================
# EDITAR OBSERVAÇÃO DE LIGAÇÃO
# =============================================================================
@app.route('/editar-observacao/<int:ligacao_id>', methods=['POST'])
@login_required
def editar_observacao(ligacao_id: int):
    try:
        ligacao = db.session.get(Ligacao, ligacao_id)
        if not ligacao:
            return jsonify({"ok": False, "mensagem": "Ligação não encontrada"}), 404
        
        # Verificar permissão
        if current_user.tipo == 'consultor' and ligacao.consultor_id != current_user.id:
            return jsonify({"ok": False, "mensagem": "Sem permissão"}), 403
        
        payload = request.get_json(silent=True) or {}
        nova_obs = s(payload.get('observacao'))
        
        ligacao.observacao = nova_obs or None
        db.session.commit()
        
        return jsonify({"ok": True, "mensagem": "Observação atualizada com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

# =============================================================================
# EDITAR LIGAÇÃO COMPLETA (RESULTADO, VALOR, OBSERVAÇÃO)
# =============================================================================
@app.route('/editar-ligacao/<int:ligacao_id>', methods=['POST'])
@login_required
def editar_ligacao(ligacao_id: int):
    try:
        ligacao = db.session.get(Ligacao, ligacao_id)
        if not ligacao:
            return jsonify({"ok": False, "mensagem": "Ligação não encontrada"}), 404
        
        # Verificar permissão: consultor só pode editar suas próprias ligações
        if current_user.tipo == 'consultor' and ligacao.consultor_id != current_user.id:
            return jsonify({"ok": False, "mensagem": "Sem permissão para editar esta ligação"}), 403
        
        payload = request.get_json(silent=True) or {}
        
        # Editar resultado
        if 'resultado' in payload:
            novo_resultado = s(payload.get('resultado'))
            if novo_resultado in ('comprou', 'nao_comprou', 'retornar', 'sem_interesse', 'relacionamento', 'cliente_inativo'):
                ligacao.resultado = novo_resultado
        
        # Editar valor da venda
        if 'valor_venda' in payload:
            try:
                novo_valor = float(str(payload.get('valor_venda') or 0).replace(',', '.'))
                ligacao.valor_venda = novo_valor
            except:
                ligacao.valor_venda = 0.0
        
        # Editar observação
        if 'observacao' in payload:
            ligacao.observacao = s(payload.get('observacao')) or None
        
        db.session.commit()
        return jsonify({"ok": True, "mensagem": "Ligação atualizada com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

# =============================================================================
# OBTER DETALHES DA LIGAÇÃO PARA EDIÇÃO
# =============================================================================
@app.route('/api/detalhes-ligacao/<int:ligacao_id>')
@login_required
def api_detalhes_ligacao(ligacao_id: int):
    try:
        ligacao = db.session.get(Ligacao, ligacao_id)
        if not ligacao:
            return jsonify({"erro": "Ligação não encontrada"}), 404
        
        # Verificar permissão
        if current_user.tipo == 'consultor' and ligacao.consultor_id != current_user.id:
            return jsonify({"erro": "Sem permissão"}), 403
        
        return jsonify({
            "id": ligacao.id,
            "resultado": ligacao.resultado,
            "valor_venda": float(ligacao.valor_venda or 0),
            "valor_venda_fmt": formatar_dinheiro(ligacao.valor_venda),
            "observacao": ligacao.observacao,
            "contato_nome": ligacao.contato_nome,
            "data_hora": ligacao.data_hora.strftime("%d/%m/%Y %H:%M") if ligacao.data_hora else ""
        })
        
    except Exception as e:
        return jsonify({"erro": f"Erro: {str(e)}"}), 500

# =============================================================================
# HISTÓRICO LIGAÇÕES
# =============================================================================
@app.route('/historico-ligacoes/<int:cliente_id>')
def historico_ligacoes(cliente_id: int):
    if not current_user.is_authenticated:
        return jsonify([])

    try:
        cli = db.session.get(Cliente, cliente_id)
        if not cli:
            return jsonify([])

        if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
            return jsonify([])

        regs = (Ligacao.query
                .options(joinedload(Ligacao.consultor))
                .filter(Ligacao.cliente_id == cliente_id)
                .order_by(Ligacao.data_hora.desc())
                .all())

        out = []
        for r in regs:
            try:
                dt = r.data_hora.strftime("%d/%m/%Y %H:%M") if r.data_hora else ""
                consultor_nome = r.consultor.nome if getattr(r, "consultor", None) else ""
                contato = s(r.contato_nome)
                resultado = s(r.resultado)
                try:
                    valor_num = float(r.valor_venda or 0)
                except Exception:
                    valor_num = 0.0

                out.append({
                    "id": r.id,  # 🆕 NOVO: incluir ID da ligação
                    "data_hora": dt,
                    "consultor": consultor_nome,
                    "contato_nome": contato,
                    "resultado": resultado,
                    "valor_venda": formatar_dinheiro(valor_num),
                    "observacao": s(r.observacao),
                    "pode_editar": (current_user.tipo == 'supervisor' or r.consultor_id == current_user.id)  # 🆕 NOVO
                })
            except Exception:
                continue

        return jsonify(out)

    except Exception:
        return jsonify([])

# =============================================================================
# NOTAS RÁPIDAS
# =============================================================================
@app.route('/clientes/<int:cliente_id>/notas', methods=['GET'])
def listar_notas(cliente_id: int):
    if not current_user.is_authenticated:
        return jsonify([])
    notas = (Nota.query
             .options(joinedload(Nota.usuario))
             .filter(Nota.cliente_id == cliente_id)
             .order_by(Nota.data_criacao.desc())
             .all())
    out = [{
        "id": n.id,
        "autor": n.usuario.nome if n.usuario else "",
        "texto": n.texto,
        "quando": n.data_criacao.strftime("%d/%m/%Y %H:%M")
    } for n in notas]
    return jsonify(out)


@app.route('/clientes/<int:cliente_id>/notas', methods=['POST'])
def adicionar_nota(cliente_id: int):
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401
    texto = s((request.get_json(silent=True) or {}).get('texto'))
    if not texto:
        return jsonify({"ok": False, "mensagem": "Texto obrigatório"}), 400

    cli = db.session.get(Cliente, cliente_id)
    if not cli:
        return jsonify({"ok": False, "mensagem": "Cliente não encontrado"}), 404

    if current_user.tipo == 'consultor' and cli.consultor_id != current_user.id:
        return jsonify({"ok": False, "mensagem": "Sem permissão"}), 403

    n = Nota(cliente_id=cliente_id, usuario_id=current_user.id, texto=texto)
    db.session.add(n)
    db.session.commit()
    return jsonify({"ok": True, "mensagem": "Nota adicionada!"})

# =============================================================================
# IMPORTAÇÃO DE CLIENTES
# =============================================================================
@app.route('/importar-clientes', methods=['GET', 'POST'])
def importar_clientes_view():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))

    if current_user.tipo != 'supervisor':
        flash('Acesso permitido somente para supervisores.', 'danger')
        return redirect(url_for('meus_clientes'))

    if request.method == 'POST':
        consultor_id = request.form.get('consultor_id')
        arquivo = request.files.get('arquivo')

        if not consultor_id or not arquivo:
            flash('Selecione o consultor e o arquivo (.xlsx ou .csv).', 'warning')
            return redirect(url_for('importar_clientes_view'))

        consultor_id = int(consultor_id)
        filename = getattr(arquivo, "filename", "") or ""
        ext = (filename.rsplit('.', 1)[-1].lower() if '.' in filename else "")

        df = None
        try:
            if ext in ("xlsx", "xls") or not ext:
                df = pd.read_excel(
                    arquivo,
                    dtype=str,
                    header=0,
                    keep_default_na=False,
                    na_filter=False,
                    engine="openpyxl"
                )
            else:
                raise ValueError("not excel")
        except Exception:
            try:
                arquivo.seek(0)
            except Exception:
                pass
            try:
                df = pd.read_csv(
                    arquivo, sep=';', dtype=str,
                    encoding='utf-8', keep_default_na=False, na_filter=False
                )
            except UnicodeDecodeError:
                arquivo.seek(0)
                df = pd.read_csv(
                    arquivo, sep=';', dtype=str,
                    encoding='latin1', keep_default_na=False, na_filter=False
                )

        COL_TIPO          = 0
        COL_EMPRESA_CNPJ  = 1
        COL_CONSULTOR_TXT = 2
        COL_REPRESENTANTE = 3
        COL_NOME_CLIENTE  = 4
        COL_TELEFONE      = 5

        total_inseridos, pulados = 0, 0
        erros = []
        batch_size = 100  # Processar em lotes para melhor performance
        batch_clientes = []
        
        app.logger.info(f"Iniciando importação de {len(df)} registros")

        for i, row in df.iterrows():
            try:
                tipo          = s(get_pos(row, COL_TIPO))
                empresa_cnpj  = so_digits(get_pos(row, COL_EMPRESA_CNPJ))
                consultor_txt = s(get_pos(row, COL_CONSULTOR_TXT))
                representante = s(get_pos(row, COL_REPRESENTANTE))
                nome_cliente  = s(get_pos(row, COL_NOME_CLIENTE))

                raw_tel = get_pos(row, COL_TELEFONE)
                if not s(raw_tel):
                    try:
                        for colname, val in row.items():
                            if colname and 'tel' in str(colname).lower():
                                raw_tel = val
                                break
                    except Exception:
                        pass
                telefone = so_digits(raw_tel)
                telefone = telefone if telefone else None

                # Validação de dados
                if nome_cliente and len(nome_cliente.strip()) < 2:
                    app.logger.warning(f"Linha {i+2}: Nome do cliente muito curto: '{nome_cliente}'")
                    erros.append(f"Linha {i+2}: Nome muito curto (mínimo 2 caracteres)")
                    pulados += 1
                    continue
                
                if empresa_cnpj and len(empresa_cnpj) < 11:
                    app.logger.warning(f"Linha {i+2}: CNPJ inválido: {empresa_cnpj}")
                    erros.append(f"Linha {i+2}: CNPJ inválido (mínimo 11 dígitos)")
                    pulados += 1
                    continue
                
                if telefone and (len(telefone) < 10 or len(telefone) > 11):
                    app.logger.warning(f"Linha {i+2}: Telefone com formato inválido: {telefone}")
                    erros.append(f"Linha {i+2}: Telefone inválido (10-11 dígitos)")
                    # Não pular, apenas limpar o telefone
                    telefone = None

                if not any([tipo, empresa_cnpj, consultor_txt, representante, nome_cliente, telefone]):
                    continue

                if not nome_cliente:
                    pulados += 1
                    continue

                if empresa_cnpj:
                    try:
                        existente_ativo = Cliente.query.filter_by(cnpj=empresa_cnpj, ativo=True).first()
                        if existente_ativo:
                            mudou = False
                            if telefone and (not existente_ativo.telefone or existente_ativo.telefone != telefone):
                                existente_ativo.telefone = telefone
                                mudou = True
                            if nome_cliente and nome_cliente != existente_ativo.nome:
                                existente_ativo.nome = nome_cliente[:200]
                                mudou = True
                            if representante and representante != existente_ativo.representante_nome:
                                existente_ativo.representante_nome = representante[:200]
                                mudou = True
                            if consultor_id and existente_ativo.consultor_id != consultor_id:
                                existente_ativo.consultor_id = consultor_id
                                mudou = True
                            if existente_ativo.origem != 'importado_csv':
                                existente_ativo.origem = 'importado_csv'
                                mudou = True

                            if mudou:
                                total_inseridos += 1
                            else:
                                pulados += 1
                            continue

                        existente_inativo = Cliente.query.filter_by(cnpj=empresa_cnpj, ativo=False).first()
                        if existente_inativo:
                            existente_inativo.nome = nome_cliente[:200] or existente_inativo.nome
                            existente_inativo.telefone = telefone
                            existente_inativo.representante_nome = (representante[:200] or None)
                            existente_inativo.consultor_id = consultor_id
                            existente_inativo.ativo = True
                            existente_inativo.origem = 'importado_csv'
                            total_inseridos += 1
                            continue
                    except Exception as db_error:
                        app.logger.error(f"Erro de banco ao processar linha {i+2}: {str(db_error)}")
                        erros.append(f"Linha {i+2}: Erro de banco - {str(db_error)}")
                        continue

                # Adicionar ao batch em vez de inserir imediatamente
                try:
                    novo = Cliente(
                        nome=nome_cliente[:200],
                        cnpj=(empresa_cnpj[:18] or None),
                        telefone=telefone,
                        representante_nome=(representante[:200] or None),
                        consultor_id=consultor_id,
                        ativo=True,
                        origem='importado_csv'
                    )
                    batch_clientes.append(novo)
                    total_inseridos += 1
                    
                    # Processar batch quando atingir o tamanho limite
                    if len(batch_clientes) >= batch_size:
                        try:
                            db.session.add_all(batch_clientes)
                            db.session.flush()  # Flush sem commit para manter transação
                            app.logger.info(f"Processado batch de {len(batch_clientes)} clientes")
                            batch_clientes = []  # Limpar batch
                        except Exception as batch_error:
                            app.logger.error(f"Erro no batch processing: {str(batch_error)}")
                            db.session.rollback()
                            # Tentar inserir um por um se batch falhar
                            for cliente in batch_clientes:
                                try:
                                    db.session.add(cliente)
                                    db.session.flush()
                                except Exception as single_error:
                                    app.logger.warning(f"Erro em cliente individual: {str(single_error)}")
                                    erros.append(f"Erro ao inserir cliente: {str(single_error)}")
                            batch_clientes = []
                            
                except ValueError as val_error:
                    app.logger.warning(f"Valor inválido na linha {i+2}: {str(val_error)}")
                    erros.append(f"Linha {i+2}: Valor inválido - {str(val_error)}")
                    continue
                except Exception as create_error:
                    app.logger.error(f"Erro ao criar cliente linha {i+2}: {str(create_error)}")
                    erros.append(f"Linha {i+2}: Erro criação - {str(create_error)}")
                    continue

            except IndexError as idx_error:
                app.logger.warning(f"Linha {i+2} com formato inválido: {str(idx_error)}")
                erros.append(f"Linha {i+2}: Formato inválido - colunas insuficientes")
                continue
            except Exception as e:
                app.logger.error(f"Erro inesperado na linha {i+2}: {str(e)}")
                erros.append(f"Linha {i+2}: {str(e)}")
                continue

        # Processar batch restante
        if batch_clientes:
            try:
                db.session.add_all(batch_clientes)
                app.logger.info(f"Processado batch final de {len(batch_clientes)} clientes")
            except Exception as final_batch_error:
                app.logger.error(f"Erro no batch final: {str(final_batch_error)}")
                db.session.rollback()
                for cliente in batch_clientes:
                    try:
                        db.session.add(cliente)
                        db.session.flush()
                    except Exception as single_error:
                        app.logger.warning(f"Erro em cliente individual final: {str(single_error)}")
                        erros.append(f"Erro ao inserir cliente final: {str(single_error)}")

        try:
            imp_nome = filename or "upload"
            db.session.execute(
                text("INSERT INTO importacoes (arquivo_nome, consultor_id, registros_importados, data_importacao) "
                     "VALUES (:n, :c, :r, :d)"),
                {"n": imp_nome, "c": consultor_id, "r": total_inseridos, "d": datetime.now()}
            )
        except Exception as import_error:
            app.logger.warning(f"Erro ao registrar importação: {str(import_error)}")

        try:
            db.session.commit()
            app.logger.info(f"Importação concluída: {total_inseridos} inseridos, {pulados} pulados, {len(erros)} erros")
        except Exception as commit_error:
            app.logger.error(f"Erro no commit final: {str(commit_error)}")
            db.session.rollback()
            flash('Erro ao salvar dados no banco. Nenhum dado foi importado.', 'danger')
            return redirect(url_for('importar_clientes_view'))

        msg = f'Importação concluída! Inseridos/Atualizados/Reativados: {total_inseridos} • Pulados: {pulados}'
        if erros:
            msg += f' • Erros: {len(erros)} (mostrando até 50)'
        flash(msg, 'success')
        for e in erros[:50]:
            flash(e, "warning")

        return redirect(url_for('meus_clientes'))

    consultores = Usuario.query.filter_by(tipo='consultor', ativo=True).order_by(Usuario.nome.asc()).all()
    return render_template('importar.html', consultores=consultores)

# =============================================================================
# LIMPAR (INATIVAR) CLIENTES DE UM CONSULTOR
# =============================================================================
@app.route('/limpar-clientes-consultor', methods=['POST'])
@login_required
def limpar_clientes_consultor():
    if not current_user.is_authenticated:
        return jsonify({"ok": False, "mensagem": "Não autenticado"}), 401

    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403

    try:
        payload = request.get_json(silent=True) or {}
        consultor_id = payload.get('consultor_id')

        if not consultor_id:
            return jsonify({"ok": False, "mensagem": "Consultor não informado"}), 400

        clientes = Cliente.query.filter_by(consultor_id=consultor_id, ativo=True).all()
        for cli in clientes:
            cli.ativo = False

        db.session.commit()
        return jsonify({"ok": True, "mensagem": f"{len(clientes)} clientes removidos com sucesso."})

    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

# =============================================================================
# 🆕 FILTRAR RESULTADOS POR MÊS/ANO (SUPERVISOR)
# =============================================================================
@app.route('/api/resultados-por-mes')
@login_required
def api_resultados_por_mes():
    if current_user.tipo != 'supervisor':
        return jsonify({"erro": "Acesso negado"}), 403
    
    try:
        mes = int(request.args.get('mes', datetime.now().month))
        ano = int(request.args.get('ano', datetime.now().year))
        
        # Buscar ligações do mês/ano específico
        ligacoes = (
            db.session.query(
                Usuario.id,
                Usuario.nome,
                func.count(Ligacao.id).label("total_ligacoes"),
                func.sum(case((Ligacao.resultado == 'comprou', 1), else_=0)).label("vendas"),
                func.sum(case((Ligacao.resultado == 'comprou', Ligacao.valor_venda), else_=0)).label("receita")
            )
            .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
            .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
            .filter(or_(
                extract('month', Ligacao.data_hora) == mes,
                Ligacao.id == None
            ))
            .filter(or_(
                extract('year', Ligacao.data_hora) == ano,
                Ligacao.id == None
            ))
            .group_by(Usuario.id, Usuario.nome)
            .order_by(desc("receita"))
            .all()
        )
        
        resultado = []
        for uid, nome, total, vendas, receita in ligacoes:
            total = int(total or 0)
            vendas = int(vendas or 0)
            receita = float(receita or 0)
            conv = _percent(vendas, total) if total else 0.0
            
            resultado.append({
                "id": uid,
                "nome": nome,
                "total_ligacoes": total,
                "vendas": vendas,
                "conversao": round(conv, 1),
                "receita": receita,
                "receita_fmt": formatar_dinheiro(receita)
            })
        
        return jsonify({
            "ok": True,
            "mes": mes,
            "ano": ano,
            "consultores": resultado
        })
        
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500

# =============================================================================
# 🆕 FILTRAR MINHAS LIGAÇÕES POR MÊS/ANO (CONSULTOR)
# =============================================================================
@app.route('/api/minhas-ligacoes-por-mes')
@login_required
def api_minhas_ligacoes_por_mes():
    if current_user.tipo != 'consultor':
        return jsonify({"erro": "Acesso negado"}), 403
    
    try:
        mes = int(request.args.get('mes', datetime.now().month))
        ano = int(request.args.get('ano', datetime.now().year))
        
        # Buscar ligações do consultor no mês/ano específico
        ligacoes = (
            db.session.query(Ligacao)
            .filter(Ligacao.consultor_id == current_user.id)
            .filter(extract('month', Ligacao.data_hora) == mes)
            .filter(extract('year', Ligacao.data_hora) == ano)
            .order_by(Ligacao.data_hora.desc())
            .all()
        )
        
        resultado = []
        for lig in ligacoes:
            resultado.append({
                "id": lig.id,
                "cliente_id": lig.cliente_id,
                "cliente_nome": lig.cliente.nome if lig.cliente else "N/A",
                "data_hora": lig.data_hora.strftime("%d/%m/%Y %H:%M"),
                "resultado": lig.resultado,
                "valor_venda": float(lig.valor_venda or 0),
                "valor_venda_fmt": formatar_dinheiro(lig.valor_venda),
                "observacao": lig.observacao
            })
        
        # Estatísticas do mês
        total_ligacoes = len(resultado)
        vendas = len([l for l in resultado if l["resultado"] == "comprou"])
        receita_total = sum([l["valor_venda"] for l in resultado if l["resultado"] == "comprou"])
        taxa_conversao = _percent(vendas, total_ligacoes) if total_ligacoes else 0
        
        return jsonify({
            "ok": True,
            "mes": mes,
            "ano": ano,
            "ligacoes": resultado,
            "estatisticas": {
                "total_ligacoes": total_ligacoes,
                "vendas": vendas,
                "receita_total": receita_total,
                "receita_fmt": formatar_dinheiro(receita_total),
                "taxa_conversao": round(taxa_conversao, 1)
            }
        })
        
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500

# =============================================================================
# DASHBOARD SUPERVISOR
# =============================================================================
@app.route('/supervisor', endpoint='dashboard_supervisor')
@login_required
def supervisor_dashboard():
    if current_user.tipo != 'supervisor':
        return redirect(url_for('meus_clientes'))

    # 🆕 Parâmetros de filtro de mês/ano
    mes_filtro = int(request.args.get('mes', datetime.now().month))
    ano_filtro = int(request.args.get('ano', datetime.now().year))

    hoje = datetime.now().date()
    desde = datetime.now() - timedelta(days=30)

    total_consultores = Usuario.query.filter_by(tipo='consultor', ativo=True).count()
    total_clientes = Cliente.query.filter_by(ativo=True).count()
    total_ligacoes = Ligacao.query.count()
    ligacoes_hoje = (Ligacao.query
                     .filter(func.date(Ligacao.data_hora) == hoje)
                     .count())

    rows = (db.session.query(Usuario.nome, func.count(Ligacao.id))
            .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
            .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
            .filter(or_(Ligacao.data_hora >= desde, Ligacao.id == None))
            .group_by(Usuario.id, Usuario.nome)
            .order_by(desc(func.count(Ligacao.id)))
            .all())
    ranking = [{"nome": n, "ligacoes": int(q or 0)} for n, q in rows]

    ult7 = (db.session.query(func.date(Ligacao.data_hora), func.count(Ligacao.id))
            .filter(Ligacao.data_hora >= datetime.now() - timedelta(days=7))
            .group_by(func.date(Ligacao.data_hora))
            .order_by(func.date(Ligacao.data_hora))
            .all())
    lig_por_dia = [{"data": d.strftime("%d/%m/%Y"), "data_iso": d.strftime("%Y-%m-%d"), "total": int(t)} for d, t in ult7]

    res = (db.session.query(Ligacao.resultado, func.count(Ligacao.id))
           .filter(Ligacao.data_hora >= desde)
           .group_by(Ligacao.resultado)
           .all())
    resultados_chart = {(r or "nao_comprou"): int(c) for r, c in res}

    progresso = []
    consultores = Usuario.query.filter_by(tipo='consultor', ativo=True).order_by(Usuario.nome).all()
    for u in consultores:
        feitas = (db.session.query(func.count(Ligacao.id))
                  .filter(Ligacao.consultor_id == u.id)
                  .filter(func.date(Ligacao.data_hora) == hoje)
                  .scalar()) or 0
        meta = u.meta_diaria or 0
        perc = round(_percent(feitas, meta), 1) if meta else 0.0
        progresso.append({
            "id": u.id,
            "nome": u.nome,
            "meta": meta,
            "feitas": int(feitas),
            "percentual": perc
        })

    conv_rows = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            func.count(Ligacao.id).label("ligacoes"),
            func.sum(case((Ligacao.resultado == 'comprou', 1), else_=0)).label("vendas"),
            func.sum(case((Ligacao.resultado == 'comprou', Ligacao.valor_venda), else_=0)).label("receita")
        )
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
        .filter(or_(Ligacao.data_hora >= desde, Ligacao.id == None))
        .group_by(Usuario.id, Usuario.nome)
        .order_by(desc("receita"))
        .all()
    )

    conversao = []
    for _, nome, ligs, vend, rec in conv_rows:
        ligs = int(ligs or 0)
        vend = int(vend or 0)
        receita_val = float(rec or 0)
        conv_pct = (vend / ligs * 100) if ligs else 0.0
        conversao.append({
            "nome": nome,
            "ligacoes": ligs,
            "vendas": vend,
            "conversao": round(conv_pct, 1),
            "receita": receita_val,
            "receita_fmt": f"{receita_val:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        })

    # 🆕 Gerar lista de meses/anos disponíveis para o filtro
    meses_disponiveis = []
    data_atual = datetime.now()
    meses_nomes = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
        5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
        9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
    }
    for i in range(12):
        data = data_atual - timedelta(days=30*i)
        meses_disponiveis.append({
            "mes": data.month,
            "ano": data.year,
            "texto": f"{meses_nomes[data.month]}/{data.year}"
        })

    return render_template(
        'supervisor.html',
        total_consultores=total_consultores,
        total_clientes=total_clientes,
        total_ligacoes=total_ligacoes,
        ligacoes_hoje=ligacoes_hoje,
        ranking=ranking,
        ligacoes_por_dia=lig_por_dia,
        resultados_chart=resultados_chart,
        progresso=progresso,
        consultores=consultores,
        conversao=conversao,
        mes_filtro=mes_filtro,
        ano_filtro=ano_filtro,
        meses_disponiveis=meses_disponiveis,
        mostrar_novidades=not current_user.viu_novidades,  # 🆕 NOVO
        banners_ativos=get_banners_ativos()  # 🆕 BANNERS
    )

# =============================================================================
# RELATÓRIO POR E-MAIL
# =============================================================================
def build_relatorio_html():
    hoje = datetime.now().date()
    agora = datetime.now()
    desde7 = agora - timedelta(days=7)
    desde30 = agora - timedelta(days=30)

    total_hoje = (db.session.query(func.count(Ligacao.id))
                  .filter(func.date(Ligacao.data_hora) == hoje).scalar()) or 0
    total_7 = (db.session.query(func.count(Ligacao.id))
               .filter(Ligacao.data_hora >= desde7).scalar()) or 0
    total_30 = (db.session.query(func.count(Ligacao.id))
                .filter(Ligacao.data_hora >= desde30).scalar()) or 0

    resultados = dict(
        (r or 'nao_comprou', int(c))
        for r, c in (
            db.session.query(Ligacao.resultado, func.count(Ligacao.id))
            .filter(Ligacao.data_hora >= desde30)
            .group_by(Ligacao.resultado)
            .all()
        )
    )
    compras_30 = resultados.get('comprou', 0)
    conv_30 = _percent(compras_30, total_30)

    ranking = (
        db.session.query(
            Usuario.nome,
            func.count(Ligacao.id).label("qtd")
        )
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
        .filter(or_(Ligacao.data_hora >= desde30, Ligacao.id == None))
        .group_by(Usuario.id, Usuario.nome)
        .order_by(desc("qtd"))
        .all()
    )

    progresso = []
    consultores_ativos = (
        Usuario.query
        .filter_by(tipo='consultor', ativo=True)
        .order_by(Usuario.nome)
        .all()
    )
    for u in consultores_ativos:
        feitas = (
            db.session.query(func.count(Ligacao.id))
            .filter(Ligacao.consultor_id == u.id)
            .filter(func.date(Ligacao.data_hora) == hoje)
            .scalar()
        ) or 0
        meta = u.meta_diaria or 0
        perc = round(_percent(feitas, meta), 1) if meta else 0.0
        progresso.append((u.nome, feitas, meta, perc))
    progresso.sort(key=lambda x: x[3], reverse=True)

    ult7 = (
        db.session.query(func.date(Ligacao.data_hora), func.count(Ligacao.id))
        .filter(Ligacao.data_hora >= desde7)
        .group_by(func.date(Ligacao.data_hora))
        .order_by(func.date(Ligacao.data_hora))
        .all()
    )

    linhas_ult7 = "".join(
        f"<tr><td>{d.strftime('%d/%m')}</td>"
        f"<td style='text-align:right'>{int(t)}</td></tr>"
        for d, t in ult7
    )

    max_ult7 = max((int(t) for _, t in ult7), default=0)
    linhas_ult7_graf = ""
    if max_ult7 > 0:
        total_blocos = 30
        for d, t in ult7:
            t_int = int(t)
            blocos_preenchidos = int(round(t_int / max_ult7 * total_blocos))
            blocos_preenchidos = max(0, min(blocos_preenchidos, total_blocos))
            barra = "█" * blocos_preenchidos + "░" * (total_blocos - blocos_preenchidos)
            linhas_ult7_graf += (
                "<tr>"
                f"<td>{d.strftime('%d/%m')}</td>"
                f"<td style='font-family:monospace; white-space:nowrap;'>{barra}</td>"
                f"<td style='text-align:right'>{t_int}</td>"
                "</tr>"
            )

    desempenho_hoje = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            Usuario.meta_diaria,
            func.count(Ligacao.id).label("ligacoes"),
            func.sum(
                case((Ligacao.resultado == 'comprou', 1), else_=0)
            ).label("vendas"),
            func.sum(
                case((Ligacao.resultado == 'comprou', Ligacao.valor_venda), else_=0)
            ).label("receita"),
        )
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
        .filter(or_(func.date(Ligacao.data_hora) == hoje, Ligacao.id == None))
        .group_by(Usuario.id, Usuario.nome, Usuario.meta_diaria)
        .order_by(Usuario.nome)
        .all()
    )

    desempenho_30 = (
        db.session.query(
            Usuario.id,
            Usuario.nome,
            func.count(Ligacao.id).label("ligacoes"),
            func.sum(
                case((Ligacao.resultado == 'comprou', 1), else_=0)
            ).label("vendas"),
            func.sum(
                case((Ligacao.resultado == 'comprou', Ligacao.valor_venda), else_=0)
            ).label("receita"),
        )
        .join(Ligacao, Ligacao.consultor_id == Usuario.id, isouter=True)
        .filter(Usuario.tipo == 'consultor', Usuario.ativo == True)
        .filter(or_(Ligacao.data_hora >= desde30, Ligacao.id == None))
        .group_by(Usuario.id, Usuario.nome)
        .order_by(Usuario.nome)
        .all()
    )

    linhas_rank = "".join(
        f"<tr><td>{nome}</td>"
        f"<td style='text-align:right'>{int(q or 0)}</td></tr>"
        for nome, q in ranking
    )

    linhas_prog = "".join(
        f"<tr>"
        f"<td>{nome}</td>"
        f"<td style='text-align:right'>{feitas} / {meta}</td>"
        f"<td style='text-align:right'>{perc:.1f}%</td>"
        f"</tr>"
        for (nome, feitas, meta, perc) in progresso
    )

    linhas_res = "".join(
        f"<tr><td>{lab}</td>"
        f"<td style='text-align:right'>{int(val)}</td></tr>"
        for lab, val in [
            ("Comprou", resultados.get("comprou", 0)),
            ("Rel. (pós-venda)", resultados.get("relacionamento", 0)),
            ("Retornar", resultados.get("retornar", 0)),
            ("Sem interesse", resultados.get("sem_interesse", 0)),
            ("Não comprou", resultados.get("nao_comprou", 0)),
        ]
    )

    linhas_consultor_hoje = ""
    for _id, nome, meta, lig, vend, rec in desempenho_hoje:
        lig = int(lig or 0)
        vend = int(vend or 0)
        rec = float(rec or 0)
        meta = int(meta or 0)
        pct_meta = _percent(lig, meta) if meta else 0.0

        linhas_consultor_hoje += (
            "<tr>"
            f"<td>{nome}</td>"
            f"<td style='text-align:right'>{lig}</td>"
            f"<td style='text-align:right'>{vend}</td>"
            f"<td style='text-align:right'>{formatar_dinheiro(rec)}</td>"
            f"<td style='text-align:right'>{meta}</td>"
            f"<td style='text-align:right'>{pct_meta:.1f}%</td>"
            "</tr>"
        )

    linhas_consultor_30 = ""
    for _id, nome, lig, vend, rec in desempenho_30:
        lig = int(lig or 0)
        vend = int(vend or 0)
        rec = float(rec or 0)
        conv = _percent(vend, lig) if lig else 0.0
        media_dia = lig / 30.0 if lig else 0.0

        total_blocos = 20
        blocos_preenchidos = int(round((conv / 100) * total_blocos))
        blocos_preenchidos = max(0, min(blocos_preenchidos, total_blocos))
        barra = "█" * blocos_preenchidos + "░" * (total_blocos - blocos_preenchidos)

        linhas_consultor_30 += (
            "<tr>"
            f"<td>{nome}</td>"
            f"<td style='text-align:right'>{lig}</td>"
            f"<td style='text-align:right'>{vend}</td>"
            f"<td style='white-space:nowrap;font-family:monospace;font-size:12px'>{barra}</td>"
            f"<td style='text-align:right'>{conv:.1f}%</td>"
            f"<td style='text-align:right'>{formatar_dinheiro(rec)}</td>"
            f"<td style='text-align:right'>{media_dia:.1f}</td>"
            "</tr>"
        )

    html = f"""
    <div style="font-family:Arial,Helvetica,sans-serif; font-size:14px; color:#222;">
      <h2 style="margin:0 0 10px 0;">📊 Relatório de Ligações — {hoje.strftime('%d/%m/%Y')}</h2>
      <p style="margin:0 0 16px 0; color:#555">Resumo do dia, últimos 7 e 30 dias.</p>

      <table cellpadding="0" cellspacing="0" border="0" style="width:100%; margin-bottom:16px">
        <tr>
          <td style="width:33%; background:#f8fafc; padding:12px; border:1px solid #e5e7eb;">
            <div style="font-size:12px; color:#64748b;">Hoje</div>
            <div style="font-size:22px; font-weight:700;">{_kfmt(total_hoje)}</div>
          </td>
          <td style="width:33%; background:#f8fafc; padding:12px; border:1px solid #e5e7eb;">
            <div style="font-size:12px; color:#64748b;">Últimos 7 dias</div>
            <div style="font-size:22px; font-weight:700;">{_kfmt(total_7)}</div>
          </td>
          <td style="width:33%; background:#f8fafc; padding:12px; border:1px solid #e5e7eb;">
            <div style="font-size:12px; color:#64748b;">Últimos 30 dias</div>
            <div style="font-size:22px; font-weight:700;">{_kfmt(total_30)}</div>
          </td>
        </tr>
      </table>

      <table cellpadding="0" cellspacing="0" border="0" style="width:100%; table-layout:fixed;">
        <tr>
          <td style="vertical-align:top; width:50%; padding-right:8px">
            <h3 style="margin:0 0 8px 0;">📈 Gráfico de ligações (7 dias)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9">
                <th align="left">Dia</th>
                <th align="left">Gráfico</th>
                <th align="right">Total</th>
              </tr>
              {linhas_ult7_graf or "<tr><td colspan='3' style='color:#64748b'>Sem dados</td></tr>"}
            </table>

            <h3 style="margin:16px 0 8px 0;">📅 Ligações por dia (7d)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9">
                <th align="left">Dia</th>
                <th align="right">Total</th>
              </tr>
              {linhas_ult7 or "<tr><td colspan='2' style='color:#64748b'>Sem dados</td></tr>"}
            </table>

            <h3 style="margin:16px 0 8px 0;">🏆 Ranking (30d)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9"><th align="left">Consultor</th><th align="right">Ligações</th></tr>
              {linhas_rank or "<tr><td colspan='2' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
          </td>

          <td style="vertical-align:top; width:50%; padding-left:8px">
            <h3 style="margin:0 0 8px 0;">🎯 Progresso meta (hoje)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9">
                <th align="left">Consultor</th>
                <th align="right">Feitas/Meta</th>
                <th align="right">% Meta</th>
              </tr>
              {linhas_prog or "<tr><td colspan='3' style='color:#64748b'>Sem dados</td></tr>"}
            </table>

            <h3 style="margin:16px 0 8px 0;">🧭 Resultados (30d)</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb;">
              <tr style="background:#f1f5f9">
                <th align="left">Status</th>
                <th align="right">Qtde</th>
              </tr>
              {linhas_res or "<tr><td colspan='2' style='color:#64748b'>Sem dados</td></tr>"}
            </table>

            <p style="margin-top:12px; color:#64748b; font-size:12px">
              Conversão (30d): <b>{conv_30:.1f}%</b> — {compras_30} compras de {total_30} ligações.
            </p>

            <h3 style="margin:16px 0 8px 0;">👤 Desempenho por consultor — Hoje</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb; font-size:12px;">
              <tr style="background:#f1f5f9">
                <th align="left">Consultor</th>
                <th align="right">Lig.</th>
                <th align="right">Vend.</th>
                <th align="right">Receita</th>
                <th align="right">Meta</th>
                <th align="right">% Meta</th>
              </tr>
              {linhas_consultor_hoje or "<tr><td colspan='6' style='color:#64748b'>Sem dados</td></tr>"}
            </table>

            <h3 style="margin:16px 0 8px 0;">📅 Desempenho por consultor — 30 dias</h3>
            <table cellpadding="6" cellspacing="0" border="0" style="width:100%; border:1px solid #e5e7eb; font-size:12px;">
              <tr style="background:#f1f5f9">
                <th align="left">Consultor</th>
                <th align="right">Lig.</th>
                <th align="right">Vend.</th>
                <th align="left">Gráfico</th>
                <th align="right">Conv.</th>
                <th align="right">Receita</th>
                <th align="right">Média/dia</th>
              </tr>
              {linhas_consultor_30 or "<tr><td colspan='7' style='color:#64748b'>Sem dados</td></tr>"}
            </table>
          </td>
        </tr>
      </table>
    </div>
    """
    return html

def enviar_relatorio_email(recipients=None):
    recs = recipients or MAIL_RECIPIENTS
    if not recs:
        print("Email: Sem destinatários")
        return False, "Sem destinatários configurados."
    
    if not MAIL_PASSWORD:
        print("Email: Senha não configurada")
        return False, "MAIL_PASSWORD não configurado."
    
    html = build_relatorio_html()
    assunto = f"Relatório de Ligações — {datetime.now().strftime('%d/%m/%Y')}"
    
    try:
        print(f"Tentando enviar email para: {', '.join(recs)}")
        with app.app_context():
            msg = Message(subject=assunto, recipients=recs)
            msg.html = html
            mail.send(msg)
        print(f"Email enviado com sucesso!")
        return True, f"Relatório enviado para: {', '.join(recs)}"
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
        return False, f"Falha ao enviar e-mail: {e}"


@app.route('/admin/enviar-relatorio', methods=['POST', 'GET'])
@login_required
def admin_enviar_relatorio():
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    ok, msg = enviar_relatorio_email()
    if request.method == 'GET':
        flash(msg, 'success' if ok else 'danger')
        return redirect(url_for('dashboard_supervisor'))
    return jsonify({"ok": ok, "mensagem": msg})


@app.route('/admin/testar-scheduler')
@login_required
def testar_scheduler():
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        if _scheduler:
            jobs = _scheduler.get_jobs()
            jobs_info = [{
                "id": job.id,
                "next_run": str(job.next_run_time),
                "trigger": str(job.trigger)
            } for job in jobs]
            
            return jsonify({
                "ok": True,
                "scheduler_running": _scheduler.running,
                "jobs": jobs_info,
                "mensagem": "Scheduler está ativo!"
            })
        else:
            return jsonify({
                "ok": False,
                "mensagem": "Scheduler não inicializado"
            })
    except Exception as e:
        return jsonify({"ok": False, "mensagem": str(e)}), 500

# =============================================================================
# LIGAÇÕES POR DIA (JSON)
# =============================================================================
@app.route('/ligacoes-dia/<string:data>')
def ligacoes_dia(data):
    if not current_user.is_authenticated or current_user.tipo != 'supervisor':
        return jsonify({"erro": "Acesso negado"}), 403

    try:
        data_obj = datetime.strptime(data, "%Y-%m-%d").date()

        ligacoes = (Ligacao.query
                   .options(joinedload(Ligacao.consultor), joinedload(Ligacao.cliente))
                   .filter(func.date(Ligacao.data_hora) == data_obj)
                   .order_by(Ligacao.data_hora.desc())
                   .all())

        resultado = []
        for lig in ligacoes:
            resultado.append({
                "hora": lig.data_hora.strftime("%H:%M"),
                "consultor": lig.consultor.nome if lig.consultor else "",
                "cliente": lig.cliente.nome if lig.cliente else "",
                "contato": lig.contato_nome or "-",
                "resultado": lig.resultado or "nao_comprou",
                "valor": formatar_dinheiro(lig.valor_venda or 0),
                "observacao": lig.observacao or ""
            })

        return jsonify(resultado)

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# =============================================================================
# GERENCIAMENTO DE USUÁRIOS
# =============================================================================
@app.route('/supervisor/usuarios')
@login_required
def gerenciar_usuarios():
    if current_user.tipo != 'supervisor':
        flash('Acesso negado.', 'danger')
        return redirect(url_for('index'))
    
    usuarios = Usuario.query.order_by(Usuario.nome.asc()).all()
    
    usuarios_data = []
    for u in usuarios:
        total_clientes = Cliente.query.filter_by(consultor_id=u.id, ativo=True).count() if u.tipo == 'consultor' else 0
        usuarios_data.append({
            'id': u.id,
            'nome': u.nome,
            'email': u.email,
            'tipo': u.tipo,
            'ativo': u.ativo,
            'meta_diaria': u.meta_diaria or 0,
            'data_cadastro': u.data_cadastro,
            'total_clientes': total_clientes
        })
    
    return render_template('gerenciar_usuarios.html', usuarios=usuarios_data)


@app.route('/supervisor/usuarios/criar', methods=['POST'])
@login_required
def criar_usuario():
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        payload = request.get_json(silent=True) or {}
        nome = s(payload.get('nome'))
        email = s(payload.get('email'))
        senha = payload.get('senha') or ""
        tipo = s(payload.get('tipo'))
        meta_diaria = int(payload.get('meta_diaria') or 10)
        
        if not nome or not email or not senha:
            return jsonify({"ok": False, "mensagem": "Nome, email e senha são obrigatórios"}), 400
        
        if tipo not in ('consultor', 'supervisor'):
            return jsonify({"ok": False, "mensagem": "Tipo inválido"}), 400
        
        if Usuario.query.filter_by(email=email).first():
            return jsonify({"ok": False, "mensagem": "Email já cadastrado"}), 400
        
        novo_usuario = Usuario(
            nome=nome,
            email=email,
            senha_hash=generate_password_hash(senha),
            tipo=tipo,
            meta_diaria=meta_diaria,
            ativo=True
        )
        
        db.session.add(novo_usuario)
        db.session.commit()
        
        return jsonify({"ok": True, "mensagem": f"Usuário {nome} criado com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500


@app.route('/supervisor/usuarios/<int:usuario_id>/editar', methods=['POST'])
@login_required
def editar_usuario(usuario_id):
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        usuario = db.session.get(Usuario, usuario_id)
        if not usuario:
            return jsonify({"ok": False, "mensagem": "Usuário não encontrado"}), 404
        
        payload = request.get_json(silent=True) or {}
        nome = s(payload.get('nome'))
        email = s(payload.get('email'))
        tipo = s(payload.get('tipo'))
        meta_diaria = int(payload.get('meta_diaria') or 10)
        
        if not nome or not email:
            return jsonify({"ok": False, "mensagem": "Nome e email são obrigatórios"}), 400
        
        if tipo not in ('consultor', 'supervisor'):
            return jsonify({"ok": False, "mensagem": "Tipo inválido"}), 400
        
        email_existe = Usuario.query.filter(Usuario.email == email, Usuario.id != usuario_id).first()
        if email_existe:
            return jsonify({"ok": False, "mensagem": "Email já cadastrado por outro usuário"}), 400
        
        usuario.nome = nome
        usuario.email = email
        usuario.tipo = tipo
        usuario.meta_diaria = meta_diaria
        
        db.session.commit()
        
        return jsonify({"ok": True, "mensagem": f"Usuário {nome} atualizado com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500


@app.route('/supervisor/usuarios/<int:usuario_id>/toggle-status', methods=['POST'])
@login_required
def toggle_status_usuario(usuario_id):
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        usuario = db.session.get(Usuario, usuario_id)
        if not usuario:
            return jsonify({"ok": False, "mensagem": "Usuário não encontrado"}), 404
        
        if usuario.id == current_user.id:
            return jsonify({"ok": False, "mensagem": "Você não pode inativar sua própria conta"}), 400
        
        usuario.ativo = not usuario.ativo
        db.session.commit()
        
        status_texto = "ativado" if usuario.ativo else "inativado"
        return jsonify({"ok": True, "mensagem": f"Usuário {usuario.nome} {status_texto} com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500


@app.route('/supervisor/usuarios/<int:usuario_id>/redefinir-senha', methods=['POST'])
@login_required
def redefinir_senha_usuario(usuario_id):
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        usuario = db.session.get(Usuario, usuario_id)
        if not usuario:
            return jsonify({"ok": False, "mensagem": "Usuário não encontrado"}), 404
        
        payload = request.get_json(silent=True) or {}
        nova_senha = payload.get('nova_senha') or ""
        
        if not nova_senha or len(nova_senha) < 6:
            return jsonify({"ok": False, "mensagem": "Senha deve ter no mínimo 6 caracteres"}), 400
        
        usuario.senha_hash = generate_password_hash(nova_senha)
        db.session.commit()
        
        return jsonify({"ok": True, "mensagem": f"Senha de {usuario.nome} redefinida com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500

# =============================================================================
# GERENCIAMENTO DE BANNERS
# =============================================================================
@app.route('/supervisor/banners')
@login_required
def gerenciar_banners():
    if current_user.tipo != 'supervisor':
        return redirect(url_for('meus_clientes'))
    
    banners = (Banner.query
               .options(joinedload(Banner.criador))
               .order_by(Banner.data_criacao.desc())
               .all())
    
    return render_template('gerenciar_banners.html', banners=banners)


@app.route('/supervisor/banners/criar', methods=['POST'])
@login_required
def criar_banner():
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        payload = request.get_json(silent=True) or {}
        titulo = s(payload.get('titulo'))
        mensagem = s(payload.get('mensagem'))
        tipo = s(payload.get('tipo')) or 'info'
        data_expiracao = payload.get('data_expiracao')
        
        if not titulo or not mensagem:
            return jsonify({"ok": False, "mensagem": "Título e mensagem são obrigatórios"}), 400
        
        if tipo not in ['info', 'warning', 'success', 'danger']:
            tipo = 'info'
        
        # Processar data de expiração
        expiracao_dt = None
        if data_expiracao:
            try:
                expiracao_dt = datetime.strptime(data_expiracao, "%Y-%m-%d")
                # Adicionar hora final do dia
                expiracao_dt = expiracao_dt.replace(hour=23, minute=59, second=59)
            except Exception:
                return jsonify({"ok": False, "mensagem": "Data de expiração inválida"}), 400
        
        banner = Banner(
            titulo=titulo,
            mensagem=mensagem,
            tipo=tipo,
            criado_por=current_user.id,
            data_expiracao=expiracao_dt
        )
        db.session.add(banner)
        db.session.commit()
        
        return jsonify({"ok": True, "mensagem": "Banner criado com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500


@app.route('/supervisor/banners/<int:banner_id>/toggle-status', methods=['POST'])
@login_required
def toggle_banner_status(banner_id):
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        banner = db.session.get(Banner, banner_id)
        if not banner:
            return jsonify({"ok": False, "mensagem": "Banner não encontrado"}), 404
        
        banner.ativo = not banner.ativo
        db.session.commit()
        
        status_texto = "ativado" if banner.ativo else "desativado"
        return jsonify({"ok": True, "mensagem": f"Banner {status_texto} com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500


@app.route('/supervisor/banners/<int:banner_id>/excluir', methods=['POST'])
@login_required
def excluir_banner(banner_id):
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso negado"}), 403
    
    try:
        banner = db.session.get(Banner, banner_id)
        if not banner:
            return jsonify({"ok": False, "mensagem": "Banner não encontrado"}), 404
        
        db.session.delete(banner)
        db.session.commit()
        
        return jsonify({"ok": True, "mensagem": "Banner excluído com sucesso!"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"ok": False, "mensagem": f"Erro: {str(e)}"}), 500


# Helper para obter banners ativos
def get_banners_ativos():
    agora = datetime.now()
    return (Banner.query
            .filter(Banner.ativo == True)
            .filter(or_(Banner.data_expiracao == None, Banner.data_expiracao >= agora))
            .order_by(Banner.data_criacao.desc())
            .all())

# =============================================================================
# MINHA CONTA / ALTERAR SENHA
# =============================================================================
@app.route('/minha-conta')
@login_required
def minha_conta():
    stats = {}
    
    if current_user.tipo == 'consultor':
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

# =============================================================================
# BUSCA EM TEMPO REAL SEM ENTER
# =============================================================================
@app.route('/api/busca-clientes')
@login_required
def api_busca_clientes():
    if not current_user.is_authenticated:
        return jsonify({"erro": "Não autenticado"}), 401
    
    try:
        termo = s(request.args.get('q'))
        aba = request.args.get('aba', 'pendentes')
        apenas_meus = True if current_user.tipo == 'consultor' else (request.args.get('meus') == '1')
        
        # Query base
        q = Cliente.query.options(joinedload(Cliente.ligacoes)).filter(Cliente.ativo == True)
        if apenas_meus:
            q = q.filter(Cliente.consultor_id == current_user.id)
        
        if termo:
            like = f"%{termo}%"
            q = q.filter(or_(
                Cliente.nome.like(like),
                Cliente.cnpj.like(like),
                Cliente.telefone.like(like),
                Cliente.representante_nome.like(like)
            ))
        
        clientes_todos = q.order_by(Cliente.nome.asc()).all()
        
        pendentes, contatados, precisa_retornar = [], [], []
        agora = datetime.now()
        
        for c in clientes_todos:
            ligs = sorted(c.ligacoes, key=lambda x: x.data_hora, reverse=True)
            ultima = ligs[0] if ligs else None
            total = len(ligs)
            dados = {
                "id": c.id,
                "nome": c.nome,
                "cnpj": c.cnpj,
                "telefone": c.telefone,
                "representante_nome": c.representante_nome,
                "ultima_ligacao": ultima.data_hora.strftime("%d/%m/%Y %H:%M") if ultima else None,
                "total_ligacoes": total,
                "proxima_ligacao": c.proxima_ligacao.strftime("%d/%m/%Y %H:%M") if c.proxima_ligacao else None,
                "origem": getattr(c, 'origem', None),
            }
            
            if total == 0:
                pendentes.append(dados)
            else:
                if c.proxima_ligacao:
                    dados["retorno_atrasado"] = (agora >= c.proxima_ligacao)
                    precisa_retornar.append(dados)
                else:
                    contatados.append(dados)
        
        # Retornar apenas a aba solicitada
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

# =============================================================================
# REMOVER CLIENTE (inativar)
# =============================================================================
@app.route('/remover-cliente/<int:cliente_id>', methods=['POST'])
@login_required
def remover_cliente(cliente_id):
    try:
        cliente = db.session.get(Cliente, cliente_id)
        if not cliente:
            return jsonify({"ok": False, "mensagem": "Cliente não encontrado"}), 404
        
        if current_user.tipo == 'consultor' and cliente.consultor_id != current_user.id:
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

# =============================================================================
# ROTAS DE TESTE ORACLE - FASE 1
# =============================================================================
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

@app.route('/sincronizar-oracle', methods=['POST'])
@login_required
def sincronizar_oracle():
    """Rota para sincronizar clientes do Oracle com o MySQL"""
    if current_user.tipo != 'supervisor':
        return jsonify({"erro": "Acesso permitido somente para supervisores"}), 403
    
    try:
        # Buscar clientes do Oracle
        clientes_oracle = get_clientes_oracle()
        
        sincronizados = 0
        atualizados = 0
        erros = []
        
        for cliente_oracle in clientes_oracle:
            try:
                cd_cliente = str(cliente_oracle.get('cd_cliente', ''))
                if not cd_cliente:
                    continue
                
                # Verificar se cliente já existe no MySQL
                cliente_mysql = Cliente.query.filter_by(cd_cliente_oracle=cd_cliente).first()
                
                if cliente_mysql:
                    # Atualizar cliente existente
                    cliente_mysql.categoria_consultor = cliente_oracle.get('consultor')
                    cliente_mysql.conceito = cliente_oracle.get('conceito')
                    cliente_mysql.representante_oracle = cliente_oracle.get('representante')
                    cliente_mysql.valor_ultimo_pedido = cliente_oracle.get('total_pedido')
                    cliente_mysql.situacao_ultimo_pedido = cliente_oracle.get('situacao')
                    
                    # Converter data do pedido
                    dt_pedido = cliente_oracle.get('dt_pedido')
                    if dt_pedido:
                        cliente_mysql.ultimo_pedido_oracle = dt_pedido
                    
                    cliente_mysql.data_ultima_sincronizacao = datetime.now()
                    atualizados += 1
                else:
                    # Criar novo cliente
                    # Tentar encontrar consultor pelo nome na categoria_consultor
                    nome_consultor = cliente_oracle.get('consultor', '')
                    consultor = None
                    
                    if nome_consultor:
                        # Extrair nome do consultor (formato: "NUM - NOME")
                        if ' - ' in nome_consultor:
                            nome_consultor = nome_consultor.split(' - ', 1)[1]
                        
                        consultor = Usuario.query.filter_by(
                            nome=nome_consultor.strip(),
                            tipo='consultor',
                            ativo=True
                        ).first()
                    
                    # Se não encontrar consultor, usar o supervisor atual
                    if not consultor:
                        consultor = current_user
                    
                    novo_cliente = Cliente(
                        nome=cliente_oracle.get('cliente', '')[:200],
                        cd_cliente_oracle=cd_cliente,
                        categoria_consultor=cliente_oracle.get('consultor'),
                        conceito=cliente_oracle.get('conceito'),
                        representante_oracle=cliente_oracle.get('representante'),
                        valor_ultimo_pedido=cliente_oracle.get('total_pedido'),
                        situacao_ultimo_pedido=cliente_oracle.get('situacao'),
                        consultor_id=consultor.id,
                        origem='importado_csv',  # Marcado como importado
                        ativo=True
                    )
                    
                    # Converter data do pedido
                    dt_pedido = cliente_oracle.get('dt_pedido')
                    if dt_pedido:
                        novo_cliente.ultimo_pedido_oracle = dt_pedido
                    
                    novo_cliente.data_ultima_sincronizacao = datetime.now()
                    db.session.add(novo_cliente)
                    sincronizados += 1
                
            except Exception as e:
                erros.append(f"Cliente {cd_cliente}: {str(e)}")
                continue
        
        # Commit de todas as alterações
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Sincronização concluída com sucesso!",
            "total_oracle": len(clientes_oracle),
            "sincronizados": sincronizados,
            "atualizados": atualizados,
            "erros": len(erros),
            "detalhes_erros": erros[:5],  # Mostra só os 5 primeiros erros
            "timestamp": datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Erro ao buscar detalhes: {str(e)}"
        }), 500

@app.route('/detalhes-cliente-oracle/<int:cliente_id>')
@login_required
def detalhes_cliente_oracle(cliente_id: int):
    """Busca detalhes completos do cliente Oracle"""
    try:
        # Buscar cliente no MySQL
        cliente = db.session.get(Cliente, cliente_id)
        if not cliente:
            return jsonify({"success": False, "message": "Cliente não encontrado"}), 404
        
        # Verificar permissão
        if current_user.tipo == 'consultor' and cliente.consultor_id != current_user.id:
            return jsonify({"success": False, "message": "Sem permissão para este cliente"}), 403
        
        # Se não tiver cd_cliente_oracle, retorna dados básicos
        if not cliente.cd_cliente_oracle:
            return jsonify({
                "success": True,
                "cliente": {
                    "id": cliente.id,
                    "nome": cliente.nome,
                    "cnpj": cliente.cnpj,
                    "telefone": cliente.telefone,
                    "telefone2": cliente.telefone2,
                    "representante_nome": cliente.representante_nome,
                    "origem": "manual"
                },
                "pedidos_oracle": [],
                "mensagem": "Cliente não possui dados Oracle"
            })
        
        # Buscar pedidos no Oracle
        from oracle_service import get_pedidos_cliente_oracle, get_itens_cliente_oracle
        pedidos_oracle = get_pedidos_cliente_oracle(cliente.cd_cliente_oracle)
        itens_oracle = get_itens_cliente_oracle(cliente.cd_cliente_oracle)
        
        # Retornar dados completos
        return jsonify({
            "success": True,
            "cliente": {
                "id": cliente.id,
                "nome": cliente.nome,
                "cnpj": cliente.cnpj,
                "telefone": cliente.telefone,
                "telefone2": cliente.telefone2,
                "cd_cliente_oracle": cliente.cd_cliente_oracle,
                "categoria_consultor": cliente.categoria_consultor,
                "conceito": cliente.conceito,
                "ultimo_pedido_oracle": cliente.ultimo_pedido_oracle.strftime('%d/%m/%Y') if cliente.ultimo_pedido_oracle else None,
                "valor_ultimo_pedido": float(cliente.valor_ultimo_pedido) if cliente.valor_ultimo_pedido else None,
                "situacao_ultimo_pedido": cliente.situacao_ultimo_pedido,
                "representante_oracle": cliente.representante_oracle,
                "data_ultima_sincronizacao": cliente.data_ultima_sincronizacao.strftime('%d/%m/%Y %H:%M') if cliente.data_ultima_sincronizacao else None
            },
            "pedidos_oracle": pedidos_oracle,
            "itens_oracle": itens_oracle,
            "total_pedidos": len(pedidos_oracle),
            "total_itens": len(itens_oracle)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Erro ao buscar detalhes: {str(e)}"
        }), 500

# =============================================================================
# ERROR HANDLERS
# =============================================================================
@app.errorhandler(404)
def not_found(error):
    db.session.rollback()
    flash('Página não encontrada.', 'warning')
    return redirect(url_for('index'))


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    flash('Erro interno do servidor. Contate o suporte.', 'danger')
    return redirect(url_for('index'))

# =============================================================================
# BOOTSTRAP DB / MIGRAÇÕES SIMPLES
# =============================================================================
with app.app_context():
    db.create_all()
    
    # meta_diaria em usuarios
    try:
        db.session.execute(text("ALTER TABLE usuarios ADD COLUMN meta_diaria INT DEFAULT 10"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    try:
        db.session.execute(text("UPDATE usuarios SET meta_diaria = 10 WHERE meta_diaria IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # coluna viu_novidades em usuarios
    try:
        db.session.execute(text("ALTER TABLE usuarios ADD COLUMN viu_novidades BOOLEAN DEFAULT FALSE"))
        db.session.commit()
    except Exception:
        db.session.rollback()
    try:
        db.session.execute(text("UPDATE usuarios SET viu_novidades = FALSE WHERE viu_novidades IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # coluna origem em clientes
    try:
        db.session.execute(text(
            "ALTER TABLE clientes ADD COLUMN origem ENUM('importado_csv','manual') "
            "NOT NULL DEFAULT 'manual'"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # garantir enum com 'relacionamento' e 'cliente_inativo' em ligacoes.resultado
    try:
        db.session.execute(text(
            "ALTER TABLE ligacoes MODIFY COLUMN resultado "
            "ENUM('comprou','nao_comprou','retornar','sem_interesse','relacionamento','cliente_inativo') "
            "NOT NULL DEFAULT 'nao_comprou'"
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    # Criar tabela de banners
    try:
        Banner.__table__.create(db.engine)
        db.session.commit()
    except Exception:
        db.session.rollback()

    # MIGRAÇÕES ORACLE - FASE 2
    # Adicionar campos Oracle na tabela clientes
    campos_oracle = [
        "ALTER TABLE clientes ADD COLUMN cd_cliente_oracle VARCHAR(50)",
        "ALTER TABLE clientes ADD COLUMN categoria_consultor VARCHAR(100)",
        "ALTER TABLE clientes ADD COLUMN conceito VARCHAR(20)",
        "ALTER TABLE clientes ADD COLUMN ultimo_pedido_oracle DATETIME",
        "ALTER TABLE clientes ADD COLUMN valor_ultimo_pedido DECIMAL(12,2)",
        "ALTER TABLE clientes ADD COLUMN situacao_ultimo_pedido VARCHAR(50)",
        "ALTER TABLE clientes ADD COLUMN representante_oracle VARCHAR(200)",
        "ALTER TABLE clientes ADD COLUMN valor_total_365dias DECIMAL(12,2)",
        "ALTER TABLE clientes ADD COLUMN data_ultima_sincronizacao DATETIME"
    ]
    
    for campo_sql in campos_oracle:
        try:
            db.session.execute(text(campo_sql))
            db.session.commit()
            print(f"✅ Campo Oracle adicionado: {campo_sql}")
        except Exception as e:
            db.session.rollback()
            print(f"⚠️ Campo Oracle já existe ou erro: {campo_sql} - {str(e)}")

    if not MAIL_PASSWORD:
        print("AVISO: MAIL_PASSWORD não configurado! Email não funcionará.")
        print("   Configure a variável MAIL_PASSWORD no .env")
    
    if not MAIL_RECIPIENTS:
        print("AVISO: Nenhum destinatário configurado para relatórios.")
    else:
        print(f"Email configurado. Destinatários: {', '.join(MAIL_RECIPIENTS)}")

# =============================================================================
# SCHEDULER DIÁRIO 18:00
# =============================================================================
_scheduler = None

def start_scheduler_once():
    from pytz import timezone
    global _scheduler
    
    if getattr(app, "_scheduler_started", False):
        return
    
    tz = timezone("America/Sao_Paulo")
    _scheduler = BackgroundScheduler(timezone=tz)
    
    def job_relatorio():
        with app.app_context():
            try:
                ok, msg = enviar_relatorio_email(MAIL_RECIPIENTS)
                print(f"Relatório automático: {msg}")
            except Exception as e:
                print(f"Erro no relatório automático: {e}")
    
    def job_sincronizacao_oracle():
        """Job diário de sincronização com Oracle"""
        with app.app_context():
            try:
                from sincronizacao_automatica import sincronizacao_automatica_diaria
                
                print("🔄 Iniciando sincronização automática com Oracle...")
                # Usar a função completa de sincronização que já mapeia consultores
                sincronizacao_automatica_diaria()
                print("✅ Sincronização automática concluída com sucesso!")
                
            except Exception as e:
                print(f"❌ Erro na sincronização Oracle: {e}")
                import traceback
                traceback.print_exc()
    
    _scheduler.add_job(
        job_relatorio,
        trigger='cron',
        day_of_week='mon-fri',
        hour=18,
        minute=0,
        id='relatorio_diario',
        replace_existing=True
    )
    
    # Adicionar sincronização Oracle - todo dia às 07:20
    _scheduler.add_job(
        job_sincronizacao_oracle,
        trigger='cron',
        hour=7,
        minute=20,
        id='sincronizacao_oracle_diaria',
        replace_existing=True
    )
    _scheduler.start()
    app._scheduler_started = True
    print("Scheduler configurado: envio diário às 18:00 (America/Sao_Paulo)")

# =============================================================================
# ROTA DE TESTE PARA DEBUG DE FILTROS ORACLE
# =============================================================================
@app.route('/test-filtros-oracle')
@login_required
def test_filtros_oracle():
    """Rota de teste para verificar filtros Oracle"""
    if current_user.tipo != 'supervisor':
        return "Acesso apenas para supervisores", 403
    
    # Testar filtros manualmente
    periodo_oracle = request.args.get('periodo_oracle')
    conceito_filtro = request.args.get('conceito_filtro')
    consultor_filtro = request.args.get('consultor_filtro')
    
    # Query base
    q = Cliente.query.filter(
        Cliente.cd_cliente_oracle.isnot(None),
        Cliente.ativo == True
    )
    
    # Aplicar filtros
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
    
    # Retornar resultado em HTML simples
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
    
    for c in clientes[:10]:  # Limitar a 10 para não sobrecarregar
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

# =============================================================================
# ROTA DE SINCRONIZAÇÃO MANUAL ORACLE
# =============================================================================
@app.route('/sincronizar-oracle', methods=['POST'])
@login_required
def sincronizar_oracle_manual():
    """Sincronização manual dos clientes Oracle"""
    if current_user.tipo != 'supervisor':
        return jsonify({"ok": False, "mensagem": "Acesso permitido apenas para supervisores"}), 403
    
    try:
        from sincronizacao_automatica import sincronizacao_automatica_diaria
        
        # Executar sincronização em thread separada para não bloquear
        import threading
        def sincronizar_background():
            try:
                sincronizacao_automatica_diaria()
            except Exception as e:
                print(f"Erro na sincronização background: {e}")
        
        thread = threading.Thread(target=sincronizar_background)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "ok": True, 
            "mensagem": "Sincronização iniciada com sucesso! Aguarde alguns minutos para ver os resultados."
        })
        
    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Erro ao iniciar sincronização: {str(e)}"}), 500

# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    from waitress import serve

    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        start_scheduler_once()

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"

    print(f"Servidor de produção iniciado, Controle de Ligações em http://{host}:{port}")
    serve(app, host=host, port=port, threads=32)