from enum import unique
from http import client
from os import replace
import re
from sqlite3.dbapi2 import Timestamp
from flask import Flask, render_template, request, redirect, url_for, abort, Response, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_migrate import Migrate
from datetime import datetime
import click
import markdown2
from functools import wraps
from io import BytesIO
from xhtml2pdf import pisa
import os
from werkzeug.utils import secure_filename
from datetime import date
from htmldocx import HtmlToDocx
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, IntegerField, SubmitField, FieldList, Form, FormField, DateField, BooleanField, FloatField
from wtforms.validators import DataRequired, Email, Optional


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
                # This is handled by Flask-Login's login_manager
                return abort(401) 
            if current_user.role != role:
                # If the user has the wrong role, show "Forbidden"
                return abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator

app = Flask(__name__)
app.config['SECRET_KEY'] = '0625fa577ac24b41fd655e4935191fb6'
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///site.db'
db = SQLAlchemy(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

#Criação comando pra criar usuario master
@app.cli.command("create-user")  # Define o nome do comando no terminal
@click.argument("username")      # Define o primeiro argumento que o comando espera
@click.argument("password")      # Define o segundo argumento
def create_user(username, password):
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


#Classes
class Cliente(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key = True)
    ordens_servico = db.relationship('OrdemServico', back_populates='cliente', cascade="all, delete-orphan")
    orcamento = db.relationship('Orcamento', back_populates='cliente', cascade="all, delete-orphan")
    nome = db.Column(db.String(100), nullable=False)
    #login usuario
    username_cliente = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(60), nullable=False)
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

    def get_id(self):
        return f"cliente-{self.id}"

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

texto_clausulas = """  """
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

    clausulas = db.Column(db.Text) 


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
    valor_aluguel = FloatField("Valor do Aluguel (R$)")
    dia_pagamento = IntegerField("Dia do Pagamento", default=10)
    indice_reajuste = StringField("Índice de Reajuste", default="IGP-M")
    multa_percentual = IntegerField("Multa (%)", default=5)
    juros_percentual = IntegerField("Juros (%)", default=1)

    cidade_foro = StringField("Foro", default="Ibirité")
    cidade = StringField("Cidade", default="Ibirité - MG")
    data_assinatura = DateField("Data de Assinatura", default=date.today())

    clausulas = TextAreaField("Clausulas", validators=[DataRequired()])


    submit = SubmitField("Salvar Contrato")

class FormacaoAcademica(db.Model):
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

#Funções principais
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

@app.route("/logout")
def logout():
    logout_user()
    flash("Logoff efetuado!", "danger")
    return redirect(url_for("login"))

@app.route("/logout_cliente")
def logout_cliente():
    logout_user()
    flash("Logoff efetuado!", "danger")
    return redirect(url_for("index"))

@app.route("/login", methods = ("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = Usuario.query.filter_by(username=username).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('home'))
        else:
            flash("Usuário ou senha incorretos", "danger")
            return redirect(url_for('login'))

    return render_template('login.html')

@app.route("/login_cliente", methods = ("GET", "POST"))
def login_cliente():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = Cliente.query.filter_by(username_cliente=username).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard_cliente'))

    return render_template('login_cliente.html')

@app.route("/cliente/dashboard")
@role_required('cliente')
def dashboard_cliente():
    return render_template("dashboard_cliente.html")

@app.route("/cliente/os/<int:id>")
@role_required("cliente")
def ver_os_cliente(id):
    ordem_servico = OrdemServico.query.get(id)
    if ordem_servico.cliente_id == current_user.id:
        return render_template("ver_os_cliente.html", ordem_servico=ordem_servico)
    else:
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
    return render_template("home.html", total_clientes=total_clientes, ordens_abertas=ordens_abertas, ordens_concluidas=ordens_concluidas)

@app.route("/clientes/cadastrar", methods=["GET", "POST"])
@role_required('funcionario')
def cadastrar_cliente():
    if request.method == "POST":
        #dados do usuario e senha do cliente
        username_cliente = request.form["username_cliente"]
        password_cliente = request.form["password_cliente"]

        password_hash = bcrypt.generate_password_hash(password_cliente).decode('utf-8')

        #dados do cliente
        nome_cliente = request.form["nome"]
        telefone_celular = request.form["telefone_celular"]
        telefone_auxiliar = request.form["telefone_auxiliar"]
        cpf = request.form["cpf"]
        cnpj = request.form["cnpj"]
        cep = request.form["cep"]
        logradouro = request.form["logradouro"]
        numero = request.form["numero"]
        complemento = request.form["complemento"]
        bairro = request.form["bairro"]
        cidade = request.form["cidade"]
        estado = request.form["estado"]
        anotacoes = request.form["anotacoes"]
        novo_cliente = Cliente(
            nome=nome_cliente,
            username_cliente=username_cliente,
            password_hash=password_hash,
            telefone_celular=telefone_celular,
            telefone_auxiliar = telefone_auxiliar,
            cpf = cpf,
            cnpj = cnpj,
            cep = cep,
            logradouro = logradouro,
            numero = numero,
            complemento = complemento,
            bairro = bairro,
            cidade = cidade,
            estado = estado,
            anotacoes = anotacoes
            )
        db.session.add(novo_cliente)
        db.session.commit()

        flash("Cliente cadastrado com sucesso!", "success")
        return redirect(url_for("listar_clientes"))

    return render_template("cadastrar_cliente.html")

@app.route("/clientes/deletar/<int:id>")
@role_required('funcionario')
def deletar_cliente(id):
    cliente_a_deletar = Cliente.query.get(id)
    db.session.delete(cliente_a_deletar)
    db.session.commit()
    flash("Cliente apagado com sucesso!", "success")
    return redirect(url_for("listar_clientes"))

@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def editar_cliente(id):
    cliente_a_editar = Cliente.query.get_or_404(id)
    if request.method == "POST": 
        nome_cliente = request.form["nome"]
        telefone_celular = request.form["telefone_celular"]
        telefone_auxiliar = request.form["telefone_auxiliar"]
        cpf = request.form["cpf"]
        cnpj = request.form["cnpj"]
        cep = request.form["cep"]
        logradouro = request.form["logradouro"]
        numero = request.form["numero"]
        complemento = request.form["complemento"]
        bairro = request.form["bairro"]
        cidade = request.form["cidade"]
        estado = request.form["estado"]
        anotacoes = request.form["anotacoes"]

        cliente_a_editar.nome = nome_cliente
        cliente_a_editar.telefone_celular = telefone_celular
        cliente_a_editar.telefone_auxiliar = telefone_auxiliar
        cliente_a_editar.cpf = cpf
        cliente_a_editar.cnpj = cnpj
        cliente_a_editar.cep = cep
        cliente_a_editar.logradouro = logradouro
        cliente_a_editar.numero = numero
        cliente_a_editar.complemento = complemento
        cliente_a_editar.bairro = bairro
        cliente_a_editar.cidade = cidade
        cliente_a_editar.estado = estado
        cliente_a_editar.anotacoes = anotacoes
        db.session.commit()
        flash("Cliente editado com sucesso!", "success")

        return redirect(url_for("listar_clientes"))
    
    return render_template("editar_cliente.html", cliente_a_editar = cliente_a_editar)
    
@app.route("/clientes", methods = ["GET"])
@role_required('funcionario')
def listar_clientes():
    termo_busca = request.args.get("termo_busca")
    query_clientes = Cliente.query
    
    if termo_busca:
        query_clientes = query_clientes.filter(Cliente.nome.ilike(f"%{termo_busca}%"))
        
    clientes = query_clientes.all()
    return render_template("listar_clientes.html", clientes = clientes)

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
        ano_atual = datetime.utcnow().year

        # Encontra o maior numero_sequencial para o ano atual
        maior_numero_os_do_ano = db.session.query(func.max(OrdemServico.numero_sequencial)).filter_by(ano=ano_atual).scalar()

        # Se não houver nenhuma OS este ano, começa em 1. Senão, incrementa.
        if maior_numero_os_do_ano is None:
            novo_numero_sequencial = 1
        else:
            novo_numero_sequencial = maior_numero_os_do_ano + 1

        equipamento = request.form["equipamento"]
        marca = request.form["marca"]
        modelo = request.form["modelo"]
        defeito = request.form["defeito"]
        status = request.form["status"]

        nova_os = OrdemServico(
            cliente_id = cliente_id,
            numero_sequencial = novo_numero_sequencial,
            ano = ano_atual,
            equipamento=equipamento,
            marca = marca,
            modelo = modelo,
            defeito = defeito,
            status = status
            )
        db.session.add(nova_os)
        db.session.commit()
        flash(f"OS cadastrado com sucesso!", "success")
        return redirect(url_for("detalhes_cliente", id=cliente_id))

    return render_template("cadastrar_os.html", cliente = cliente)

# app.py

@app.route("/os/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def detalhes_os(id):
    ordem_servico = OrdemServico.query.get_or_404(id) # Alterado para get_or_404
    lista_servicos = Servico.query.all()
    lista_pecas = Peca.query.all()

    if request.method == "POST":
        # Salva os campos existentes
        ordem_servico.equipamento = request.form["equipamento"]
        ordem_servico.marca = request.form["marca"]
        ordem_servico.modelo = request.form["modelo"]
        ordem_servico.defeito = request.form["defeito"]
        ordem_servico.status = request.form["status"]
        
        # Salva os NOVOS campos
        ordem_servico.tecnico_responsavel = request.form.get('tecnico_responsavel')
        ordem_servico.numero_de_serie = request.form.get('numero_de_serie')
        ordem_servico.problema_constatado = request.form.get('problema_constatado')
        ordem_servico.servico_executado = request.form.get('servico_executado')
        ordem_servico.observacoes_cliente = request.form.get('observacoes_cliente')
        ordem_servico.observacoes_internas = request.form.get('observacoes_internas')
    
        db.session.commit()
        flash("Ordem de Serviço salva com sucesso!", "success")
        return redirect(url_for("detalhes_os", id=ordem_servico.id))
    
    return render_template("detalhes_os.html", ordem_servico=ordem_servico, lista_servicos=lista_servicos, lista_pecas=lista_pecas)

@app.route("/os/deletar/<int:id>")
@role_required('funcionario')
def deletar_os(id):
    os_a_deletar = OrdemServico.query.get(id)
    id_do_cliente = os_a_deletar.cliente.id
    db.session.delete(os_a_deletar)
    db.session.commit()
    flash("Os apagada com sucesso!", "success")
    return redirect(url_for("detalhes_cliente", id=id_do_cliente))

@app.route("/servicos")
@role_required('funcionario')
def listar_servicos():
    termo_busca = request.args.get("termo_busca")
    servico_query = Servico.query

    if termo_busca:
        servico_query = servico_query.filter(Servico.descricao_servico.ilike(f"%{termo_busca}%"))
    
    servicos = servico_query.all()

    return render_template("listar_servicos.html", servicos = servicos)

@app.route("/servicos/cadastrar", methods = ["GET", "POST"])
@role_required('funcionario')
def cadastrar_servico():
    if request.method == "POST":
        descricao_servico = request.form["descricao_servico"]
        detalhes_opcional = request.form["detalhes_opcional"]
        unidade_medida = request.form["unidade_medida"]
        preco_unitario = request.form["preco_unitario"]
        if preco_unitario == "":
            preco_unitario_novo = 0.0
        else:
            preco_unitario_novo = preco_unitario.replace(",", ".")

        novo_servico = Servico(
            descricao_servico=descricao_servico,
            detalhes_opcional=detalhes_opcional,
            unidade_medida = unidade_medida,
            preco_unitario = preco_unitario_novo,
            )
        db.session.add(novo_servico)
        db.session.commit()
        flash("Serviço cadastrado com sucesso!", "success")
        return redirect(url_for("listar_servicos"))
    
    return render_template("cadastrar_servico.html")

@app.route("/servicos/editar/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def editar_servico(id):
    servico_a_editar = Servico.query.get_or_404(id)
    if request.method == "POST": 
        descricao_servico = request.form["descricao_servico"]
        detalhes_opcional = request.form["detalhes_opcional"]
        unidade_medida = request.form["unidade_medida"]
        preco_unitario = request.form["preco_unitario"]

        servico_a_editar.descricao_servico = descricao_servico
        servico_a_editar.detalhes_opcional = detalhes_opcional
        servico_a_editar.unidade_medida = unidade_medida
        servico_a_editar.preco_unitario = preco_unitario

        db.session.commit()
        flash("Serviço editado com sucesso!", "success")

        return redirect(url_for("listar_servicos"))
    
    return render_template("editar_servicos.html", servico_a_editar = servico_a_editar)

@app.route("/servicos/deletar/<int:id>")
@role_required('funcionario')
def deletar_servico(id):
    servico_a_deletar = Servico.query.get(id)
    db.session.delete(servico_a_deletar)
    db.session.commit()
    flash("Serviço apagado com sucesso!", "success")
    return redirect(url_for("listar_servicos"))

@app.route("/peca", methods = ["GET"])
@role_required('funcionario')
def listar_pecas():
    termo_busca = request.args.get("termo_busca")
    pecas_query = Peca.query

    if termo_busca:
        pecas_query = pecas_query.filter(Peca.nome_peca.ilike(f"%{termo_busca}%"))
    
    pecas = pecas_query.all()

    return render_template("listar_pecas.html", pecas = pecas)

@app.route("/peca/cadastrar", methods = ["GET", "POST"])
@role_required('funcionario')
def cadastrar_peca():
    if request.method == "POST":
        nome_peca = request.form["nome_peca"]
        detalhes_opcional = request.form["detalhes_opcional"]
        codigo_interno = request.form["codigo_interno"]
        unidade_medida = request.form["unidade_medida"]
        preco_unitario = request.form["preco_unitario"]
        if preco_unitario == "":
            preco_unitario_novo = 0.0
        else:
            preco_unitario_novo = preco_unitario.replace(",", ".")

        nova_peca = Peca(
            nome_peca=nome_peca,
            detalhes_opcional=detalhes_opcional,
            codigo_interno=codigo_interno,
            unidade_medida = unidade_medida,
            preco_unitario = preco_unitario_novo,
            )
        db.session.add(nova_peca)
        db.session.commit()
        flash("Peça cadastrada com sucesso!", "success")
        return redirect(url_for("listar_pecas"))
    
    return render_template("cadastrar_peca.html")

@app.route("/peca/editar/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def editar_peca(id):
    peca_a_editar = Peca.query.get_or_404(id)
    if request.method == "POST": 
        nome_peca = request.form["nome_peca"]
        detalhes_opcional = request.form["detalhes_opcional"]
        codigo_interno = request.form["codigo_interno"]
        unidade_medida = request.form["unidade_medida"]
        preco_unitario = request.form["preco_unitario"]

        peca_a_editar.nome_peca = nome_peca
        peca_a_editar.detalhes_opcional = detalhes_opcional
        peca_a_editar.codigo_interno = codigo_interno
        peca_a_editar.unidade_medida = unidade_medida
        peca_a_editar.preco_unitario = preco_unitario

        db.session.commit()
        flash("Peça editada com sucesso!", "success")

        return redirect(url_for("listar_pecas"))
    
    return render_template("editar_peca.html", peca_a_editar = peca_a_editar)

@app.route("/peca/deletar/<int:id>")
@role_required('funcionario')
def deletar_peca(id):
    peca_a_deletar = Peca.query.get(id)
    db.session.delete(peca_a_deletar)
    db.session.commit()
    flash("Peça apagada com sucesso!", "success")
    return redirect(url_for("listar_pecas"))

@app.route("/item/adicionar/<int:os_id>", methods=["GET", "POST"])
@role_required('funcionario')
def adicionar_servico(os_id):
    if request.method == "POST":
        quantidade = request.form["quantidade"]
        preco_cobrado = request.form["preco_cobrado"]
        
        servico_id = request.form["servico_id"]
        servico = Servico.query.get(servico_id)

        if preco_cobrado == "":
            preco_cobrado_novo = servico.preco_unitario
        else:
            preco_cobrado_novo = preco_cobrado.replace(",", ".")

        novo_item = ItemServico(
            quantidade = int(quantidade),
            preco_cobrado = float(preco_cobrado_novo),
            ordem_servico_id = os_id,
            servico_id = servico_id
        )

        db.session.add(novo_item)
        db.session.commit()
        flash("Serviço adicionado com sucesso!", "success")
        return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_servico")
    
    return render_template("detalhes_os.html")

@app.route("/item/adicionar_peca/<int:os_id>", methods=["GET", "POST"])
@role_required('funcionario')
def adicionar_peca(os_id):
    if request.method == "POST":
        quantidade = request.form["quantidade"]
        preco_cobrado = request.form["preco_cobrado"]
        
        peca_id = request.form["peca_id"]
        peca = Peca.query.get(peca_id)

        if preco_cobrado == "":
            preco_cobrado_novo = peca.preco_unitario
        else:
            preco_cobrado_novo = preco_cobrado.replace(",", ".")

        novo_item = ItemPeca(
            quantidade = int(quantidade),
            preco_cobrado = float(preco_cobrado_novo),
            ordem_servico_id = os_id,
            peca_id = peca_id
        )

        db.session.add(novo_item)
        db.session.commit()
        flash("Peça adicionado com sucesso!", "success")
        return redirect(url_for("detalhes_os", id=os_id) + "#adicionar_peca")
    
    return render_template("detalhes_os.html")

@app.route("/item/deletar/<int:id>")
@role_required('funcionario')
def remover_servico(id):
    item_a_deletar = ItemServico.query.get(id)
    os_id = item_a_deletar.ordem_servico.id
    db.session.delete(item_a_deletar)
    db.session.commit()
    flash("Serviço apagado com sucesso!", "success")
    
    return redirect(url_for("detalhes_os", id = os_id) + "#adicionar_servico")

@app.route("/item_peca/deletar/<int:id>")
@role_required('funcionario')
def remover_peca(id):
    peca_a_deletar = ItemPeca.query.get(id)
    os_id = peca_a_deletar.ordem_servico.id
    db.session.delete(peca_a_deletar)
    db.session.commit()
    flash("Peça removida com sucesso!", "success")
    return redirect(url_for("detalhes_os", id = os_id)+ "#adicionar_peca")

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
    return render_template("template_pdf.html", ordem_servico=ordem_servico)

@app.route("/os/<int:os_id>/adicionar_foto", methods=["POST"])
@login_required
@role_required('funcionario')
def adicionar_foto(os_id):
    if 'foto' not in request.files:
        return redirect(request.referrer or url_for('detalhes_os', id=os_id))
    
    file = request.files['foto']
    legenda = request.form.get('legenda', '')

    if file.filename == '' or not allowed_file(file.filename):
        return redirect(request.referrer or url_for('detalhes_os', id=os_id))

    if file:
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        original_filename = secure_filename(file.filename)
        novo_nome_arquivo = f"{timestamp}_{original_filename}"
        
        # --- ESTA É A LINHA IMPORTANTE PARA ADICIONAR ---
        # Garante que a pasta de upload exista antes de tentar salvar
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        
        # Agora a linha abaixo não dará mais erro
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], novo_nome_arquivo))

        nova_foto = Foto(
            nome_arquivo=novo_nome_arquivo,
            legenda=legenda,
            ordem_servico_id=os_id
        )
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
        # 3. Deleta o arquivo físico do servidor
        os.remove(caminho_arquivo)
        
        # 4. Deleta o registro do banco de dados
        db.session.delete(foto_a_remover)
        db.session.commit()
        flash("Foto apagada com sucesso!", "success")
    except Exception as e:
        # (Opcional) Adicionar uma mensagem de erro se algo der errado
        print(f"Erro ao deletar foto: {e}")
        db.session.rollback()

    return redirect(url_for('detalhes_os', id=os_id) + "#fotos_equipamento")

@app.route("/contato")
def pagina_contato():
    return render_template("pagina_contato.html")

@app.route("/orcamento/<int:cliente_id>/novo", methods=["POST", "GET"])
@login_required
@role_required('funcionario')
def novo_orcamento(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    
    if request.method == "POST":
        # --- Lógica para gerar o número do Orçamento ---
        ano_atual = datetime.utcnow().year
        maior_numero_orc_do_ano = db.session.query(func.max(Orcamento.numero_orcamento))\
                                    .filter_by(ano=ano_atual).scalar()        
       
        if maior_numero_orc_do_ano is None:
            proximo_numero = 1
        else:
            proximo_numero = maior_numero_orc_do_ano + 1

        validade_str = request.form.get("validade_do_orcamento") or None

        # --- Lógica principal ---
        novo_orcamento = Orcamento(
            cliente_id = cliente_id,
            numero_orcamento = proximo_numero, # Usando o número gerado
            ano=ano_atual,
            equipamento = request.form.get('equipamento'),
            marca = request.form.get('marca'),
            modelo = request.form.get('modelo'),
            numero_de_serie = request.form.get('numero_de_serie'),
            validade_do_orcamento = validade_str,
            problema_informado = request.form.get('problema_informado'),
            problema_constatado = request.form.get('problema_constatado'),
            observacoes_cliente = request.form.get('observacoes_cliente'),
            observacoes_internas = request.form.get('observacoes_internas'),
            status = request.form.get('status'),
            data_de_criacao = datetime.now().date(),
            tecnico_responsavel = request.form.get('tecnico_responsavel')
        )
        db.session.add(novo_orcamento)
        db.session.commit()
        
        flash(f"Orçamento {novo_orcamento.numero_orcamento} criado com sucesso! Agora adicione os itens.", "success")
        
        # Redireciona para uma futura página de detalhes do orçamento
        # return redirect(url_for("detalhes_orcamento", id=novo_orcamento.id))
        
        # Por enquanto, redireciona para a página do cliente
        return redirect(url_for("detalhes_cliente", id=cliente_id))

    return render_template("novo_orcamento.html", cliente = cliente)

@app.route("/orcamento/deletar/<int:id>")
@role_required('funcionario')
def deletar_orcamento(id):
    orcamento_a_deletar = Orcamento.query.get(id)
    id_do_cliente = orcamento_a_deletar.cliente.id
    db.session.delete(orcamento_a_deletar)
    db.session.commit()
    flash("Orçamento apagado com sucesso!", "success")
    return redirect(url_for("detalhes_cliente", id=id_do_cliente))

@app.route("/orcamento/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def detalhes_orcamento(id):
    orcamento = Orcamento.query.get(id)
    lista_servicos = Servico.query.all()
    lista_pecas = Peca.query.all()

    



    if request.method == "POST":
        equipamento = request.form.get('equipamento')
        marca = request.form.get('marca')
        modelo = request.form.get('modelo')
        numero_de_serie = request.form.get('numero_de_serie')
        
        problema_informado = request.form.get('problema_informado')
        problema_constatado = request.form.get('problema_constatado')
        observacoes_cliente = request.form.get('observacoes_cliente')
        observacoes_internas = request.form.get('observacoes_internas')
        status = request.form.get('status')
        tecnico_responsavel = request.form.get('tecnico_responsavel')

        orcamento.equipamento = equipamento
        orcamento.marca = marca
        orcamento.modelo = modelo
        orcamento.numero_de_serie = numero_de_serie
        orcamento.validade_do_orcamento = request.form.get("validade_do_orcamento") or None
        orcamento.problema_informado = problema_informado
        orcamento.problema_constatado = problema_constatado
        orcamento.observacoes_cliente = observacoes_cliente
        orcamento.observacoes_internas = observacoes_internas
        orcamento.status = status
        orcamento.tecnico_responsavel = tecnico_responsavel
    
        db.session.commit()

        return redirect(url_for("detalhes_cliente", id=orcamento.cliente_id))
    
    return render_template("detalhes_orcamento.html", orcamento = orcamento, lista_servicos = lista_servicos, lista_pecas=lista_pecas)



@app.route("/orcamento/item_servico/adicionar/<int:orcamento_id>", methods=["POST"])
@role_required('funcionario')
def adicionar_servico_orcamento(orcamento_id):
    """
    Rota para adicionar um item de serviço a um orçamento específico.
    """
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    
    # Pega os dados do formulário
    servico_id = request.form.get("servico_id")
    quantidade = request.form.get("quantidade", 1, type=int) # Padrão é 1
    preco_cobrado_str = request.form.get("preco_cobrado")

    # Busca o serviço no banco de dados para pegar o preço padrão
    servico = Servico.query.get(servico_id)
    if not servico:
        flash("Serviço não encontrado!", "danger")
        return redirect(url_for("detalhes_orcamento", id=orcamento_id))

    # Define o preço a ser usado
    if preco_cobrado_str:
        preco_cobrado = float(preco_cobrado_str.replace(",", "."))
    else:
        preco_cobrado = servico.preco_unitario # Usa o preço padrão do serviço

    # Cria o novo item de serviço para o orçamento
    novo_item = ItemOrcamentoServico(
        quantidade=quantidade,
        preco_cobrado=preco_cobrado,
        orcamento_id=orcamento.id,
        servico_id=servico.id
    )

    db.session.add(novo_item)
    db.session.commit()
    
    flash("Serviço adicionado ao orçamento com sucesso!", "success")
    # Redireciona de volta para a página de detalhes, focando na seção de serviços
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
    """
    Rota para adicionar um item de peça a um orçamento específico.
    """
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    
    # Pega os dados do formulário
    peca_id = request.form.get("peca_id")
    quantidade = request.form.get("quantidade", 1, type=int)
    preco_cobrado_str = request.form.get("preco_cobrado")

    # Busca a peça para pegar o preço padrão
    peca = Peca.query.get(peca_id)
    if not peca:
        flash("Peça não encontrada!", "danger")
        return redirect(url_for("detalhes_orcamento", id=orcamento_id))

    # Define o preço
    if preco_cobrado_str:
        preco_cobrado = float(preco_cobrado_str.replace(",", "."))
    else:
        preco_cobrado = peca.preco_unitario

    # Cria o novo item de peça para o orçamento
    novo_item = ItemOrcamentoPeca(
        quantidade=quantidade,
        preco_cobrado=preco_cobrado,
        orcamento_id=orcamento.id,
        peca_id=peca.id
    )

    db.session.add(novo_item)
    db.session.commit()
    
    flash("Peça adicionada ao orçamento com sucesso!", "success")
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
    if 'foto' not in request.files:
        return redirect(request.referrer)
    
    file = request.files['foto']
    legenda = request.form.get('legenda', '')

    if file.filename == '' or not allowed_file(file.filename):
        return redirect(request.referrer)

    if file:
        timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
        original_filename = secure_filename(file.filename)
        novo_nome_arquivo = f"{timestamp}_{original_filename}"
        
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], novo_nome_arquivo))

        # AQUI, conectamos a foto ao ORÇAMENTO
        nova_foto = Foto(
            nome_arquivo=novo_nome_arquivo,
            legenda=legenda,
            orcamento_id=orcamento_id # Usamos orcamento_id
        )
        db.session.add(nova_foto)
        db.session.commit()
        flash("Foto adicionada com sucesso!", "success")

    return redirect(url_for('detalhes_orcamento', id=orcamento_id) + "#adicionar-foto")


@app.route("/orcamento/<int:foto_id>/remover_foto", methods=["POST"])
@login_required
@role_required('funcionario')
def remover_foto_orcamento(foto_id):
    foto_a_remover = Foto.query.get_or_404(foto_id)
    
    # Precisamos saber para qual orçamento voltar
    orcamento_id = foto_a_remover.orcamento_id 

    caminho_arquivo = os.path.join(app.config['UPLOAD_FOLDER'], foto_a_remover.nome_arquivo)

    try:
        if os.path.exists(caminho_arquivo):
            os.remove(caminho_arquivo)
        
        db.session.delete(foto_a_remover)
        db.session.commit()
        flash("Foto apagada com sucesso!", "success")
    except Exception as e:
        print(f"Erro ao deletar foto: {e}")
        db.session.rollback()

    return redirect(url_for('detalhes_orcamento', id=orcamento_id) + "#fotos_equipamento")

# app.py

# ... (outras rotas)

@app.route("/orcamento/pdf/<int:orcamento_id>")
@login_required
@role_required('funcionario')
def gerar_pdf_orcamento(orcamento_id):
    # 1. Busca os dados do orçamento
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    
    # 2. Renderiza o template HTML específico para o PDF
    html_renderizado = render_template("template_pdf_orcamento.html", orcamento=orcamento)
    
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
    """
    Busca os dados de um orçamento e renderiza o template HTML 
    formatado para PDF, para ser exibido no navegador.
    """
    # 1. Busca os dados do orçamento
    orcamento = Orcamento.query.get_or_404(orcamento_id)
    
    # 2. Renderiza o template do PDF diretamente
    return render_template("template_pdf_orcamento.html", orcamento=orcamento)


# app.py

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
        for formacoes in curriculo.formacoes:
            db.session.delete(formacoes)

        for descricao in lista_de_descricoes:
            if descricao:
                nova_formacao = FormacaoAcademica(descricao=descricao, curriculo_id=curriculo.id)
                db.session.add(nova_formacao)

        db.session.commit()
        flash("Formações cadastradas com sucesso!", "success")

        return redirect(url_for('curriculo_passo3', curriculo_id=curriculo.id))
    if request.method == "GET":
        formacoes_atuais = curriculo.formacoes
        lista_de_descricoes = [formacao.descricao for formacao in formacoes_atuais]
        form = CurriculoPasso2Form(formacoes=lista_de_descricoes)

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
    termos_busca = request.args.get('busca', '')
    query = Curriculo.query

    if termos_busca:
        query = query.filter(Curriculo.nome.ilike(f'%{termos_busca}%'))
    
    curriculos = query.order_by(Curriculo.nome).all()
        
                               
    return render_template('listar_curriculos.html', curriculos=curriculos, termos_busca=termos_busca)


@app.route("/curriculos/deletar/<int:curriculo_id>")
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

@app.route("/contrato/deletar/<int:id>")
@login_required
@role_required('funcionario')
def deletar_contrato(id): 
    contrato_a_deletar = Contrato.query.get_or_404(id)
    db.session.delete(contrato_a_deletar)
    db.session.commit()
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

        form.cidade_foro.data = contrato.cidade_foro
        form.cidade.data = contrato.cidade
        form.data_assinatura.data = contrato.data_assinatura
    
    return render_template("novo_contrato.html", form=form, contrato=contrato)

@app.route("/contratos") # Ou a URL que você preferir
@login_required
@role_required('funcionario')
def listar_contratos():
    # 1. Pega o termo de busca (igual antes)
    termos_busca = request.args.get('busca', '')
    
    # 2. Muda a query para o modelo Contrato
    query = Contrato.query

    # 3. Muda o filtro para um campo do Contrato (ex: nome do locatário)
    if termos_busca:
        query = query.filter(Contrato.locatario_nome.ilike(f'%{termos_busca}%'))
    
    # 4. Executa a query
    contratos = query.order_by(Contrato.locatario_nome).all()
        
    # 5. Renderiza o template de listagem de contratos
    return render_template('listar_contratos.html', contratos=contratos, termos_busca=termos_busca)

if __name__ == "__main__":
    app.run(debug=True)