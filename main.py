from flask import Flask, render_template, request, redirect, session, send_from_directory, flash
import sqlite3
import os

app = Flask(__name__)
app.secret_key = "troque_esta_senha_depois"

def conectar():
    return sqlite3.connect("portal.db")


@app.route("/", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        usuario = request.form["usuario"]
        senha = request.form["senha"]

        conn = conectar()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM usuarios
            WHERE usuario = ? AND senha = ?
        """, (usuario, senha))

        usuario_encontrado = cursor.fetchone()

        conn.close()

        if usuario_encontrado:

            session["nome"] = usuario_encontrado[1]
            session["usuario"] = usuario_encontrado[2]
            session["tipo"] = usuario_encontrado[4]

            tipo = usuario_encontrado[4]

            if tipo == "admin":
                return redirect("/admin")
            else:
                return redirect("/locatario")
        else:
            return "Usuário ou senha inválidos"

    return render_template("login.html")


@app.route("/admin")
def admin():

    if session.get("tipo") != "admin":
        return redirect("/")

    nome = session.get("nome")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*)
        FROM usuarios
        WHERE tipo = 'locatario'
    """)
    total_locatarios = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM boletos
    """)
    total_boletos = cursor.fetchone()[0]

    conn.close()

    return render_template(
        "admin.html",
        nome=nome,
        total_locatarios=total_locatarios,
        total_boletos=total_boletos
    )

@app.route("/cadastro_locatario", methods=["GET", "POST"])
def cadastro_locatario():

    if request.method == "POST":

        nome = request.form["nome"]
        usuario = request.form["usuario"]
        senha = request.form["senha"]

        conn = conectar()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO usuarios
            (nome, usuario, senha, tipo)

            VALUES (?, ?, ?, ?)
        """, (
            nome,
            usuario,
            senha,
            "locatario"
        ))

        conn.commit()
        conn.close()

        return "Locatário cadastrado com sucesso!"

    return render_template("cadastro_locatario.html")

@app.route("/locatario")
def locatario():

    usuario = session.get("usuario")
    nome = session.get("nome")

    if not usuario:
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nome_arquivo, valor, vencimento, status
        FROM boletos
        WHERE usuario_locatario = ?
    """, (usuario,))

    boletos = cursor.fetchall()

    conn.close()

    return render_template(
        "locatario.html",
        boletos=boletos,
        nome=nome
    )

@app.route("/upload_boleto", methods=["GET", "POST"])
def upload_boleto():

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT usuario, nome
        FROM usuarios
        WHERE tipo = 'locatario'
        ORDER BY nome
    """)
    locatarios = cursor.fetchall()

    if request.method == "POST":
        usuario_locatario = request.form["usuario_locatario"]
        valor = request.form["valor"]
        vencimento = request.form["vencimento"]
        status = request.form["status"]
        arquivo = request.files["arquivo"]

        if arquivo:
            nome_arquivo = arquivo.filename
            caminho = os.path.join("uploads", "boletos", nome_arquivo)
            arquivo.save(caminho)

            cursor.execute("""
                INSERT INTO boletos
                (nome_arquivo, usuario_locatario, valor, vencimento, status)
                VALUES (?, ?, ?, ?, ?)
            """, (nome_arquivo, usuario_locatario, valor, vencimento, status))

            conn.commit()
            conn.close()

            flash("✅ Boleto enviado com sucesso!")
            return redirect("/upload_boleto")

    conn.close()

    return render_template(
        "upload_boleto.html",
        locatarios=locatarios
    )


@app.route("/baixar_boleto/<nome_arquivo>")
def baixar_boleto(nome_arquivo):
    return send_from_directory(
        "uploads/boletos",
        nome_arquivo,
        as_attachment=False
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/boletos_admin")
def boletos_admin():

    if session.get("tipo") != "admin":
        return redirect("/")

    busca = request.args.get("busca", "")

    conn = conectar()
    cursor = conn.cursor()

    if busca:

        cursor.execute("""
            SELECT *
            FROM boletos
            WHERE usuario_locatario LIKE ?
            ORDER BY id DESC
        """, (f"%{busca}%",))

    else:

        cursor.execute("""
            SELECT *
            FROM boletos
            ORDER BY id DESC
        """)

    boletos = cursor.fetchall()

    total_pagos = 0
    total_abertos = 0
    total_vencidos = 0

    for boleto in boletos:

        status = boleto[5]

        if status == "Pago":
            total_pagos += 1

        elif status == "Vencido":
            total_vencidos += 1

        else:
            total_abertos += 1

    conn.close()

    return render_template(
        "boletos_admin.html",
        boletos=boletos,
        total_pagos=total_pagos,
        total_abertos=total_abertos,
        total_vencidos=total_vencidos,
        busca=busca
    )
@app.route("/marcar_pago/<int:id_boleto>")
def marcar_pago(id_boleto):

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE boletos
        SET status = 'Pago'
        WHERE id = ?
    """, (id_boleto,))

    conn.commit()
    conn.close()

    return redirect("/boletos_admin")


if __name__ == "__main__":
    app.run(debug=True)