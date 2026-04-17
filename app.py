import email
import logging
import os
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

from flask import Flask, Response, abort, current_app, flash, redirect, render_template, request, send_file, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_migrate import Migrate
import click
import markdown2
from functools import wraps
from xhtml2pdf import pisa
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.exceptions import RequestEntityTooLarge
from htmldocx import HtmlToDocx
from flask_wtf import FlaskForm, CSRFProtect
from flask_wtf.csrf import CSRFError, generate_csrf
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, TextAreaField, IntegerField, SubmitField, FieldList, Form, FormField, DateField, BooleanField, SelectField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, URL
from sqlalchemy.orm import joinedload
from PIL import Image, UnidentifiedImageError


#função Fábrica de Decoradores de login
def role_required(role):
    """
    Restricts access to users with a specific role.
    e.g. @role_required('admin')
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                log_security_event("unauthenticated_access_attempt", level=logging.WARNING, path=request.path)
                return abort(401) 
            if current_user.role != role:
                log_security_event(
                    "authorization_denied",
                    level=logging.WARNING,
                    path=request.path,
                    required_role=role,
                    current_role=getattr(current_user, "role", "unknown"),
                    user_id=getattr(current_user, "id", None),
                )
                return abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

session_minutes = int(os.environ.get("SESSION_MINUTES", "30"))
max_upload_mb = int(os.environ.get("MAX_UPLOAD_MB", "8"))
force_https = os.environ.get("FORCE_HTTPS", "0") == "1"
generated_secret_key = secrets.token_hex(32)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', generated_secret_key)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=session_minutes)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = force_https
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_SECURE'] = force_https
app.config['SESSION_REFRESH_EACH_REQUEST'] = True
app.config['MAX_CONTENT_LENGTH'] = max_upload_mb * 1024 * 1024
app.config['WTF_CSRF_TIME_LIMIT'] = session_minutes * 60
app.config['FORCE_HTTPS'] = force_https
db = SQLAlchemy(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'strong'

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
FAILED_LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILED_LOGINS = 5
FAILED_LOGIN_ATTEMPTS = defaultdict(list)

security_logger = logging.getLogger("security")
if not security_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s in %(name)s: %(message)s'))
    security_logger.addHandler(handler)
security_logger.setLevel(logging.INFO)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def log_security_event(event, level=logging.INFO, **details):
    safe_details = ", ".join(f"{key}={value}" for key, value in details.items() if value not in (None, ""))
    security_logger.log(level, "%s | ip=%s%s", event, get_client_ip(), f", {safe_details}" if safe_details else "")


def sanitize_text(value, max_length=None, multiline=False):
    value = (value or "").strip()
    value = value.replace("\x00", "")
    value = re.sub(r"[<>]", "", value)
    if multiline:
        value = re.sub(r"\r\n?", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
    else:
        value = re.sub(r"\s+", " ", value)
    if max_length:
        value = value[:max_length]
    return value


def sanitize_username(value, field_name="usuário"):
    value = sanitize_text(value, max_length=50)
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,50}", value):
        raise ValueError(f"{field_name.capitalize()} inválido.")
    return value


def validate_password_strength(password, field_name="senha"):
    if len(password or "") < 8:
        raise ValueError(f"A {field_name} deve ter pelo menos 8 caracteres.")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError(f"A {field_name} deve conter letras e números.")
    return password


def get_form_value(name, required=False, max_length=None, multiline=False):
    value = sanitize_text(request.form.get(name, ""), max_length=max_length, multiline=multiline)
    if required and not value:
        raise ValueError(f"O campo '{name}' é obrigatório.")
    return value


def get_form_int(name, required=False, minimum=None, maximum=None, default=None):
    raw_value = request.form.get(name, None)
    if raw_value in (None, ""):
        if required:
            raise ValueError(f"O campo '{name}' é obrigatório.")
        return default
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"O campo '{name}' deve ser numérico.")
    if minimum is not None and value < minimum:
        raise ValueError(f"O campo '{name}' deve ser maior ou igual a {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"O campo '{name}' deve ser menor ou igual a {maximum}.")
    return value


def get_form_decimal(name, required=False, default=None):
    raw_value = request.form.get(name, None)
    if raw_value in (None, ""):
        if required:
            raise ValueError(f"O campo '{name}' é obrigatório.")
        return default
    normalized = sanitize_text(str(raw_value)).replace(".", "").replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        raise ValueError(f"O campo '{name}' deve conter um valor válido.")
    if value < 0:
        raise ValueError(f"O campo '{name}' não pode ser negativo.")
    return value


def get_form_date(name):
    raw_value = sanitize_text(request.form.get(name, ""))
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"O campo '{name}' deve estar no formato YYYY-MM-DD.")


def get_selected_ids(name):
    values = request.form.getlist(name)
    ids = []
    for value in values:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def escape_like(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def purge_old_failed_logins(key):
    cutoff = datetime.utcnow() - FAILED_LOGIN_WINDOW
    FAILED_LOGIN_ATTEMPTS[key] = [attempt for attempt in FAILED_LOGIN_ATTEMPTS[key] if attempt > cutoff]


def is_login_rate_limited(username, audience):
    key = f"{audience}:{sanitize_text(username, max_length=50).lower()}:{get_client_ip()}"
    purge_old_failed_logins(key)
    return len(FAILED_LOGIN_ATTEMPTS[key]) >= MAX_FAILED_LOGINS


def register_failed_login(username, audience):
    key = f"{audience}:{sanitize_text(username, max_length=50).lower()}:{get_client_ip()}"
    purge_old_failed_logins(key)
    FAILED_LOGIN_ATTEMPTS[key].append(datetime.utcnow())
    level = logging.WARNING if len(FAILED_LOGIN_ATTEMPTS[key]) < MAX_FAILED_LOGINS else logging.ERROR
    log_security_event("login_failed", level=level, username=username, audience=audience, attempts=len(FAILED_LOGIN_ATTEMPTS[key]))


def clear_failed_logins(username, audience):
    key = f"{audience}:{sanitize_text(username, max_length=50).lower()}:{get_client_ip()}"
    FAILED_LOGIN_ATTEMPTS.pop(key, None)


def validate_uploaded_image(file_storage):
    if not file_storage or file_storage.filename == "":
        raise ValueError("Selecione uma imagem válida.")
    if not allowed_file(file_storage.filename):
        raise ValueError("Formato de imagem não permitido.")
    if not (file_storage.mimetype or "").startswith("image/"):
        raise ValueError("Tipo de arquivo inválido.")

    original_position = file_storage.stream.tell()
    try:
        image = Image.open(file_storage.stream)
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("O arquivo enviado não é uma imagem válida.")
    finally:
        file_storage.stream.seek(original_position)


def save_uploaded_image(file_storage):
    validate_uploaded_image(file_storage)
    extension = secure_filename(file_storage.filename).rsplit('.', 1)[1].lower()
    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(8)}.{extension}"
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    file_storage.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    return filename


@app.before_request
def apply_request_security():
    if app.config.get("FORCE_HTTPS"):
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "http")
        if not request.is_secure and forwarded_proto != "https":
            secure_url = request.url.replace("http://", "https://", 1)
            return redirect(secure_url, code=301)

    if current_user.is_authenticated:
        session.permanent = True
        session.modified = True


@app.after_request
def set_security_headers(response):
    csp = (
        "default-src 'self'; "
        "img-src 'self' data: https://cdn.jsdelivr.net https://wa.me; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "connect-src 'self' https://viacep.com.br; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    log_security_event("csrf_validation_failed", level=logging.WARNING, reason=error.description, path=request.path)
    flash("Sua sessão de segurança expirou. Tente novamente.", "warning")
    return redirect(request.referrer or url_for("index"))


@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(_error):
    log_security_event("request_too_large", level=logging.WARNING, path=request.path)
    flash("O arquivo enviado excede o limite permitido.", "danger")
    return redirect(request.referrer or url_for("index"))

#Criação comando pra criar usuario master
@app.cli.command("create-user")  # Define o nome do comando no terminal
@click.argument("username")      # Define o primeiro argumento que o comando espera
@click.argument("password")      # Define o segundo argumento
def create_user(username, password):
    try:
        username = sanitize_username(username)
        validate_password_strength(password)
    except ValueError as error:
        print(f"Erro: {error}")
        return
    user = Usuario.query.filter_by(username=username).first()
    if user:
        print("Erro: Usuário já existe!")
        return
    else:
        password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        novo_usuario = Usuario(username = username, password_hash = password_hash)
        db.session.add(novo_usuario)
        db.session.commit()
        print(f"Usuário {username} criado com sucesso!")


#Criação comando pra deletar usuario master
@app.cli.command("delete-user")  # Define o nome do comando no terminal
@click.argument("username")      # Define o primeiro argumento que o comando espera
def delete_user(username):
    usuario_a_deletar = Usuario.query.filter_by(username=username).first()
    if usuario_a_deletar:
        db.session.delete(usuario_a_deletar)
        db.session.commit()
        print(f"Usuário {username} foi deletado com sucesso!")
    else:
        print(f"Usuario {username} não encontrado!")


@app.route('/sw.js')
def service_worker():
    resposta = current_app.send_static_file('js/sw.js')
    resposta.mimetype = 'application/javascript'
    return resposta

@app.route('/offline.html')
def offline():
    return render_template('offline.html')

# Tabela "Ponte" (Muitos-para-Muitos)
cliente_impressora_association = db.Table('cliente_impressora', db.metadata,
    db.Column('cliente_id', db.Integer, db.ForeignKey('cliente.id'), primary_key=True),
    db.Column('impressora_id', db.Integer, db.ForeignKey('impressora.id'), primary_key=True)
)
#Classes
class Cliente(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key = True)
    ordens_servico = db.relationship('OrdemServico', back_populates='cliente', cascade="all, delete-orphan")
    orcamento = db.relationship('Orcamento', back_populates='cliente', cascade="all, delete-orphan")
    nome = db.Column(db.String(100), nullable=False)
    #login usuario
    username_cliente = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='cliente')
     #Contato
    telefone_celular = db.Column(db.String(20), nullable = False)
    telefone_auxiliar = db.Column(db.String(20))
    #Tipo
    tipo_cliente = db.Column(db.String(20))
    cpf = db.Column(db.String(20))
    cnpj = db.Column(db.String(20))
    #Endereço
    cep = db.Column(db.String(10))
    logradouro = db.Column(db.String(150))
    numero = db.Column(db.String(10))
    complemento = db.Column(db.String(100))
    bairro = db.Column(db.String(100))
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(10))
    #Outros
    anotacoes = db.Column(db.Text)

    impressoras_permitidas = db.relationship(
        'Impressora', 
        secondary=cliente_impressora_association, 
        lazy='dynamic', 
        backref=db.backref('clientes_com_acesso', lazy=True)
    )
    senha_plana_temporaria = db.Column(db.String(100), nullable=True)

    def get_id(self):
        return f"cliente-{self.id}"
    
    @property
    def whatsapp_limpo(self):
        if self.telefone_celular:
            # Importante: o 're' já está importado no topo do seu app.py
            return re.sub(r'\D', '', self.telefone_celular)
        return ""

class OrdemServico(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    cliente = db.relationship('Cliente', back_populates='ordens_servico')
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable = False)

    # --- Campos de Numeração ---
    numero_sequencial = db.Column(db.Integer, nullable = True)
    ano = db.Column(db.Integer, nullable = True)
    
    # --- Campos do Equipamento ---
    equipamento = db.Column(db.String(150), nullable = False)
    marca = db.Column(db.String(100))
    modelo = db.Column(db.String(100))
    
    # --- NOVOS CAMPOS ADICIONADOS ---
    numero_de_serie = db.Column(db.String(100)) # <--- NOVO
    tecnico_responsavel = db.Column(db.String(50)) # <--- NOVO

    # --- Campos de Descrição do Problema/Serviço ---
    defeito = db.Column(db.Text, nullable = False)
    problema_constatado = db.Column(db.Text) # <--- NOVO
    servico_executado = db.Column(db.Text) # <--- NOVO

    # --- NOVOS CAMPOS DE OBSERVAÇÕES ---
    observacoes_cliente = db.Column(db.Text) # <--- NOVO
    observacoes_internas = db.Column(db.Text) # <--- NOVO

    # --- Campos de Controle ---
    status = db.Column(db.String(50), nullable = False)
    data_de_criacao = db.Column(db.DateTime, nullable = False, default = datetime.now)
    orcamento_id = db.Column(db.Integer, db.ForeignKey('orcamento.id'), nullable=True, unique=True)
    
    # --- Relacionamentos ---
    itens_servico = db.relationship('ItemServico', backref='ordem_servico', lazy=True, cascade="all, delete-orphan")
    itens_peca = db.relationship('ItemPeca', backref='ordem_servico', lazy=True, cascade="all, delete-orphan")
    fotos = db.relationship('Foto', backref='ordem_servico', lazy=True, cascade="all, delete-orphan")

    @property
    def valor_calculado(self):
        total_servicos = sum(item.quantidade * item.preco_cobrado for item in self.itens_servico)
        total_pecas = sum(item.quantidade * item.preco_cobrado for item in self.itens_peca)
        return total_servicos + total_pecas
    
    @property
    def numero_formatado(self):
        return f"{self.numero_sequencial:03d}-{self.ano}"

class Orcamento(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable = False)
    cliente = db.relationship('Cliente', back_populates='orcamento')
    numero_orcamento = db.Column(db.Integer, nullable = True)
    ano = db.Column(db.Integer, nullable = True)
    marca = db.Column(db.String(100))
    modelo = db.Column(db.String(100))
    equipamento = db.Column(db.String(150), nullable = False)
    numero_de_serie = db.Column(db.String(100))
    validade_do_orcamento = db.Column(db.String(10))
    problema_informado = db.Column(db.Text, nullable = False)
    problema_constatado = db.Column(db.Text, nullable = False)
    #servico_executado = db.Column(db.Text, nullable = False)
    observacoes_cliente = db.Column(db.Text)
    observacoes_internas = db.Column(db.Text)
    status = db.Column(db.String(50), nullable = False)
    tecnico_responsavel = db.Column(db.String(50))
    data_de_criacao = db.Column(db.Date, nullable = False, default = date.today)
    itens_servico = db.relationship('ItemOrcamentoServico', backref='orcamento', lazy=True, cascade="all, delete-orphan")
    itens_peca = db.relationship('ItemOrcamentoPeca', backref='orcamento', lazy=True, cascade="all, delete-orphan")
    fotos = db.relationship('Foto', backref='orcamento', lazy=True, cascade="all, delete-orphan")

    @property
    def numero_formatado(self):
        if self.numero_orcamento and self.ano:
            return f"{self.numero_orcamento:03d}-{self.ano}"
        return "Sem número"
    
    @property
    def valor_total(self):
        total_servicos = sum(item.quantidade * item.preco_cobrado for item in self.itens_servico)
        total_pecas = sum(item.quantidade * item.preco_cobrado for item in self.itens_peca)
        return total_servicos + total_pecas


class ItemOrcamentoServico(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    quantidade = db.Column(db.Integer, nullable = False)
    preco_cobrado = db.Column(db.Float, nullable = False)
    orcamento_id = db.Column(db.Integer, db.ForeignKey('orcamento.id'))
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'))
    servico = db.relationship('Servico', backref='itens_orcamento_servico')

class ItemOrcamentoPeca(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    quantidade = db.Column(db.Integer, nullable = False)
    preco_cobrado = db.Column(db.Float, nullable = False)
    orcamento_id = db.Column(db.Integer, db.ForeignKey('orcamento.id'))
    peca_id = db.Column(db.Integer, db.ForeignKey('peca.id'))
    peca = db.relationship('Peca', backref='itens_orcamento_peca')

class Servico(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    descricao_servico = db.Column(db.String(150), nullable = False)
    detalhes_opcional = db.Column(db.Text)
    preco_unitario = db.Column(db.Float, nullable = False)
    unidade_medida = db.Column(db.String(20))
    __table_args__ = (db.UniqueConstraint('descricao_servico', name='uq_servico_descricao'),)
    itens_servico = db.relationship('ItemServico', backref='servico', lazy=True)

class Peca(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    nome_peca = db.Column(db.String(100), nullable = False)
    detalhes_opcional = db.Column(db.Text)
    codigo_interno = db.Column(db.String(100))
    preco_unitario = db.Column(db.Float, nullable = False)
    unidade_medida = db.Column(db.String(20))
    __table_args__ = (db.UniqueConstraint('nome_peca', name='uq_peca_nome'),)
    itens_peca = db.relationship('ItemPeca', backref='peca', lazy=True)

class ItemServico(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    quantidade = db.Column(db.Integer, nullable = False)
    preco_cobrado = db.Column(db.Float, nullable = False)
    ordem_servico_id = db.Column(db.Integer, db.ForeignKey('ordem_servico.id'))
    servico_id = db.Column(db.Integer, db.ForeignKey('servico.id'))

class ItemPeca(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    quantidade = db.Column(db.Integer, nullable = False)
    preco_cobrado = db.Column(db.Float, nullable = False)
    ordem_servico_id = db.Column(db.Integer, db.ForeignKey('ordem_servico.id'))
    peca_id = db.Column(db.Integer, db.ForeignKey('peca.id'))

class Usuario(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key = True)
    username = db.Column(db.String(80), nullable = False, unique = True)
    password_hash = db.Column(db.String(128), nullable = False)
    role = db.Column(db.String(20), nullable=False, default='funcionario')

    def get_id(self):
        return f"usuario-{self.id}"

class Foto(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    nome_arquivo = db.Column(db.String(20))
    legenda = db.Column(db.String(150))
    ordem_servico_id = db.Column(db.Integer, db.ForeignKey('ordem_servico.id'), nullable=True) 
    orcamento_id = db.Column(db.Integer, db.ForeignKey('orcamento.id'), nullable=True) 

class Curriculo(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    nome = db.Column(db.String(120))
    estado_civil = db.Column(db.String(30))
    idade = db.Column(db.Integer)
    endereco = db.Column(db.Text)
    telefone_principal = db.Column(db.String(20))
    email = db.Column(db.String(40))
    objetivo = db.Column(db.Text)
    data_criacao = db.Column(db.Date)
    formacoes = db.relationship('FormacaoAcademica', backref = 'curriculo')
    experiencias = db.relationship('ExperienciaProfissional', backref = 'curriculo')
    cursos = db.relationship('Curso', backref='curriculo', cascade="all, delete-orphan")

class Contrato(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # Dados do locador
    locador_nome = db.Column(db.String(120), nullable=False)
    locador_rg = db.Column(db.String(20))
    locador_cpf = db.Column(db.String(20))
    locador_endereco = db.Column(db.String(200))

    # Dados do locatário
    locatario_nome = db.Column(db.String(120), nullable=False)
    locatario_rg = db.Column(db.String(20))
    locatario_cpf = db.Column(db.String(20))
    locatario_endereco = db.Column(db.String(200))

    # Dados do imóvel e contrato
    endereco_imovel = db.Column(db.String(200), nullable=False)
    finalidade = db.Column(db.String(100), default="residenciais")
    prazo_meses = db.Column(db.Integer, default=12)
    data_inicio = db.Column(db.Date)
    data_fim = db.Column(db.Date)
    valor_aluguel = db.Column(db.Float)
    dia_pagamento = db.Column(db.Integer, default=10)
    indice_reajuste = db.Column(db.String(50), default="IGP-M")
    multa_percentual = db.Column(db.Integer, default=5)
    juros_percentual = db.Column(db.Integer, default=1)

    cidade_foro = db.Column(db.String(100), default="Ibirité")
    cidade = db.Column(db.String(100), default="Ibirité - MG")
    data_assinatura = db.Column(db.Date)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)


    def __repr__(self):
        return f"<Contrato {self.id} - {self.locatario_nome}>"
    
class ContratoForm(FlaskForm):
    # Locador
    locador_nome = StringField("Nome do Locador", validators=[DataRequired()])
    locador_rg = StringField("RG do Locador")
    locador_cpf = StringField("CPF do Locador")
    locador_endereco = StringField("Endereço do Locador")

    # Locatário
    locatario_nome = StringField("Nome do Locatário", validators=[DataRequired()])
    locatario_rg = StringField("RG do Locatário")
    locatario_cpf = StringField("CPF do Locatário")
    locatario_endereco = StringField("Endereço do Locatário")

    # Imóvel e contrato
    endereco_imovel = StringField("Endereço do Imóvel", validators=[DataRequired()])
    finalidade = StringField("Finalidade", default="residenciais")
    prazo_meses = IntegerField("Prazo (meses)", default=12)
    data_inicio = DateField("Data de Início")
    data_fim = DateField("Data de Término")
    valor_aluguel = StringField("Valor do Aluguel (R$)")
    dia_pagamento = IntegerField("Dia do Pagamento", default=10)
    indice_reajuste = StringField("Índice de Reajuste", default="IGP-M")
    multa_percentual = IntegerField("Multa (%)", default=5)
    juros_percentual = IntegerField("Juros (%)", default=1)

    cidade_foro = StringField("Foro", default="Ibirité")
    cidade = StringField("Cidade", default="Ibirité - MG")
    data_assinatura = DateField("Data de Assinatura", default=date.today())

    submit = SubmitField("Salvar Contrato")

class FormacaoAcademica(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    descricao = db.Column(db.Text)
    curriculo_id = db.Column(db.Integer, db.ForeignKey("curriculo.id"))

class Curso(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    descricao = db.Column(db.Text)
    curriculo_id = db.Column(db.Integer, db.ForeignKey("curriculo.id"))

class ExperienciaProfissional(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    empresa = db.Column(db.String(120))
    cargo = db.Column(db.String(120))
    data_admissao = db.Column(db.Date)
    data_demissao = db.Column(db.Date)
    desabilitar_datas = db.Column(db.Boolean)
    periodo = db.Column(db.Text)
    curriculo_id = db.Column(db.Integer, db.ForeignKey("curriculo.id"))

class CurriculoPasso1Form(FlaskForm):
    nome = StringField("Nome", validators=[DataRequired("Digite o nome")])
    estado_civil = StringField("Estado civil", validators=[Optional()])
    endereco = StringField("Endereço", validators=[Optional()])
    idade = IntegerField("Idade", validators=[Optional()])
    telefone_principal = StringField("Telefone principal", validators=[DataRequired("Precisa de telefone")])
    email = StringField("Email", validators=[Optional(), Email("Formato de email inválido")])
    submit = SubmitField("Avançar")

class CurriculoPasso2Form(FlaskForm):
    formacoes = FieldList(StringField("Formação", validators=[Optional()]), min_entries=1)
    cursos = FieldList(StringField("Curso", validators=[Optional()]), min_entries=1)
    submit = SubmitField("Avançar")

class ExperienciaForm(Form):
    empresa = StringField("Empresa", validators=[DataRequired("Digite o nome da empresa")])
    cargo = StringField("Cargo", validators=[DataRequired("Digite o cargo")])
    data_admissao = DateField("Data de admissão", format='%Y-%m-%d', validators=[Optional()])
    data_demissao = DateField("Data de demissão", format='%Y-%m-%d', validators=[Optional()],)
    desabilitar_datas = BooleanField("Desabilitar datas", validators=[Optional()])
    periodo = StringField("Período", validators=[Optional()])


class CurriculoPasso3Form(FlaskForm):
    experiencias = FieldList(FormField(ExperienciaForm), min_entries=1)
    submit = SubmitField("Avançar")

class CurriculoPasso4Form(FlaskForm):
    objetivo = TextAreaField("Objetivo", validators=[Optional()])
    submit = SubmitField("Finalizar")

class Configuracao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome_loja = db.Column(db.String(100))
    cnpj = db.Column(db.String(20))
    endereco = db.Column(db.String(200))
    telefone = db.Column(db.String(20))
    logomarca = db.Column(db.String(100))  # Armazenará o nome do arquivo da logo
    texto_garantia = db.Column(db.Text)
    email_contato = db.Column(db.String(100))
    site = db.Column(db.String(100))

class ConfiguracaoForm(FlaskForm):
    nome_loja = StringField('Nome da Loja', validators=[Optional(), Length(max=100)])
    cnpj = StringField('CNPJ', validators=[Optional(), Length(max=20)])
    endereco = StringField('Endereço Completo', validators=[Optional(), Length(max=200)])
    telefone = StringField('Telefone Comercial', validators=[Optional(), Length(max=20)])
    logomarca = FileField('Logomarca', validators=[FileAllowed(['jpg', 'png'], 'Apenas imagens!')])
    texto_garantia = TextAreaField('Texto Padrão da Garantia', validators=[Optional(), Length(max=4000)], render_kw={"rows": 10})
    email_contato = StringField('Email de Contato', validators=[Optional(), Email(), Length(max=100)])
    site = StringField('Site', validators=[Optional(), URL(require_tld=False), Length(max=100)])
    submit = SubmitField('Salvar Configurações')


# Tabela 1: A "Categoria" (A Impressora)
class Impressora(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    modelo = db.Column(db.String(100), nullable=False, unique=True)
    descricao = db.Column(db.Text)

    # O "relacionamento" que conecta esta impressora aos seus arquivos
    recursos = db.relationship('RecursoImpressora', backref='impressora', lazy=True, cascade="all, delete-orphan")

# Tabela 2: Os "Links" (Os Resets e Drivers)
class RecursoImpressora(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # A Chave Estrangeira que "amarra" este link a uma impressora
    impressora_id = db.Column(db.Integer, db.ForeignKey('impressora.id'), nullable=False)

    # Campos para descrever o link, como você sugeriu
    tipo = db.Column(db.String(50), nullable=False) # Ex: "Reset", "Driver", "Scanner"
    descricao = db.Column(db.Text) # Ex: "Driver de Impressora (Win 10/11 x64)"
    sistema_operacional = db.Column(db.String(50)) # Ex: "Windows 10/11", "Windows 7", "macOS"
    link_download = db.Column(db.Text, nullable=False)

class ImpressoraForm(FlaskForm):
    modelo = StringField('Nome da Categoria', validators=[DataRequired(), Length(max=100)])
    descricao = TextAreaField('Descrição da Categoria (Opcional)', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Salvar')

class RecursoForm(FlaskForm):
    # Usamos um SelectField para o 'tipo' para padronizar a entrada
    tipo = SelectField('Tipo de Recurso', 
                       choices=[
                           ('Reset', 'Reset'), 
                           ('Driver', 'Driver'), 
                           ('Scanner', 'Scanner'), 
                           ('Manual', 'Manual'),
                           ('Outro', 'Outro')
                       ], 
                       validators=[DataRequired()])

    descricao = StringField('Descrição', 
                            validators=[DataRequired(), Length(max=255)], 
                            render_kw={"placeholder": "Ex: Reset para Windows 10/11"})

    sistema_operacional = StringField('Sistema Operacional', 
                                      validators=[Optional(), Length(max=50)], 
                                      render_kw={"placeholder": "Ex: Windows 10/11 x64"})

    link_download = TextAreaField('Link para Download', 
                                  validators=[DataRequired(), URL(require_tld=False), Length(max=2000)], 
                                  render_kw={"rows": 3, "placeholder": "Cole o link completo (Google Drive, Mega, etc.)"})

    submit = SubmitField('Adicionar Recurso')

class ReciboSimplesForm(FlaskForm):
    valor = StringField("Valor (R$)", validators=[DataRequired()])
    pagador = StringField("Recebemos de", validators=[DataRequired()])
    document_pagador = StringField("CPF/CNPJ (Opcional)") # No HTML usei document_pagador
    referente_a = TextAreaField("Referente a", validators=[DataRequired()])
    cidade = StringField("Cidade", default="Ibirité - MG")
    data_emissao = DateField("Data", default=date.today)
    submit = SubmitField("Gerar Recibo em PDF")

#Funções principais
@app.context_processor
def inject_now():
    return {'now': datetime.now}


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': generate_csrf}

@app.context_processor
def inject_config():
    # Busca a primeira (e única) linha de configuração do banco
    config = Configuracao.query.first()

    # Se nenhuma configuração foi salva ainda, retorna um dicionário vazio
    # para evitar erros nos templates.
    if not config:
        return {} 

    # Retorna um dicionário com a variável que estará disponível nos templates
    return dict(config=config)

@login_manager.user_loader
def load_user(user_id_string): # O nome agora reflete que é uma string
    try:
        # Separa o tipo do número. ex: "cliente-1" vira ["cliente", "1"]
        user_type, user_id = user_id_string.split('-')
        user_id = int(user_id)
    except ValueError:
        return None # Se o ID não estiver no formato esperado, retorna None

    # Agora, usa o tipo para saber em qual tabela procurar
    if user_type == 'cliente':
        return Cliente.query.get(user_id)
    elif user_type == 'usuario':
        return Usuario.query.get(user_id)
    
    return None

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    user_identifier = current_user.get_id()
    logout_user()
    session.clear()
    log_security_event("logout_success", user_id=user_identifier, path=request.path)
    flash("Logoff efetuado!", "danger")
    return redirect(url_for("login"))

@app.route("/logout_cliente", methods=["POST"])
@login_required
def logout_cliente():
    user_identifier = current_user.get_id()
    logout_user()
    session.clear()
    log_security_event("logout_success", user_id=user_identifier, path=request.path)
    flash("Logoff efetuado!", "danger")
    return redirect(url_for("index"))

@app.route("/login", methods = ("GET", "POST"))
def login():
    if request.method == "POST":
        try:
            username = sanitize_username(request.form.get("username"), "usu?rio")
            password = request.form.get("password", "")
        except ValueError as error:
            flash(str(error), "danger")
            register_failed_login(request.form.get("username", ""), "funcionario")
            return redirect(url_for('login'))

        if is_login_rate_limited(username, "funcionario"):
            log_security_event("login_rate_limited", level=logging.ERROR, username=username, audience="funcionario")
            flash("Muitas tentativas seguidas. Aguarde alguns minutos antes de tentar novamente.", "warning")
            return redirect(url_for('login'))

        user = Usuario.query.filter_by(username=username).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            session.clear()
            login_user(user, remember=False, fresh=True)
            session.permanent = True
            clear_failed_logins(username, "funcionario")
            log_security_event("login_success", username=username, audience="funcionario", user_id=user.id)
            return redirect(url_for('home'))

        register_failed_login(username, "funcionario")
        flash("Usu?rio ou senha incorretos", "danger")
        return redirect(url_for('login'))

    return render_template('login.html')

@app.route("/login_cliente", methods = ("GET", "POST"))
def login_cliente():
    if request.method == "POST":
        try:
            username = sanitize_username(request.form.get("username"), "usu?rio")
            password = request.form.get("password", "")
        except ValueError as error:
            flash(str(error), "danger")
            register_failed_login(request.form.get("username", ""), "cliente")
            return redirect(url_for('login_cliente'))

        if is_login_rate_limited(username, "cliente"):
            log_security_event("login_rate_limited", level=logging.ERROR, username=username, audience="cliente")
            flash("Muitas tentativas seguidas. Aguarde alguns minutos antes de tentar novamente.", "warning")
            return redirect(url_for('login_cliente'))

        user = Cliente.query.filter_by(username_cliente=username).first()
        if user and bcrypt.check_password_hash(user.password_hash, password):
            session.clear()
            login_user(user, remember=False, fresh=True)
            session.permanent = True
            clear_failed_logins(username, "cliente")
            log_security_event("login_success", username=username, audience="cliente", user_id=user.id)
            return redirect(url_for('dashboard_cliente'))

        register_failed_login(username, "cliente")
        flash("Usu?rio ou senha incorretos", "danger")
        return redirect(url_for('login_cliente'))

    return render_template('login_cliente.html')

@app.route("/cliente/dashboard")
@role_required('cliente')
def dashboard_cliente():
    return render_template("dashboard_cliente.html")

@app.route("/cliente/os/<int:id>")
@role_required("cliente")
def ver_os_cliente(id):
    ordem_servico = OrdemServico.query.get_or_404(id)
    if ordem_servico.cliente_id == current_user.id:
        return render_template("ver_os_cliente.html", ordem_servico=ordem_servico)
    log_security_event("authorization_denied", level=logging.WARNING, path=request.path, user_id=current_user.id, target_os=id)
    abort(403)
 
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dashboard")
@role_required('funcionario')
def home():
    total_clientes = Cliente.query.count()
    ordens_abertas = OrdemServico.query.filter(OrdemServico.status != "Concluído").count()
    ordens_concluidas = OrdemServico.query.filter(OrdemServico.status == "Concluído").count()
    orcamento_aberto = Orcamento.query.filter(Orcamento.status != "Aprovado" or Orcamento.status != "Convertido em OS").count()
    orcamento_concluido = Orcamento.query.filter(Orcamento.status == "Aprovado" or Orcamento.status == "Convertido em OS").count()

    total_curriculos = Curriculo.query.count()
    total_contratos = Contrato.query.count()
    total_links = Impressora.query.count()

    ordens_pagas = OrdemServico.query.filter_by(status='Concluído').all()
    # Somamos os valores usando a @property via Python
    faturamento_total = sum(os.valor_calculado for os in ordens_pagas) if ordens_pagas else 0.0

    ultimas_os = OrdemServico.query.order_by(OrdemServico.data_de_criacao.desc()).limit(5).all()

    return render_template(
        "home.html",
        total_clientes=total_clientes,
        ordens_abertas=ordens_abertas,
        ordens_concluidas=ordens_concluidas,
        orcamento_aberto = orcamento_aberto,
        orcamento_concluido = orcamento_concluido,
        total_curriculos = total_curriculos,
        total_contratos = total_contratos,
        total_links = total_links,
        ultimas_os = ultimas_os,
        faturamento_total = faturamento_total
        )

@app.route("/clientes/cadastrar", methods=["GET", "POST"])
@role_required('funcionario')
def cadastrar_cliente():
    if request.method == "POST":
        try:
            username_cliente = sanitize_username(request.form.get("username_cliente"), "usu?rio do cliente")
            password_cliente = validate_password_strength(request.form.get("password_cliente", ""), "senha do cliente")
            if Cliente.query.filter_by(username_cliente=username_cliente).first():
                raise ValueError("J? existe um cliente com esse usu?rio.")

            novo_cliente = Cliente(
                nome=get_form_value("nome", required=True, max_length=100),
                username_cliente=username_cliente,
                password_hash=bcrypt.generate_password_hash(password_cliente).decode('utf-8'),
                senha_plana_temporaria=None,
                telefone_celular=get_form_value("telefone_celular", required=True, max_length=20),
                telefone_auxiliar=get_form_value("telefone_auxiliar", max_length=20),
                cpf=get_form_value("cpf", max_length=20),
                cnpj=get_form_value("cnpj", max_length=20),
                cep=get_form_value("cep", max_length=10),
                logradouro=get_form_value("logradouro", max_length=150),
                numero=get_form_value("numero", max_length=10),
                complemento=get_form_value("complemento", max_length=100),
                bairro=get_form_value("bairro", max_length=100),
                cidade=get_form_value("cidade", max_length=100),
                estado=get_form_value("estado", max_length=10),
                anotacoes=get_form_value("anotacoes", max_length=2000, multiline=True),
            )
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("cadastrar_cliente"))

        db.session.add(novo_cliente)
        db.session.commit()
        log_security_event("client_created", user_id=current_user.id, client_id=novo_cliente.id)
        flash("Cliente cadastrado com sucesso!", "success")
        return redirect(url_for("listar_clientes"))

    return render_template("cadastrar_cliente.html")

@app.route("/clientes/deletar/<int:id>", methods=["POST"])
@role_required('funcionario')
def deletar_cliente(id):
    cliente_a_deletar = Cliente.query.get_or_404(id)
    db.session.delete(cliente_a_deletar)
    db.session.commit()
    log_security_event("client_deleted", level=logging.WARNING, user_id=current_user.id, client_id=id)
    flash("Cliente apagado com sucesso!", "success")
    return redirect(url_for("listar_clientes"))

@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def editar_cliente(id):
    cliente_a_editar = Cliente.query.get_or_404(id)
    todas_impressoras = Impressora.query.options(joinedload(Impressora.clientes_com_acesso)).order_by(Impressora.modelo).all()

    if request.method == "POST":
        try:
            cliente_a_editar.nome = get_form_value("nome", required=True, max_length=100)
            cliente_a_editar.telefone_celular = get_form_value("telefone_celular", required=True, max_length=20)
            cliente_a_editar.telefone_auxiliar = get_form_value("telefone_auxiliar", max_length=20)
            cliente_a_editar.cpf = get_form_value("cpf", max_length=20)
            cliente_a_editar.cnpj = get_form_value("cnpj", max_length=20)
            cliente_a_editar.cep = get_form_value("cep", max_length=10)
            cliente_a_editar.logradouro = get_form_value("logradouro", max_length=150)
            cliente_a_editar.numero = get_form_value("numero", max_length=10)
            cliente_a_editar.complemento = get_form_value("complemento", max_length=100)
            cliente_a_editar.bairro = get_form_value("bairro", max_length=100)
            cliente_a_editar.cidade = get_form_value("cidade", max_length=100)
            cliente_a_editar.estado = get_form_value("estado", max_length=10)
            cliente_a_editar.anotacoes = get_form_value("anotacoes", max_length=2000, multiline=True)
            cliente_a_editar.impressoras_permitidas = []
            for impressora_id in get_selected_ids("impressora_permitida"):
                impressora = Impressora.query.get(impressora_id)
                if impressora:
                    cliente_a_editar.impressoras_permitidas.append(impressora)

            nova_senha = request.form.get('nova_senha')
            if nova_senha:
                validate_password_strength(nova_senha, "nova senha")
                cliente_a_editar.password_hash = bcrypt.generate_password_hash(nova_senha).decode('utf-8')
            cliente_a_editar.senha_plana_temporaria = None
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("editar_cliente", id=id))

        db.session.commit()
        log_security_event("client_updated", user_id=current_user.id, client_id=id)
        flash("Cliente editado com sucesso!", "success")
        return redirect(url_for("listar_clientes"))

    return render_template(
        "editar_cliente.html",
        cliente_a_editar=cliente_a_editar,
        todas_impressoras=todas_impressoras
    )

@app.route("/clientes", methods = ["GET"])
@role_required('funcionario')
def listar_clientes():
    page = request.args.get("page", 1, type=int)
    termo_busca = sanitize_text(request.args.get("termo_busca"), max_length=100)
    query_clientes = Cliente.query

    if termo_busca:
        termo_escapado = escape_like(termo_busca)
        query_clientes = query_clientes.filter(Cliente.nome.ilike(f"%{termo_escapado}%", escape='\\'))

    paginacao = query_clientes.order_by(Cliente.nome).paginate(page=page, per_page=15, error_out=False)
    clientes_da_pagina = paginacao.items

    return render_template(
        "listar_clientes.html",
         clientes=clientes_da_pagina,
         paginacao=paginacao,
         termo_busca=termo_busca
         )

@app.route("/cliente/<int:id>")
@role_required('funcionario')
def detalhes_cliente(id):
    cliente_a_detalhar = Cliente.query.get(id)
    ordens_servico = cliente_a_detalhar.ordens_servico
    orcamentos = cliente_a_detalhar.orcamento
    return render_template("detalhes_cliente.html", cliente_a_detalhar = cliente_a_detalhar, ordens_servico = ordens_servico, orcamentos=orcamentos)

@app.route("/cliente/<int:cliente_id>/os/cadastrar", methods = ["GET", "POST"])
@role_required('funcionario')
def cadastrar_os(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    if request.method == "POST":
        try:
            ano_atual = datetime.utcnow().year
            maior_numero_os_do_ano = db.session.query(func.max(OrdemServico.numero_sequencial)).filter_by(ano=ano_atual).scalar()
            novo_numero_sequencial = 1 if maior_numero_os_do_ano is None else maior_numero_os_do_ano + 1

            nova_os = OrdemServico(
                cliente_id=cliente_id,
                numero_sequencial=novo_numero_sequencial,
                ano=ano_atual,
                equipamento=get_form_value("equipamento", required=True, max_length=150),
                marca=get_form_value("marca", max_length=100),
                modelo=get_form_value("modelo", max_length=100),
                defeito=get_form_value("defeito", required=True, max_length=2000, multiline=True),
                status=get_form_value("status", required=True, max_length=50),
            )
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("cadastrar_os", cliente_id=cliente_id))

        db.session.add(nova_os)
        db.session.commit()
        log_security_event("service_order_created", user_id=current_user.id, os_id=nova_os.id, client_id=cliente_id)
        flash("OS cadastrado com sucesso!", "success")
        return redirect(url_for("detalhes_cliente", id=cliente_id))

    return render_template("cadastrar_os.html", cliente=cliente)

@app.route("/os/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def detalhes_os(id):
    ordem_servico = OrdemServico.query.get_or_404(id)
    lista_servicos = Servico.query.all()
    lista_pecas = Peca.query.all()

    if request.method == "POST":
        try:
            ordem_servico.tecnico_responsavel = get_form_value('tecnico_responsavel', max_length=50)
            ordem_servico.equipamento = get_form_value("equipamento", required=True, max_length=150)
            ordem_servico.marca = get_form_value("marca", max_length=100)
            ordem_servico.modelo = get_form_value("modelo", max_length=100)
            ordem_servico.numero_de_serie = get_form_value('numero_de_serie', max_length=100)
            ordem_servico.defeito = get_form_value("defeito", required=True, max_length=2000, multiline=True)
            ordem_servico.problema_constatado = get_form_value('problema_constatado', max_length=2000, multiline=True)
            ordem_servico.servico_executado = get_form_value('servico_executado', max_length=2000, multiline=True)
            ordem_servico.observacoes_cliente = get_form_value('observacoes_cliente', max_length=2000, multiline=True)
            ordem_servico.observacoes_internas = get_form_value('observacoes_internas', max_length=2000, multiline=True)
            ordem_servico.status = get_form_value("status", required=True, max_length=50)
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("detalhes_os", id=id))

        db.session.commit()
        log_security_event("service_order_updated", user_id=current_user.id, os_id=id)
        flash("Ordem de Servi?o salva com sucesso!", "success")
        return redirect(url_for("detalhes_os", id=ordem_servico.id))

    return render_template("detalhes_os.html", ordem_servico=ordem_servico, lista_servicos=lista_servicos, lista_pecas=lista_pecas)

@app.route("/os/deletar/<int:id>", methods=["POST"])
@role_required('funcionario')
def deletar_os(id):
    os_a_deletar = OrdemServico.query.get_or_404(id)
    id_do_cliente = os_a_deletar.cliente.id
    db.session.delete(os_a_deletar)
    db.session.commit()
    log_security_event("service_order_deleted", level=logging.WARNING, user_id=current_user.id, os_id=id)
    flash("OS apagada com sucesso!", "success")
    return redirect(url_for("detalhes_cliente", id=id_do_cliente))

@app.route("/servicos")
@role_required('funcionario')
def listar_servicos():
    page = request.args.get("page", 1, type=int)
    termo_busca = sanitize_text(request.args.get("termo_busca"), max_length=100)
    servico_query = Servico.query

    if termo_busca:
        termo_escapado = escape_like(termo_busca)
        servico_query = servico_query.filter(Servico.descricao_servico.ilike(f"%{termo_escapado}%", escape='\\'))

    paginacao = servico_query.order_by(Servico.descricao_servico).paginate(page=page, per_page=15, error_out=False)
    servicos_por_pagina = paginacao.items

    return render_template(
        "listar_servicos.html",
        servicos=servicos_por_pagina,
        paginacao=paginacao,
        termo_busca=termo_busca
    )

@app.route("/servicos/cadastrar", methods = ["GET", "POST"])
@role_required('funcionario')
def cadastrar_servico():
    if request.method == "POST":
        try:
            novo_servico = Servico(
                descricao_servico=get_form_value("descricao_servico", required=True, max_length=150),
                detalhes_opcional=get_form_value("detalhes_opcional", max_length=1000, multiline=True),
                unidade_medida=get_form_value("unidade_medida", max_length=50),
                preco_unitario=get_form_decimal("preco_unitario", default=0.0),
            )
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("cadastrar_servico"))

        db.session.add(novo_servico)
        db.session.commit()
        log_security_event("service_created", user_id=current_user.id, service_id=novo_servico.id)
        flash("Servi?o cadastrado com sucesso!", "success")
        return redirect(url_for("listar_servicos"))

    return render_template("cadastrar_servico.html")

@app.route("/servicos/editar/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def editar_servico(id):
    servico_a_editar = Servico.query.get_or_404(id)
    if request.method == "POST":
        try:
            servico_a_editar.descricao_servico = get_form_value("descricao_servico", required=True, max_length=150)
            servico_a_editar.detalhes_opcional = get_form_value("detalhes_opcional", max_length=1000, multiline=True)
            servico_a_editar.unidade_medida = get_form_value("unidade_medida", max_length=50)
            servico_a_editar.preco_unitario = get_form_decimal("preco_unitario", default=0.0)
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("editar_servico", id=id))

        db.session.commit()
        log_security_event("service_updated", user_id=current_user.id, service_id=id)
        flash("Servi?o editado com sucesso!", "success")
        return redirect(url_for("listar_servicos"))

    return render_template("editar_servicos.html", servico_a_editar=servico_a_editar)

@app.route("/servicos/deletar/<int:id>", methods=["POST"])
@role_required('funcionario')
def deletar_servico(id):
    servico_a_deletar = Servico.query.get_or_404(id)
    db.session.delete(servico_a_deletar)
    db.session.commit()
    log_security_event("service_deleted", level=logging.WARNING, user_id=current_user.id, service_id=id)
    flash("Servi?o apagado com sucesso!", "success")
    return redirect(url_for("listar_servicos"))

@app.route("/peca", methods = ["GET"])
@role_required('funcionario')
def listar_pecas():
    page = request.args.get("page", 1, type=int)
    termo_busca = sanitize_text(request.args.get("termo_busca"), max_length=100)
    pecas_query = Peca.query

    if termo_busca:
        termo_escapado = escape_like(termo_busca)
        pecas_query = pecas_query.filter(Peca.nome_peca.ilike(f"%{termo_escapado}%", escape='\\'))

    paginacao = pecas_query.order_by(Peca.nome_peca).paginate(page=page, per_page=15, error_out=False)
    pecas = paginacao.items
    return render_template("listar_pecas.html", pecas=pecas, paginacao=paginacao, termo_busca=termo_busca)

@app.route("/peca/cadastrar", methods = ["GET", "POST"])
@role_required('funcionario')
def cadastrar_peca():
    if request.method == "POST":
        try:
            nova_peca = Peca(
                nome_peca=get_form_value("nome_peca", required=True, max_length=150),
                detalhes_opcional=get_form_value("detalhes_opcional", max_length=1000, multiline=True),
                codigo_interno=get_form_value("codigo_interno", max_length=100),
                unidade_medida=get_form_value("unidade_medida", max_length=50),
                preco_unitario=get_form_decimal("preco_unitario", default=0.0),
            )
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("cadastrar_peca"))

        db.session.add(nova_peca)
        db.session.commit()
        log_security_event("part_created", user_id=current_user.id, part_id=nova_peca.id)
        flash("Pe?a cadastrada com sucesso!", "success")
        return redirect(url_for("listar_pecas"))

    return render_template("cadastrar_peca.html")

@app.route("/peca/editar/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def editar_peca(id):
    peca_a_editar = Peca.query.get_or_404(id)
    if request.method == "POST":
        try:
            peca_a_editar.nome_peca = get_form_value("nome_peca", required=True, max_length=150)
            peca_a_editar.detalhes_opcional = get_form_value("detalhes_opcional", max_length=1000, multiline=True)
            peca_a_editar.codigo_interno = get_form_value("codigo_interno", max_length=100)
            peca_a_editar.unidade_medida = get_form_value("unidade_medida", max_length=50)
            peca_a_editar.preco_unitario = get_form_decimal("preco_unitario", default=0.0)
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("editar_peca", id=id))

        db.session.commit()
        log_security_event("part_updated", user_id=current_user.id, part_id=id)
        flash("Pe?a editada com sucesso!", "success")
        return redirect(url_for("listar_pecas"))

    return render_template("editar_peca.html", peca_a_editar=peca_a_editar)

@app.route("/peca/deletar/<int:id>", methods=["POST"])
@role_required('funcionario')
def deletar_peca(id):
    peca_a_deletar = Peca.query.get_or_404(id)
    db.session.delete(peca_a_deletar)
    db.session.commit()
    log_security_event("part_deleted", level=logging.WARNING, user_id=current_user.id, part_id=id)
    flash("Pe?a apagada com sucesso!", "success")
    return redirect(url_for("listar_pecas"))

@app.route("/item/adicionar/<int:os_id>", methods=["POST"])
@role_required('funcionario')
def adicionar_servico(os_id):
    ordem_servico = OrdemServico.query.get_or_404(os_id)
    try:
        quantidade = get_form_int("quantidade", required=True, minimum=1, maximum=999)
        servico_id = get_form_int("servico_id", required=True, minimum=1)
        servico = Servico.query.get_or_404(servico_id)
        preco_cobrado = get_form_decimal("preco_cobrado", default=servico.preco_unitario)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_servico")

    novo_item = ItemServico(
        quantidade=quantidade,
        preco_cobrado=preco_cobrado,
        ordem_servico_id=ordem_servico.id,
        servico_id=servico.id,
    )
    db.session.add(novo_item)
    db.session.commit()
    flash("Servi?o adicionado com sucesso!", "success")
    return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_servico")

@app.route("/item/adicionar_peca/<int:os_id>", methods=["POST"])
@role_required('funcionario')
def adicionar_peca(os_id):
    ordem_servico = OrdemServico.query.get_or_404(os_id)
    try:
        quantidade = get_form_int("quantidade", required=True, minimum=1, maximum=999)
        peca_id = get_form_int("peca_id", required=True, minimum=1)
        peca = Peca.query.get_or_404(peca_id)
        preco_cobrado = get_form_decimal("preco_cobrado", default=peca.preco_unitario)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_peca")

    novo_item = ItemPeca(
        quantidade=quantidade,
        preco_cobrado=preco_cobrado,
        ordem_servico_id=ordem_servico.id,
        peca_id=peca.id,
    )
    db.session.add(novo_item)
    db.session.commit()
    flash("Pe?a adicionada com sucesso!", "success")
    return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_peca")

@app.route("/item/deletar/<int:id>", methods=["POST"])
@role_required('funcionario')
def remover_servico(id):
    item_a_deletar = ItemServico.query.get_or_404(id)
    os_id = item_a_deletar.ordem_servico_id
    db.session.delete(item_a_deletar)
    db.session.commit()
    flash("Servi?o removido com sucesso!", "success")
    return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_servico")

@app.route("/item_peca/deletar/<int:id>", methods=["POST"])
@role_required('funcionario')
def remover_peca(id):
    peca_a_deletar = ItemPeca.query.get_or_404(id)
    os_id = peca_a_deletar.ordem_servico_id
    db.session.delete(peca_a_deletar)
    db.session.commit()
    flash("Pe?a removida com sucesso!", "success")
    return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_peca")

@app.route("/relatorios", methods=["GET", "POST"])
@role_required('funcionario')
def relatorios():
    # Começa com uma query base que será modificada
    query = OrdemServico.query

    if request.method == "POST":
        # Pega os dados do formulário
        busca_nome = request.form.get('busca_nome', '')
        data_inicio_str = request.form.get('data_inicio')
        data_fim_str = request.form.get('data_fim')

        # Se o usuário preencheu um nome, adiciona o filtro de nome
        if busca_nome:
            query = query.join(Cliente).filter(Cliente.nome.ilike(f'%{busca_nome}%'))

        # Se o usuário preencheu as datas, adiciona o filtro de datas
        if data_inicio_str and data_fim_str:
            data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d')
            data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d')
            query = query.filter(
                OrdemServico.data_de_criacao >= data_inicio,
                OrdemServico.data_de_criacao <= data_fim
            )
        
        # Executa a query construída com os filtros
        ordens_exibidas = query.order_by(OrdemServico.data_de_criacao.desc()).all()

    else: # Lógica para o GET (quando a página é carregada pela primeira vez)
        ordens_exibidas = OrdemServico.query.order_by(OrdemServico.data_de_criacao.desc()).limit(10).all()
        
    # Renderiza o template, passando a lista de ordens
    return render_template("relatorios.html", ordens_exibidas=ordens_exibidas)

@app.route("/os/pdf/<int:os_id>")
@login_required
@role_required('funcionario')
def gerar_pdf_os(os_id):
    # 1. Busca os dados (igual a antes)
    ordem_servico = OrdemServico.query.get_or_404(os_id)
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # 2. Renderiza um template HTML para uma string (igual a antes)
    # Lembre-se que tínhamos falado em criar um pdf_template.html limpo
    html_renderizado = render_template("template_pdf.html", ordem_servico=ordem_servico, base_dir=base_dir)
    
    # 3. Prepara um "arquivo" em memória para receber o PDF
    result = BytesIO()
    
    # 4. Mágica do xhtml2pdf: converte o HTML para PDF e salva no "result"
    pdf = pisa.pisaDocument(BytesIO(html_renderizado.encode("UTF-8")), result)
    
    # 5. Se não houve erro na conversão...
    if not pdf.err:
        # Cria um nome de arquivo dinâmico
        nome_arquivo = f"OS-{ordem_servico.numero_formatado}.pdf"
        # Retorna o PDF para o navegador como um download
        flash("PDF gerado com sucesso!", "success")
        return Response(
            result.getvalue(),
            mimetype="application/pdf",
            headers={"Content-disposition": f"attachment; filename={nome_arquivo}"}
        )
        
    
    # Se houve algum erro, retorna uma mensagem simples
    return flash("Ocorreu um erro ao gerar o PDF."), 500

@app.route("/os/exibir_pdf/<int:os_id>")
@login_required
def exibir_pdf_os(os_id):
    ordem_servico = OrdemServico.query.get_or_404(os_id)
    if current_user.role == 'cliente' and ordem_servico.cliente_id != current_user.id:
        log_security_event("authorization_denied", level=logging.WARNING, path=request.path, user_id=current_user.id, target_os=os_id)
        abort(403)
    if current_user.role not in ('cliente', 'funcionario'):
        abort(403)
    return render_template("template_pdf.html", ordem_servico=ordem_servico)

@app.route("/os/<int:os_id>/adicionar_foto", methods=["POST"])
@login_required
@role_required('funcionario')
def adicionar_foto(os_id):
    OrdemServico.query.get_or_404(os_id)
    if 'foto' not in request.files:
        flash("Selecione uma imagem para enviar.", "warning")
        return redirect(request.referrer or url_for('detalhes_os', id=os_id))

    file = request.files['foto']
    legenda = sanitize_text(request.form.get('legenda', ''), max_length=255)
    try:
        novo_nome_arquivo = save_uploaded_image(file)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(request.referrer or url_for('detalhes_os', id=os_id))

    nova_foto = Foto(nome_arquivo=novo_nome_arquivo, legenda=legenda, ordem_servico_id=os_id)
    db.session.add(nova_foto)
    db.session.commit()
    flash("Foto adicionada com sucesso!", "success")
    return redirect(url_for('detalhes_os', id=os_id) + "#adicionar-foto")

@app.route("/os/<int:foto_id>/remover_foto", methods=["POST"])
@login_required
@role_required('funcionario')
def remover_foto(foto_id):
    foto_a_remover = Foto.query.get_or_404(foto_id)
    os_id = foto_a_remover.ordem_servico_id
    caminho_arquivo = os.path.join(app.config['UPLOAD_FOLDER'], foto_a_remover.nome_arquivo)

    try:
        if os.path.exists(caminho_arquivo):
            os.remove(caminho_arquivo)
        db.session.delete(foto_a_remover)
        db.session.commit()
        flash("Foto apagada com sucesso!", "success")
    except OSError as error:
        log_security_event("file_delete_failed", level=logging.ERROR, path=caminho_arquivo, reason=error)
        db.session.rollback()
        flash("N?o foi poss?vel remover a foto.", "danger")

    return redirect(url_for('detalhes_os', id=os_id) + "#fotos_equipamento")

@app.route("/contato")
@app.route("/contato")
def pagina_contato():
    return render_template("pagina_contato.html")

@app.route("/orcamento/<int:cliente_id>/novo", methods=["POST", "GET"])
@login_required
@role_required('funcionario')
def novo_orcamento(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)

    if request.method == "POST":
        try:
            ano_atual = datetime.utcnow().year
            maior_numero_orc_do_ano = db.session.query(func.max(Orcamento.numero_orcamento)).filter_by(ano=ano_atual).scalar()
            proximo_numero = 1 if maior_numero_orc_do_ano is None else maior_numero_orc_do_ano + 1

            novo_orcamento = Orcamento(
                cliente_id=cliente_id,
                numero_orcamento=proximo_numero,
                ano=ano_atual,
                equipamento=get_form_value('equipamento', required=True, max_length=150),
                marca=get_form_value('marca', max_length=100),
                modelo=get_form_value('modelo', max_length=100),
                numero_de_serie=get_form_value('numero_de_serie', max_length=100),
                validade_do_orcamento=get_form_date('validade_do_orcamento'),
                problema_informado=get_form_value('problema_informado', required=True, max_length=2000, multiline=True),
                problema_constatado=get_form_value('problema_constatado', required=True, max_length=2000, multiline=True),
                observacoes_cliente=get_form_value('observacoes_cliente', max_length=2000, multiline=True),
                observacoes_internas=get_form_value('observacoes_internas', max_length=2000, multiline=True),
                status=get_form_value('status', required=True, max_length=50),
                data_de_criacao=datetime.now().date(),
                tecnico_responsavel=get_form_value('tecnico_responsavel', max_length=50),
            )
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("novo_orcamento", cliente_id=cliente_id))

        db.session.add(novo_orcamento)
        db.session.commit()
        log_security_event("quote_created", user_id=current_user.id, quote_id=novo_orcamento.id, client_id=cliente_id)
        flash(f"Or?amento {novo_orcamento.numero_orcamento} criado com sucesso! Agora adicione os itens.", "success")
        return redirect(url_for("detalhes_cliente", id=cliente_id))

    return render_template("novo_orcamento.html", cliente=cliente)

@app.route("/orcamento/deletar/<int:id>", methods=["POST"])
@role_required('funcionario')
def deletar_orcamento(id):
    orcamento_a_deletar = Orcamento.query.get_or_404(id)
    id_do_cliente = orcamento_a_deletar.cliente.id
    db.session.delete(orcamento_a_deletar)
    db.session.commit()
    log_security_event("quote_deleted", level=logging.WARNING, user_id=current_user.id, quote_id=id)
    flash("Or?amento apagado com sucesso!", "success")
    return redirect(url_for("detalhes_cliente", id=id_do_cliente))

@app.route("/orcamento/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def detalhes_orcamento(id):
    orcamento = Orcamento.query.get_or_404(id)
    lista_servicos = Servico.query.all()
    lista_pecas = Peca.query.all()

    if request.method == "POST":
        try:
            orcamento.equipamento = get_form_value('equipamento', required=True, max_length=150)
            orcamento.marca = get_form_value('marca', max_length=100)
            orcamento.modelo = get_form_value('modelo', max_length=100)
            orcamento.numero_de_serie = get_form_value('numero_de_serie', max_length=100)
            orcamento.validade_do_orcamento = get_form_date('validade_do_orcamento')
            orcamento.problema_informado = get_form_value('problema_informado', required=True, max_length=2000, multiline=True)
            orcamento.problema_constatado = get_form_value('problema_constatado', required=True, max_length=2000, multiline=True)
            orcamento.observacoes_cliente = get_form_value('observacoes_cliente', max_length=2000, multiline=True)
            orcamento.observacoes_internas = get_form_value('observacoes_internas', max_length=2000, multiline=True)
            orcamento.status = get_form_value('status', required=True, max_length=50)
            orcamento.tecnico_responsavel = get_form_value('tecnico_responsavel', max_length=50)
        except ValueError as error:
            flash(str(error), "danger")
            return redirect(url_for("detalhes_orcamento", id=id))

        db.session.commit()
        log_security_event("quote_updated", user_id=current_user.id, quote_id=id)
        return redirect(url_for("detalhes_cliente", id=orcamento.cliente_id))

    return render_template("detalhes_orcamento.html", orcamento=orcamento, lista_servicos=lista_servicos, lista_pecas=lista_pecas)

@app.route("/orcamento/item_servico/adicionar/<int:orcamento_id>", methods=["POST"])
@role_required('funcionario')
def adicionar_servico_orcamento(orcamento_id):
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    try:
        servico_id = get_form_int("servico_id", required=True, minimum=1)
        quantidade = get_form_int("quantidade", required=True, minimum=1, maximum=999)
        servico = Servico.query.get_or_404(servico_id)
        preco_cobrado = get_form_decimal("preco_cobrado", default=servico.preco_unitario)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("detalhes_orcamento", id=orcamento_id) + "#adicionar_servico")

    novo_item = ItemOrcamentoServico(quantidade=quantidade, preco_cobrado=preco_cobrado, orcamento_id=orcamento.id, servico_id=servico.id)
    db.session.add(novo_item)
    db.session.commit()
    flash("Servi?o adicionado ao or?amento com sucesso!", "success")
    return redirect(url_for("detalhes_orcamento", id=orcamento_id) + "#adicionar_servico")

@app.route("/orcamento/item_servico/remover/<int:item_id>", methods=["POST"])
@role_required('funcionario')
def remover_servico_orcamento(item_id):
    """
    Rota para remover um item de serviço de um orçamento.
    """
    item_a_remover = ItemOrcamentoServico.query.get_or_404(item_id)
    orcamento_id = item_a_remover.orcamento_id
    
    db.session.delete(item_a_remover)
    db.session.commit()
    
    flash("Serviço removido do orçamento com sucesso!", "success")
    return redirect(url_for("detalhes_orcamento", id=orcamento_id) + "#adicionar_servico")


@app.route("/orcamento/item_peca/adicionar/<int:orcamento_id>", methods=["POST"])
@role_required('funcionario')
def adicionar_peca_orcamento(orcamento_id):
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    try:
        peca_id = get_form_int("peca_id", required=True, minimum=1)
        quantidade = get_form_int("quantidade", required=True, minimum=1, maximum=999)
        peca = Peca.query.get_or_404(peca_id)
        preco_cobrado = get_form_decimal("preco_cobrado", default=peca.preco_unitario)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(url_for("detalhes_orcamento", id=orcamento_id) + "#adicionar_peca")

    novo_item = ItemOrcamentoPeca(quantidade=quantidade, preco_cobrado=preco_cobrado, orcamento_id=orcamento.id, peca_id=peca.id)
    db.session.add(novo_item)
    db.session.commit()
    flash("Pe?a adicionada ao or?amento com sucesso!", "success")
    return redirect(url_for("detalhes_orcamento", id=orcamento_id) + "#adicionar_peca")

@app.route("/orcamento/item_peca/remover/<int:item_id>", methods=["POST"])
@role_required('funcionario')
def remover_peca_orcamento(item_id):
    """
    Rota para remover um item de peça de um orçamento.
    """
    item_a_remover = ItemOrcamentoPeca.query.get_or_404(item_id)
    orcamento_id = item_a_remover.orcamento_id
    
    db.session.delete(item_a_remover)
    db.session.commit()
    
    flash("Peça removida do orçamento com sucesso!", "success")
    return redirect(url_for("detalhes_orcamento", id=orcamento_id) + "#adicionar_peca")

# app.py

@app.route("/orcamento/<int:orcamento_id>/adicionar_foto", methods=["POST"])
@login_required
@role_required('funcionario')
def adicionar_foto_orcamento(orcamento_id):
    Orcamento.query.get_or_404(orcamento_id)
    if 'foto' not in request.files:
        flash("Selecione uma imagem para enviar.", "warning")
        return redirect(request.referrer or url_for('detalhes_orcamento', id=orcamento_id))

    file = request.files['foto']
    legenda = sanitize_text(request.form.get('legenda', ''), max_length=255)
    try:
        novo_nome_arquivo = save_uploaded_image(file)
    except ValueError as error:
        flash(str(error), "danger")
        return redirect(request.referrer or url_for('detalhes_orcamento', id=orcamento_id))

    nova_foto = Foto(nome_arquivo=novo_nome_arquivo, legenda=legenda, orcamento_id=orcamento_id)
    db.session.add(nova_foto)
    db.session.commit()
    flash("Foto adicionada com sucesso!", "success")
    return redirect(url_for('detalhes_orcamento', id=orcamento_id) + "#adicionar-foto")

@app.route("/orcamento/<int:foto_id>/remover_foto", methods=["POST"])
@login_required
@role_required('funcionario')
def remover_foto_orcamento(foto_id):
    foto_a_remover = Foto.query.get_or_404(foto_id)
    orcamento_id = foto_a_remover.orcamento_id
    caminho_arquivo = os.path.join(app.config['UPLOAD_FOLDER'], foto_a_remover.nome_arquivo)

    try:
        if os.path.exists(caminho_arquivo):
            os.remove(caminho_arquivo)
        db.session.delete(foto_a_remover)
        db.session.commit()
        flash("Foto apagada com sucesso!", "success")
    except OSError as error:
        log_security_event("file_delete_failed", level=logging.ERROR, path=caminho_arquivo, reason=error)
        db.session.rollback()
        flash("N?o foi poss?vel remover a foto.", "danger")

    return redirect(url_for('detalhes_orcamento', id=orcamento_id) + "#fotos_equipamento")

@app.route("/orcamento/pdf/<int:orcamento_id>")
@app.route("/orcamento/pdf/<int:orcamento_id>")
@login_required
@role_required('funcionario')
def gerar_pdf_orcamento(orcamento_id):
    # 1. Busca os dados do orçamento
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    
    # 2. Renderiza o template HTML específico para o PDF
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_renderizado = render_template("template_pdf_orcamento.html", orcamento=orcamento, base_dir=base_dir)
    
    # 3. Prepara um "arquivo" em memória para receber o PDF
    result = BytesIO()
    
    # 4. Converte o HTML para PDF e salva no "result"
    pdf = pisa.pisaDocument(BytesIO(html_renderizado.encode("UTF-8")), result)
    
    # 5. Se não houver erro na conversão, retorna o PDF para download
    if not pdf.err:
        nome_arquivo = f"Orcamento-{orcamento.numero_formatado}.pdf"
        flash("PDF gerado com sucesso!", "success")
        return Response(
            result.getvalue(),
            mimetype="application/pdf",
            headers={"Content-disposition": f"attachment; filename={nome_arquivo}"}
        )
        
    # Se houver algum erro, retorna uma mensagem de erro
    flash("Ocorreu um erro ao gerar o PDF.", "danger")
    return redirect(url_for('detalhes_orcamento', id=orcamento_id))

@app.route("/orcamento/exibir_pdf/<int:orcamento_id>")
@login_required
@role_required('funcionario')
def exibir_pdf_orcamento(orcamento_id):
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    
    return render_template("template_pdf_orcamento.html", orcamento=orcamento)

@app.route("/orcamento/converter/<int:orcamento_id>", methods=["POST"])
@login_required
@role_required('funcionario')
def converter_orcamento_para_os(orcamento_id):
    orcamento = Orcamento.query.get_or_404(orcamento_id)

    if orcamento.status != 'Aprovado':
        flash("Apenas orçamentos aprovados podem ser convertidos em OS.", "warning")
        return redirect(url_for('detalhes_orcamento', id=orcamento_id))

    os_existente = OrdemServico.query.filter_by(orcamento_id=orcamento.id).first()
    if os_existente:
        flash(f"Este orçamento já foi convertido na OS #{os_existente.numero_formatado}.", "info")
        return redirect(url_for('detalhes_os', id=os_existente.id))

    ano_atual = datetime.utcnow().year
    maior_numero_os_do_ano = db.session.query(func.max(OrdemServico.numero_sequencial)).filter_by(ano=ano_atual).scalar()
    novo_numero_sequencial = 1 if maior_numero_os_do_ano is None else maior_numero_os_do_ano + 1

    # --- LÓGICA DE CÓPIA ATUALIZADA ---
    nova_os = OrdemServico(
        cliente_id=orcamento.cliente_id,
        numero_sequencial=novo_numero_sequencial,
        ano=ano_atual,
        equipamento=orcamento.equipamento,
        marca=orcamento.marca,
        modelo=orcamento.modelo,
        status='Em andamento',
        orcamento_id=orcamento.id,
        
        # Copiando os novos campos
        numero_de_serie=orcamento.numero_de_serie,
        tecnico_responsavel=orcamento.tecnico_responsavel,
        defeito=orcamento.problema_informado, # 'defeito' na OS recebe 'problema_informado'
        problema_constatado=orcamento.problema_constatado,
        observacoes_cliente=orcamento.observacoes_cliente,
        observacoes_internas=orcamento.observacoes_internas
    )
    db.session.add(nova_os)
    
    for item_orc in orcamento.itens_servico:
        novo_item_servico = ItemServico(
            quantidade=item_orc.quantidade,
            preco_cobrado=item_orc.preco_cobrado,
            servico_id=item_orc.servico_id,
            ordem_servico=nova_os
        )
        db.session.add(novo_item_servico)

    for item_orc in orcamento.itens_peca:
        novo_item_peca = ItemPeca(
            quantidade=item_orc.quantidade,
            preco_cobrado=item_orc.preco_cobrado,
            peca_id=item_orc.peca_id,
            ordem_servico=nova_os
        )
        db.session.add(novo_item_peca)

    for foto in orcamento.fotos:
        foto.ordem_servico_id = nova_os.id
    
    orcamento.status = 'Convertido em OS'
    
    db.session.commit()

    flash(f"Orçamento convertido com sucesso na OS #{nova_os.numero_formatado}!", "success")
    return redirect(url_for('detalhes_os', id=nova_os.id))

@app.route("/curriculo/novo")
@login_required
@role_required('funcionario')
def novo_curriculo():
    novo_curriculo = Curriculo(data_criacao = datetime.now())
    db.session.add(novo_curriculo)
    db.session.commit()

    return redirect(url_for('curriculo_passo1', curriculo_id=novo_curriculo.id))

@app.route("/curriculo/passo1/<int:curriculo_id>", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def curriculo_passo1(curriculo_id):
    curriculo = Curriculo.query.get_or_404(curriculo_id)
    form = CurriculoPasso1Form()

    if form.validate_on_submit():
        curriculo.nome = form.nome.data
        curriculo.estado_civil = form.estado_civil.data
        curriculo.idade = form.idade.data
        curriculo.endereco = form.endereco.data
        curriculo.telefone_principal = form.telefone_principal.data
        curriculo.email = form.email.data

        db.session.commit()
        flash("Passo um concluido com sucesso!", "success")

        return redirect(url_for('curriculo_passo2', curriculo_id=curriculo.id))
    if request.method == "GET":
        form.nome.data = curriculo.nome
        form.estado_civil.data = curriculo.estado_civil
        form.idade.data = curriculo.idade
        form.endereco.data = curriculo.endereco
        form.telefone_principal.data = curriculo.telefone_principal
        form.email.data = curriculo.email
    
    return render_template("curriculo_passo1.html", form=form)

@app.route("/curriculo/passo2/<int:curriculo_id>", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def curriculo_passo2(curriculo_id):
    curriculo = Curriculo.query.get_or_404(curriculo_id)
    form = CurriculoPasso2Form()

    if form.validate_on_submit():
        lista_de_descricoes = form.formacoes.data
        lista_de_cursos = form.cursos.data
        for formacoes in curriculo.formacoes:
            db.session.delete(formacoes)
        for cursos in curriculo.cursos:
            db.session.delete(cursos)


        for descricao in lista_de_descricoes:
            if descricao:
                nova_formacao = FormacaoAcademica(descricao=descricao, curriculo_id=curriculo.id)
                db.session.add(nova_formacao)

        for descricao in lista_de_cursos:
            if descricao:
                novo_curso = Curso(descricao=descricao, curriculo_id=curriculo.id)
                db.session.add(novo_curso)

        db.session.commit()
        flash("Formações cadastradas com sucesso!", "success")

        return redirect(url_for('curriculo_passo3', curriculo_id=curriculo.id))
    if request.method == "GET":
        formacoes_atuais = curriculo.formacoes
        cursos_atuais = curriculo.cursos
        lista_de_descricoes = [formacao.descricao for formacao in formacoes_atuais]
        lista_de_cursos = [curso.descricao for curso in cursos_atuais]
        form = CurriculoPasso2Form(formacoes=lista_de_descricoes, cursos=lista_de_cursos)

    return render_template("curriculo_passo2.html", form=form)

@app.route("/curriculo/passo3/<int:curriculo_id>", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def curriculo_passo3(curriculo_id):
    curriculo = Curriculo.query.get_or_404(curriculo_id)
    form = CurriculoPasso3Form()

    if form.validate_on_submit():
        lista_de_experiencias  = form.experiencias.data
        for experiencias in curriculo.experiencias:
            db.session.delete(experiencias)
        
        for dados_experiencia in lista_de_experiencias:
            if dados_experiencia['empresa'] and dados_experiencia['cargo']:
                nova_experiencia = ExperienciaProfissional(
                    empresa = dados_experiencia['empresa'],
                    cargo = dados_experiencia['cargo'],
                    data_admissao = dados_experiencia['data_admissao'],
                    data_demissao = dados_experiencia['data_demissao'],
                    desabilitar_datas = dados_experiencia['desabilitar_datas'],
                    periodo = dados_experiencia['periodo'],
                    curriculo_id = curriculo.id
                )
                db.session.add(nova_experiencia)

        db.session.commit()
        flash("Experiências cadastradas com sucesso!", "success")

        return redirect(url_for('curriculo_passo4', curriculo_id=curriculo.id))
    if request.method == "GET":
        experiencias_atuais = curriculo.experiencias
        dados_para_o_form = []
        for experiencia in experiencias_atuais:
            dados_para_o_form.append({
                'empresa': experiencia.empresa,
                'cargo': experiencia.cargo,
                'data_admissao': experiencia.data_admissao,
                'data_demissao': experiencia.data_demissao,
                'desabilitar_datas': experiencia.desabilitar_datas,
                'periodo': experiencia.periodo
            })
        form = CurriculoPasso3Form(experiencias=dados_para_o_form)

    return render_template("curriculo_passo3.html", form=form)

@app.route("/curriculo/passo4/<int:curriculo_id>", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def curriculo_passo4(curriculo_id):
    curriculo = Curriculo.query.get_or_404(curriculo_id)
    form = CurriculoPasso4Form()

    if form.validate_on_submit():
        curriculo.objetivo = form.objetivo.data

        db.session.commit()
        flash("Passo 4 concluido com sucesso!", "success")

        return redirect(url_for('curriculo_passo_final', curriculo_id=curriculo.id))
    
    if request.method == "GET":
        if curriculo.objetivo != None:
            form.objetivo.data = curriculo.objetivo
        else:
            form.objetivo.data = """Busco uma vaga no mercado de trabalho, numa empresa onde eu possa
me desenvolver profissionalmente, demonstrar minhas competências e habilidades
técnicas e emocionais e, em conjunto com os meus colegas e gestores, eu possa
colaborar para o crescimento da organização e do grupo"""
        
    return render_template("curriculo_passo4.html", form=form)

@app.route("/curriculo/passo_final/<int:curriculo_id>")
@login_required
@role_required('funcionario')
def curriculo_passo_final(curriculo_id):
    curriculo = Curriculo.query.get_or_404(curriculo_id)
    
    return render_template("curriculo_preview.html", curriculo=curriculo)

@app.route("/curriculo/<int:curriculo_id>/download_pdf")
@login_required
@role_required('funcionario')
def download_curriculo_pdf(curriculo_id):
    # 1. Busca os dados (igual a antes)
    curriculo = Curriculo.query.get_or_404(curriculo_id)
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # 2. Renderiza um template HTML para uma string (igual a antes)
    # Lembre-se que tínhamos falado em criar um pdf_template.html limpo
    html_renderizado = render_template("curriculo_preview.html", curriculo=curriculo, para_pdf = True)
    
    # 3. Prepara um "arquivo" em memória para receber o PDF
    result = BytesIO()
    
    # 4. Mágica do xhtml2pdf: converte o HTML para PDF e salva no "result"
    pdf = pisa.pisaDocument(BytesIO(html_renderizado.encode("UTF-8")), result)
    
    # 5. Se não houve erro na conversão...
    if not pdf.err:
        # Cria um nome de arquivo dinâmico
        nome_arquivo = f"Curriculo-{curriculo.nome}.pdf"
        # Retorna o PDF para o navegador como um download
        flash("PDF gerado com sucesso!", "success")
        return Response(
            result.getvalue(),
            mimetype="application/pdf",
            headers={"Content-disposition": f"attachment; filename={nome_arquivo}"}
        )
        
    
    # Se houve algum erro, retorna uma mensagem simples
    return flash("Ocorreu um erro ao gerar o PDF."), 500

@app.route("/curriculos")
@login_required
@role_required('funcionario')
def listar_curriculos():
    page = request.args.get('page', 1, type=int)
    termos_busca = request.args.get('busca', '')
    query = Curriculo.query

    if termos_busca:
        query = query.filter(Curriculo.nome.ilike(f'%{termos_busca}%'))

    paginacao = query.order_by(Curriculo.nome).paginate(page=page, per_page=15, error_out=False)

    curriculos = paginacao.items
                                      
    return render_template('listar_curriculos.html', curriculos=curriculos, paginacao = paginacao, termos_busca=termos_busca)

@app.route("/curriculos/deletar/<int:curriculo_id>", methods=["POST"])
@login_required
@role_required('funcionario')
def deletar_curriculo(curriculo_id): 
    curriculo_a_deletar = Curriculo.query.get_or_404(curriculo_id)
    db.session.delete(curriculo_a_deletar)
    db.session.commit()
    flash("Curriculo apagado com sucesso!", "success")
    return redirect(url_for('listar_curriculos'))

@app.route("/curriculo/<int:curriculo_id>/download_word")
@login_required
@role_required('funcionario')
def download_curriculo_word(curriculo_id):
    curriculo = Curriculo.query.get_or_404(curriculo_id)
    html_renderizado = render_template("curriculo_preview.html", curriculo=curriculo, para_pdf = True)
    
    parser = HtmlToDocx()

    docx = parser.parse_html_string(html_renderizado)

    buffer = BytesIO()
    docx.save(buffer)
    buffer.seek(0)
    
    return send_file(
        buffer, 
        as_attachment=True, 
        download_name=f'Curriculo-{curriculo.nome}.docx', 
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        
@app.route("/contrato/novo", methods=["POST", "GET"])
@login_required
@role_required('funcionario')
def novo_contrato():
    form = ContratoForm()

    if form.validate_on_submit():
        novo_contrato = Contrato()
        novo_contrato.locador_nome = form.locador_nome.data
        novo_contrato.locador_rg = form.locador_rg.data
        novo_contrato.locador_cpf = form.locador_cpf.data
        novo_contrato.locador_endereco = form.locador_endereco.data
        
        novo_contrato.locatario_nome = form.locatario_nome.data
        novo_contrato.locatario_rg = form.locatario_rg.data
        novo_contrato.locatario_cpf = form.locatario_cpf.data
        novo_contrato.locatario_endereco = form.locatario_endereco.data
        
        novo_contrato.endereco_imovel = form.endereco_imovel.data
        novo_contrato.finalidade = form.finalidade.data
        novo_contrato.prazo_meses = form.prazo_meses.data
        novo_contrato.data_inicio = form.data_inicio.data
        novo_contrato.data_fim = form.data_fim.data
        novo_contrato.dia_pagamento = form.dia_pagamento.data
        novo_contrato.indice_reajuste = form.indice_reajuste.data
        novo_contrato.multa_percentual = form.multa_percentual.data
        novo_contrato.juros_percentual = form.juros_percentual.data
        valor_str = form.valor_aluguel.data or '0'

        # Tenta converter para um número float, tratando os formatos brasileiros
        try:
            # 1. Remove o separador de milhar (ponto)
            # 2. Troca o separador decimal (vírgula) por ponto
            valor_float = float(valor_str.replace('.', '').replace(',', '.'))
        except ValueError:
            # Se o usuário digitar um texto inválido (ex: "mil reais"), salva 0.0
            valor_float = 0.0

        # Salva o número float e limpo no banco de dados
        novo_contrato.valor_aluguel = valor_float

        novo_contrato.cidade_foro = form.cidade_foro.data
        novo_contrato.cidade = form.cidade.data
        novo_contrato.data_assinatura = form.data_assinatura.data
        novo_contrato.data_criacao = datetime.now()

        db.session.add(novo_contrato)
        db.session.commit()
        flash("Contrato criado com sucesso!", "success")

        return redirect(url_for('preview_contrato', id=novo_contrato.id))
    
    return render_template("novo_contrato.html", form=form)

@app.route("/contrato/preview/<int:id>")
@login_required
@role_required('funcionario')
def preview_contrato(id):
    contrato = Contrato.query.get_or_404(id)
    return render_template("template_contrato.html", contrato=contrato)

    
@app.route("/contrato/<int:id>/download_pdf")
@login_required
@role_required('funcionario')
def download_contrato_pdf(id):

    # 1. Busca os dados (igual a antes)
    contrato = Contrato.query.get_or_404(id)
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # 2. Renderiza um template HTML para uma string (igual a antes)
    # Lembre-se que tínhamos falado em criar um pdf_template.html limpo
    html_renderizado = render_template("template_contrato.html", contrato=contrato, para_pdf = True)
    
    # 3. Prepara um "arquivo" em memória para receber o PDF
    result = BytesIO()
    
    # 4. Mágica do xhtml2pdf: converte o HTML para PDF e salva no "result"
    pdf = pisa.pisaDocument(BytesIO(html_renderizado.encode("UTF-8")), result)
    
    # 5. Se não houve erro na conversão...
    if not pdf.err:
        # Cria um nome de arquivo dinâmico
        nome_arquivo = f"Contrato-{contrato.locatario_nome}.pdf"
        # Retorna o PDF para o navegador como um download
        flash("PDF gerado com sucesso!", "success")
        return Response(
            result.getvalue(),
            mimetype="application/pdf",
            headers={"Content-disposition": f"attachment; filename={nome_arquivo}"}
        )
        
    
    # Se houve algum erro, retorna uma mensagem simples
    return flash("Ocorreu um erro ao gerar o PDF."), 500

@app.route("/contrato/<int:id>/download_word")
@login_required
@role_required('funcionario')
def download_contrato_word(id):
    contrato = Contrato.query.get_or_404(id)
    html_renderizado = render_template("template_contrato.html", contrato=contrato, para_pdf = True)
    
    parser = HtmlToDocx()

    docx = parser.parse_html_string(html_renderizado)

    buffer = BytesIO()
    docx.save(buffer)
    buffer.seek(0)
    
    return send_file(
        buffer, 
        as_attachment=True, 
        download_name=f'Contrato-{contrato.locador_nome}.docx', 
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

@app.route("/contrato/deletar/<int:id>", methods=["POST"])
@login_required
@role_required('funcionario')
def deletar_contrato(id):
    contrato_a_deletar = Contrato.query.get_or_404(id)
    db.session.delete(contrato_a_deletar)
    db.session.commit()
    log_security_event("contract_deleted", level=logging.WARNING, user_id=current_user.id, contract_id=id)
    flash("Contrato apagado com sucesso!", "success")
    return redirect(url_for('listar_contratos'))

@app.route("/contrato/editar/<int:id>", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def editar_contrato(id):
    contrato = Contrato.query.get_or_404(id)
    form = ContratoForm()

    if form.validate_on_submit():
        contrato.locador_nome = form.locador_nome.data
        contrato.locador_rg = form.locador_rg.data
        contrato.locador_cpf = form.locador_cpf.data
        contrato.locador_endereco = form.locador_endereco.data
        
        contrato.locatario_nome = form.locatario_nome.data
        contrato.locatario_rg = form.locatario_rg.data
        contrato.locatario_cpf = form.locatario_cpf.data
        contrato.locatario_endereco = form.locatario_endereco.data
        
        contrato.endereco_imovel = form.endereco_imovel.data
        contrato.finalidade = form.finalidade.data
        contrato.prazo_meses = form.prazo_meses.data
        contrato.data_inicio = form.data_inicio.data
        contrato.data_fim = form.data_fim.data
        contrato.dia_pagamento = form.dia_pagamento.data
        contrato.indice_reajuste = form.indice_reajuste.data
        contrato.multa_percentual = form.multa_percentual.data
        contrato.juros_percentual = form.juros_percentual.data
        valor_str = form.valor_aluguel.data or '0' # Pega o texto do form ou '0' se estiver vazio
        try:
            valor_float = float(valor_str.replace('.', '').replace(',', '.')) # Remove o separador de milhar e troca a vírgula
        except ValueError:
            valor_float = 0.0 # Define um valor padrão em caso de erro

        contrato.valor_aluguel = valor_float # Salva o número float convertido


        contrato.cidade_foro = form.cidade_foro.data
        contrato.cidade = form.cidade.data
        contrato.data_assinatura = form.data_assinatura.data

        db.session.commit()
        flash("Contrato editado com sucesso!", "success")
        return redirect(url_for('listar_contratos'))
    
    elif request.method == "GET":
        form.locador_nome.data = contrato.locador_nome
        form.locador_rg.data = contrato.locador_rg
        form.locador_cpf.data = contrato.locador_cpf
        form.locador_endereco.data = contrato.locador_endereco
        
        form.locatario_nome.data = contrato.locatario_nome
        form.locatario_rg.data = contrato.locatario_rg
        form.locatario_cpf.data = contrato.locatario_cpf
        form.locatario_endereco.data = contrato.locatario_endereco
        
        form.endereco_imovel.data = contrato.endereco_imovel
        form.finalidade.data = contrato.finalidade 
        form.prazo_meses.data = contrato.prazo_meses
        form.data_inicio.data = contrato.data_inicio
        form.data_fim.data = contrato.data_fim
        form.valor_aluguel.data = contrato.valor_aluguel
        form.dia_pagamento.data = contrato.dia_pagamento
        form.indice_reajuste.data = contrato.indice_reajuste
        form.multa_percentual.data = contrato.multa_percentual
        form.juros_percentual.data = contrato.juros_percentual
        form.valor_aluguel.data = contrato.valor_aluguel

        form.cidade_foro.data = contrato.cidade_foro
        form.cidade.data = contrato.cidade
        form.data_assinatura.data = contrato.data_assinatura
    
    return render_template("novo_contrato.html", form=form, contrato=contrato)

@app.route("/contratos") # Ou a URL que voc? preferir
@login_required
@role_required('funcionario')
def listar_contratos():
    page = request.args.get('page', 1, type=int)
    termos_busca = sanitize_text(request.args.get('busca', ''), max_length=100)
    query = Contrato.query

    if termos_busca:
        termo_escapado = escape_like(termos_busca)
        query = query.filter(Contrato.locatario_nome.ilike(f'%{termo_escapado}%', escape='\\'))

    paginacao = query.order_by(Contrato.locatario_nome).paginate(page=page, per_page=15, error_out=False)
    contratos = paginacao.items
    return render_template('listar_contratos.html', contratos=contratos, paginacao=paginacao, termos_busca=termos_busca)

@app.route("/configuracoes", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def configuracoes():
    config = Configuracao.query.first()
    if not config:
        config = Configuracao()
        db.session.add(config)

    form = ConfiguracaoForm()
    if form.validate_on_submit():
        config.nome_loja = sanitize_text(form.nome_loja.data, max_length=100)
        config.cnpj = sanitize_text(form.cnpj.data, max_length=20)
        config.endereco = sanitize_text(form.endereco.data, max_length=200)
        config.telefone = sanitize_text(form.telefone.data, max_length=20)

        if form.logomarca.data:
            try:
                config.logomarca = save_uploaded_image(form.logomarca.data)
            except ValueError as error:
                flash(str(error), 'danger')
                return redirect(url_for('configuracoes'))

        config.texto_garantia = sanitize_text(form.texto_garantia.data, max_length=4000, multiline=True)
        config.email_contato = sanitize_text(form.email_contato.data, max_length=100)
        config.site = sanitize_text(form.site.data, max_length=100)

        db.session.commit()
        flash("Configura??es salvas com sucesso!", "success")
        return redirect(url_for('configuracoes'))
    elif request.method == "GET":
        form.nome_loja.data = config.nome_loja
        form.cnpj.data = config.cnpj
        form.endereco.data = config.endereco
        form.telefone.data = config.telefone
        form.texto_garantia.data = config.texto_garantia
        form.email_contato.data = config.email_contato
        form.site.data = config.site

    return render_template("configuracoes.html", form=form, config=config)

@app.route("/configuracoes/reset", methods=["POST"])
@login_required
@role_required('funcionario')
def configuracoes_reset():
    config = Configuracao.query.first()
    if config:
        db.session.delete(config)
    db.session.commit()
    flash("Configurações resetadas com sucesso!", "success")
    return redirect(url_for('configuracoes'))

@app.route("/configuracoes/logo/remover", methods=["POST"])
@login_required
@role_required('funcionario')
def configuracoes_remove_logo():
    config = Configuracao.query.first()

    if config and config.logomarca:
        caminho_arquivo = os.path.join(app.config['UPLOAD_FOLDER'], config.logomarca)

        try:
            if os.path.exists(caminho_arquivo):
                os.remove(caminho_arquivo)

            config.logomarca = None
            db.session.commit()
            flash("Logomarca removida com sucesso!", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao remover a logomarca: {e}", "danger")

    return redirect(url_for('configuracoes'))

@app.route("/orcamento/<int:id>/comprovante_entrada_pdf", methods=["GET"])
@login_required
@role_required('funcionario')
def gerar_comprovante_entrada_pdf(id):
    orcamento = Orcamento.query.get_or_404(id)
        
    config = Configuracao.query.first()
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    html_renderizado = render_template(
        "template_comprovante_entrada.html",
          orcamento=orcamento,
          config=config,
          para_pdf = True,
          base_dir=base_dir
    )
    
    result = BytesIO()
    
    pdf = pisa.pisaDocument(BytesIO(html_renderizado.encode("UTF-8")), result)
    
    # 5. Se não houve erro na conversão...
    if not pdf.err:
        # Cria um nome de arquivo dinâmico
        nome_arquivo = f"Comprovante-Entrada-{orcamento.numero_formatado}.pdf"
        # Retorna o PDF para o navegador como um download
        flash("PDF gerado com sucesso!", "success")
        return Response(
            result.getvalue(),
            mimetype="application/pdf",
            headers={"Content-disposition": f"attachment; filename={nome_arquivo}"}
        )
        
    
    # Se houve algum erro, retorna uma mensagem simples
    return flash("Ocorreu um erro ao gerar o PDF."), 500
    
@app.route("/utilidades/impressoras")
@login_required
@role_required('funcionario')
def listar_impressoras():
    page = request.args.get('page', 1, type=int)
    termos_busca = sanitize_text(request.args.get('busca', ''), max_length=100)
    query = Impressora.query

    if termos_busca:
        termo_escapado = escape_like(termos_busca)
        query = query.filter(Impressora.modelo.ilike(f'%{termo_escapado}%', escape='\\'))

    paginacao = query.order_by(Impressora.modelo).paginate(page=page, per_page=15, error_out=False)
    impressoras = paginacao.items
    return render_template('listar_impressoras.html', impressoras=impressoras, paginacao=paginacao, termos_busca=termos_busca)

@app.route("/utilidades/impressoras/nova", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def nova_impressora():
    form = ImpressoraForm()
    if form.validate_on_submit():
        # Verifica se o modelo já existe
        modelo_existente = Impressora.query.filter_by(modelo=form.modelo.data).first()
        if modelo_existente:
            flash('Erro: Já existe uma impressora cadastrada com esse modelo.', 'danger')
        else:
            nova_imp = Impressora(
                modelo=form.modelo.data,
                descricao=form.descricao.data
            )
            db.session.add(nova_imp)
            db.session.commit()
            flash('Impressora cadastrada com sucesso!', 'success')
            return redirect(url_for('listar_impressoras'))

    # Vamos criar um template simples para o formulário
    return render_template('form_impressora.html', form=form, titulo='Nova Categoria de Link')

@app.route("/utilidades/impressoras/editar/<int:id>", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def editar_impressora(id):
    impressora = Impressora.query.get_or_404(id)
    form = ImpressoraForm()

    if form.validate_on_submit():
        impressora.modelo = form.modelo.data
        impressora.descricao = form.descricao.data
        db.session.commit()
        flash('Impressora atualizada com sucesso!', 'success')
        return redirect(url_for('listar_impressoras'))

    elif request.method == "GET":
        form.modelo.data = impressora.modelo
        form.descricao.data = impressora.descricao

    return render_template('form_impressora.html', form=form, titulo=f'Editar Categoria: {impressora.modelo}')

@app.route("/utilidades/impressoras/deletar/<int:id>", methods=["POST"])
@login_required
@role_required('funcionario')
def deletar_impressora(id):
    # Usamos POST para segurança, e o botão no template já faz isso.
    impressora = Impressora.query.get_or_404(id)

    # Graças ao 'cascade' que definimos no modelo, 
    # o banco de dados também apagará todos os 'Recursos' (links) 
    # associados a esta impressora.

    db.session.delete(impressora)
    db.session.commit()
    flash(f'Impressora "{impressora.modelo}" e todos os seus links foram apagados com sucesso!', 'success')
    return redirect(url_for('listar_impressoras'))


@app.route("/utilidades/impressoras/<int:id>/detalhes", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def detalhes_impressora(id):
    # 1. Busca a impressora (a "categoria")
    impressora = Impressora.query.get_or_404(id)

    # 2. Cria o formulário para adicionar um NOVO recurso
    form = RecursoForm()

    # 3. Lógica para salvar o NOVO recurso
    if form.validate_on_submit():
        novo_recurso = RecursoImpressora(
            impressora_id = impressora.id,
            tipo = form.tipo.data,
            descricao = form.descricao.data,
            sistema_operacional = form.sistema_operacional.data,
            link_download = form.link_download.data
        )
        db.session.add(novo_recurso)
        db.session.commit()
        flash('Novo recurso adicionado com sucesso!', 'success')
        return redirect(url_for('detalhes_impressora', id=impressora.id)) # Recarrega a página

    # 4. No GET, apenas busca os recursos já existentes para listar
    recursos_cadastrados = impressora.recursos

    # 5. Renderiza um novo template
    return render_template(
        'detalhes_impressora.html', 
        impressora=impressora, 
        recursos=recursos_cadastrados, 
        form=form
    )

@app.route("/utilidades/recurso/deletar/<int:id>", methods=["POST"])
@login_required
@role_required('funcionario')
def deletar_recurso(id):
    # 1. Busca o recurso (o link) que será deletado
    recurso = RecursoImpressora.query.get_or_404(id)

    # 2. IMPORTANTE: Precisamos saber para qual impressora voltar.
    #    Guardamos o ID da impressora "pai" antes de deletar.
    impressora_id = recurso.impressora_id

    # 3. Deleta o recurso
    db.session.delete(recurso)
    db.session.commit()

    flash('Recurso (link) removido com sucesso!', 'success')

    # 4. Redireciona de volta para a página de detalhes da impressora
    return redirect(url_for('detalhes_impressora', id=impressora_id))

@app.route("/utilidades/recurso/editar/<int:id>", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def editar_recurso(id):

    # 1. Busca o recurso específico que queremos editar
    recurso = RecursoImpressora.query.get_or_404(id)

    # 2. Reutilizamos o mesmo formulário que já criamos
    form = RecursoForm()

    # 3. Lógica de salvamento (POST)
    if form.validate_on_submit():
        # Atualiza os dados do objeto 'recurso'
        recurso.tipo = form.tipo.data
        recurso.descricao = form.descricao.data
        recurso.sistema_operacional = form.sistema_operacional.data
        recurso.link_download = form.link_download.data

        db.session.commit()
        flash('Recurso atualizado com sucesso!', 'success')

        # Redireciona de volta para a página de detalhes
        return redirect(url_for('detalhes_impressora', id=recurso.impressora_id))

    # 4. Lógica de carregamento (GET)
    elif request.method == "GET":
        # Preenche o formulário com os dados atuais do recurso
        form.tipo.data = recurso.tipo
        form.descricao.data = recurso.descricao
        form.sistema_operacional.data = recurso.sistema_operacional
        form.link_download.data = recurso.link_download

    # 5. Renderiza um novo template para o formulário de edição
    return render_template(
        'form_recurso.html', 
        form=form, 
        recurso=recurso,  # Passamos 'recurso' para o template
        titulo=f'Editar Recurso: {recurso.descricao[:30]}...'
    )

@app.route("/resets")
@login_required
@role_required('cliente')
def dashboard_resets():
    # --- PASSO 1: Verificação de Permissão (NOVA LÓGICA) ---
    # Verifica se o cliente tem PELO MENOS UMA impressora permitida.
    # .count() é mais rápido do que carregar todos os objetos.
    if current_user.impressoras_permitidas.count() == 0:
        flash("Você não tem permissão para acessar esta área.", "danger")
        return redirect(url_for('dashboard_cliente')) # Volta para o dashboard normal

    # --- PASSO 2: Lógica de Busca (NOVA LÓGICA) ---
    page = request.args.get('page', 1, type=int)
    termos_busca = request.args.get('busca', '')

    # A MUDANÇA PRINCIPAL:
    # Em vez de Impressora.query, buscamos DENTRO da lista do usuário
    query = current_user.impressoras_permitidas.options(
        joinedload(Impressora.recursos) # A otimização que já tínhamos
    ).order_by(Impressora.modelo)

    if termos_busca:
        # Filtra pelo modelo da impressora
        query = query.filter(Impressora.modelo.ilike(f'%{termos_busca}%'))

    # A paginação funciona exatamente da mesma forma
    paginacao = query.paginate(
        page=page, 
        per_page=10,
        error_out=False
    )
    impressoras_da_pagina = paginacao.items

    # --- PASSO 3: Renderizar o Template ---
    # O template 'dashboard_resets.html' não precisa de NENHUMA MUDANÇA,
    # pois ele já espera as variáveis 'impressoras' e 'paginacao'.
    return render_template(
        'dashboard_resets.html', 
        impressoras=impressoras_da_pagina, 
        paginacao=paginacao,
        termos_busca=termos_busca
    )

@app.route("/recibo/gerar", methods=["GET", "POST"])
@login_required
@role_required('funcionario')
def gerar_recibo_rapido():
    form = ReciboSimplesForm()
    
    if form.validate_on_submit():
        try:
            # 1. Tratamento do valor para conversão numérica
            # Remove pontos de milhar e troca vírgula por ponto decimal
            valor_str = form.valor.data.replace('.', '').replace(',', '.')
            valor_float = float(valor_str)
            
            # 2. Geração do valor por extenso (Ex: "cento e cinquenta reais")
            # O parâmetro to='currency' cuida dos termos "reais" e "centavos"
            valor_extenso = num2words(valor_float, to='currency', lang='pt_BR')
            
            # 3. Organização dos dados para o template
            dados_recibo = {
                'valor': form.valor.data,
                'valor_extenso': valor_extenso, # Enviando o extenso para o PDF
                'pagador': form.pagador.data,
                'documento': form.document_pagador.data,
                'referente': form.referente_a.data,
                'cidade': form.cidade.data,
                'data': form.data_emissao.data.strftime('%d/%m/%Y')
            }
            
            # 4. Preparação para o PDF
            base_dir = os.path.dirname(os.path.abspath(__file__))
            # Certifique-se de que 'config' está disponível (via context_processor ou query direta)
            html_renderizado = render_template(
                "template_recibo_pdf.html", 
                recibo=dados_recibo, 
                base_dir=base_dir
            )
            
            # 5. Geração do arquivo PDF
            result = BytesIO()
            pdf = pisa.pisaDocument(BytesIO(html_renderizado.encode("UTF-8")), result)
            
            if not pdf.err:
                nome_arquivo = f"Recibo_{form.pagador.data[:15]}.pdf"
                return Response(
                    result.getvalue(),
                    mimetype="application/pdf",
                    headers={"Content-disposition": f"attachment; filename={nome_arquivo}"}
                )
            
            flash("Erro técnico ao gerar o PDF.", "danger")
            
        except ValueError:
            flash("Valor numérico inválido. Use o formato 00,00", "warning")
        except Exception as e:
            flash(f"Ocorreu um erro inesperado: {str(e)}", "danger")
        
    return render_template("gerar_recibo.html", form=form)

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "0") == "1")
