from enum import unique
from http import client
from os import replace
from sqlite3.dbapi2 import Timestamp
from flask import Flask, render_template, request, redirect, url_for, abort, Response, flash
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
    # In class OrdemServico:
    cliente = db.relationship('Cliente', back_populates='ordens_servico')

    #criação do numero da os
    numero_sequencial = db.Column(db.Integer, nullable = True)
    ano = db.Column(db.Integer, nullable = True)

    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable = False)
    #validade_do_orcamento = db.Column(db.String(100))
    #prazo_de_execucao = db.Column(db.String(100))
    marca = db.Column(db.String(100))
    modelo = db.Column(db.String(100))
    equipamento = db.Column(db.String(150), nullable = False)
    #numero_de_serie = db.Column(db.String(100))
    defeito = db.Column(db.Text, nullable = False)
    #observacoes = db.Column(db.Text)
    status = db.Column(db.String(50), nullable = False)
    #valor_total = db.Column(db.Float)
    data_de_criacao = db.Column(db.DateTime, nullable = False, default = datetime.now)
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
    ordem_servico_id = db.Column(db.Integer, db.ForeignKey('ordem_servico.id'))


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
    return redirect(url_for("login_cliente"))

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
    
@app.route("/clientes")
@role_required('funcionario')
def listar_clientes():
    clientes = Cliente.query.all()
    return render_template("listar_clientes.html", clientes = clientes)

@app.route("/cliente/<int:id>")
@role_required('funcionario')
def detalhes_cliente(id):
    cliente_a_detalhar = Cliente.query.get(id)
    ordens_servico = cliente_a_detalhar.ordens_servico
    return render_template("detalhes_cliente.html", cliente_a_detalhar = cliente_a_detalhar, ordens_servico = ordens_servico)

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

@app.route("/os/<int:id>", methods=["GET", "POST"])
@role_required('funcionario')
def detalhes_os(id):
    ordem_servico = OrdemServico.query.get(id)
    lista_servicos = Servico.query.all()
    lista_pecas = Peca.query.all()

    if request.method == "POST":
        equipamento = request.form["equipamento"]
        marca = request.form["marca"]
        modelo = request.form["modelo"]
        defeito = request.form["defeito"]
        status = request.form["status"]

        ordem_servico.equipamento = equipamento
        ordem_servico.marca = marca
        ordem_servico.modelo = modelo
        ordem_servico.defeito = defeito
        ordem_servico.status = status
    
        db.session.commit()

        return redirect(url_for("detalhes_cliente", id=ordem_servico.cliente_id))
    
    return render_template("detalhes_os.html", ordem_servico = ordem_servico, lista_servicos = lista_servicos, lista_pecas=lista_pecas)

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
    servicos = Servico.query.all()

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

@app.route("/peca")
@role_required('funcionario')
def listar_pecas():
    pecas = Peca.query.all()

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

if __name__ == "__main__":
    app.run(debug=True)