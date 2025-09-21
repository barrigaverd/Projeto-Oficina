from http import client
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from flask_migrate import Migrate
from datetime import datetime
import click

app = Flask(__name__)
app.config['SECRET_KEY'] = '0625fa577ac24b41fd655e4935191fb6'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
db = SQLAlchemy(app)
migrate = Migrate(app, db)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

@app.cli.command("create-user")  # Define o nome do comando no terminal
@click.argument("username")      # Define o primeiro argumento que o comando espera
@click.argument("password")      # Define o segundo argumento
def create_user(username, password):
    user = Usuario.query.filter_by(username=username).first()
    if user:
        print("Erro: Usuário já existe!")
        return
    else:
        password_hash = bcrypt.generate_password_hash(password)
        novo_usuario = Usuario(username = username, password_hash = password_hash)
        db.session.add(novo_usuario)
        db.session.commit()
        print(f"Usuário {username} criado com sucesso!")

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

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    #Contato
    nome = db.Column(db.String(100), nullable=False)
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


class OrdemServico(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    cliente = db.relationship('Cliente', backref='ordens_servico')
    #numero_os = db.Column(db.Integer, nullable = False)
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

class Usuario(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key = True)
    username = db.Column(db.String(80), nullable = False, unique = True)
    password_hash = db.Column(db.String(128), nullable = False)

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/login", methods = ("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        user = Usuario.query.filter_by(username=username).first()

        if user and bcrypt.check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('home'))

    return render_template('login.html')

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/dashboard")
@login_required
def home():
    return render_template("home.html")

@app.route("/clientes/cadastrar", methods=["GET", "POST"])
@login_required
def cadastrar_cliente():
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
        novo_cliente = Cliente(
            nome=nome_cliente,
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
        return redirect(url_for("listar_clientes"))

    return render_template("cadastrar_cliente.html")

@app.route("/clientes/deletar/<int:id>")
@login_required
def deletar_cliente(id):
    cliente_a_deletar = Cliente.query.get(id)
    db.session.delete(cliente_a_deletar)
    db.session.commit()
    return redirect(url_for("listar_clientes"))

@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
@login_required
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

        return redirect(url_for("listar_clientes"))
    
    return render_template("editar_cliente.html", cliente_a_editar = cliente_a_editar)
    

@app.route("/clientes")
@login_required
def listar_clientes():
    clientes = Cliente.query.all()
    return render_template("listar_clientes.html", clientes = clientes)

@app.route("/cliente/<int:id>")
@login_required
def detalhes_cliente(id):
    cliente_a_detalhar = Cliente.query.get(id)
    ordens_servico = cliente_a_detalhar.ordens_servico
    return render_template("detalhes_cliente.html", cliente_a_detalhar = cliente_a_detalhar, ordens_servico = ordens_servico)

@app.route("/cliente/<int:cliente_id>/os/cadastrar", methods = ["GET", "POST"])
@login_required
def cadastrar_os(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    if request.method == "POST":
        equipamento = request.form["equipamento"]
        marca = request.form["marca"]
        modelo = request.form["modelo"]
        defeito = request.form["defeito"]
        status = request.form["status"]

        nova_os = OrdemServico(
            cliente_id = cliente_id,
            equipamento=equipamento,
            marca = marca,
            modelo = modelo,
            defeito = defeito,
            status = status
            )
        db.session.add(nova_os)
        db.session.commit()
        return redirect(url_for("detalhes_cliente", id=cliente_id))

    return render_template("cadastrar_os.html", cliente = cliente)

@app.route("/os/<int:id>", methods=["GET", "POST"])
@login_required
def detalhes_os(id):
    ordem_servico = OrdemServico.query.get(id)
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
    
    return render_template("detalhes_os.html", ordem_servico = ordem_servico)

@app.route("/os/deletar/<int:id>")
@login_required
def deletar_os(id):
    os_a_deletar = OrdemServico.query.get(id)
    id_do_cliente = os_a_deletar.cliente.id
    db.session.delete(os_a_deletar)
    db.session.commit()
    return redirect(url_for("detalhes_cliente", id=id_do_cliente))

if __name__ == "__main__":
    app.run(debug=True)