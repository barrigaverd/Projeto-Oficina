from flask import Flask
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
db = SQLAlchemy(app)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    nome = db.Column(db.String(100), nullable=False)
    telefone_celular = db.Column(db.String(20), nullable = False)

@app.route("/")
def home():
    return "Ola Mundo"

if __name__ == "__main__":
    app.run(debug=True)