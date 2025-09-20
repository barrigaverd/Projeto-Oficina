from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
db = SQLAlchemy(app)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    nome = db.Column(db.String(100), nullable=False)
    telefone_celular = db.Column(db.String(20), nullable = False)

class OrdemServico(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    numero_os = db.Column(db.Integer, nullable = False)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable = False)
    validade_do_orcamento = db.Column(db.String(100))
    prazo_de_execucao = db.Column(db.String(100))
    marca = db.Column(db.String(100))
    modelo = db.Column(db.String(100))
    equipamento = db.Column(db.String(150), nullable = False)
    numero_de_serie = db.Column(db.String(100))
    defeito = db.Column(db.Text, nullable = False)
    observacoes = db.Column(db.Text)
    status = db.Column(db.String(50), nullable = False)
    valor_total = db.Column(db.Float)
    data_de_criacao = db.Column(db.DateTime, nullable = False, default = datetime.now)

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/clientes/cadastrar", methods=["GET", "POST"])
def cadastrar_cliente():
    if request.method == "POST":
        nome_cliente = request.form["nome"]
        telefone_cliente = request.form["telefone"]
        novo_cliente = Cliente(nome=nome_cliente, telefone_celular=telefone_cliente)
        db.session.add(novo_cliente)
        db.session.commit()
        return redirect(url_for("home"))

    return render_template("cadastrar_cliente.html")

@app.route("/clientes/deletar/<int:id>")
def deletar_cliente(id):
    cliente_a_deletar = Cliente.query.get(id)
    db.session.delete(cliente_a_deletar)
    db.session.commit()
    return redirect(url_for("listar_clientes"))

@app.route("/clientes/editar/<int:id>", methods=["GET", "POST"])
def editar_cliente(id):
    cliente_a_editar = Cliente.query.get_or_404(id)
    if request.method == "POST": 
        nome_cliente = request.form["nome"]
        telefone_cliente = request.form["telefone"]
        cliente_a_editar.nome = nome_cliente
        cliente_a_editar.telefone_celular = telefone_cliente
        db.session.commit()

        return redirect(url_for("listar_clientes"))
    
    return render_template("editar_cliente.html", cliente_a_editar = cliente_a_editar)
    

@app.route("/clientes")
def listar_clientes():
    clientes = Cliente.query.all()
    return render_template("listar_clientes.html", clientes = clientes)

@app.route("/cliente/<int:id>")
def detalhes_cliente(id):
    cliente_a_detalhar = Cliente.query.get(id)
    return render_template("detalhes_cliente.html", cliente_a_detalhar = cliente_a_detalhar)



if __name__ == "__main__":
    app.run(debug=True)