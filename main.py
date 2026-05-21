from flask import Flask, render_template, request, redirect, session, send_from_directory, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, date
import sqlite3
import os
import re
import shutil
from pathlib import Path
import pandas as pd

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque_esta_senha_depois")

DB_PATH = "portal.db"
UPLOAD_FOLDER = Path("uploads") / "boletos"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
INFORMES_FOLDER = Path("uploads") / "informes"
INFORMES_FOLDER.mkdir(parents=True, exist_ok=True)


def conectar():
    return sqlite3.connect(DB_PATH)


def limpar_cpf(cpf):
    if cpf is None:
        return ""

    texto = str(cpf).strip()

    if texto.lower() == "nan":
        return ""

    # Se veio como número do Excel, remove o .0
    if texto.endswith(".0"):
        texto = texto[:-2]

    return re.sub(r"\D", "", texto)


def formatar_data(valor):
    if valor is None or str(valor).strip() == "" or str(valor).lower() == "nan":
        return ""
    try:
        data = pd.to_datetime(valor)
        return data.strftime("%Y-%m-%d")
    except Exception:
        return str(valor).strip()


def login_obrigatorio_admin():
    return session.get("tipo") == "admin"


ORDEM_CONTRATO_NUMERICA = """
    CASE
        WHEN contrato IS NULL OR TRIM(contrato) = '' THEN 1
        ELSE 0
    END,
    CAST(contrato AS INTEGER),
    contrato,
    nome
"""

def obter_configuracao(chave, padrao=None):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT valor
        FROM configuracoes
        WHERE chave = ?
    """, (chave,))

    resultado = cursor.fetchone()
    conn.close()

    if resultado:
        return resultado[0]

    return padrao


def salvar_configuracao(chave, valor):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO configuracoes (chave, valor)
        VALUES (?, ?)
        ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor
    """, (chave, valor))

    conn.commit()
    conn.close()


def excluir_boletos_vencidos_automaticamente():
    excluir = obter_configuracao("excluir_boletos_vencidos", "nao")
    dias = int(obter_configuracao("dias_excluir_boletos_vencidos", "10"))

    if excluir != "sim":
        return

    hoje = date.today()

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, nome_arquivo, vencimento, status
        FROM boletos
        WHERE status != 'Pago'
          AND vencimento IS NOT NULL
          AND vencimento != ''
    """)

    boletos = cursor.fetchall()

    for boleto in boletos:
        id_boleto = boleto[0]
        nome_arquivo = boleto[1]
        vencimento = boleto[2]
        status = boleto[3]

        try:
            data_vencimento = datetime.strptime(vencimento, "%Y-%m-%d").date()
        except Exception:
            continue

        dias_vencido = (hoje - data_vencimento).days

        if dias_vencido >= dias:
            caminho = UPLOAD_FOLDER / nome_arquivo

            if caminho.exists():
                caminho.unlink()

            cursor.execute("""
                DELETE FROM boletos
                WHERE id = ?
            """, (id_boleto,))

    conn.commit()
    conn.close()

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        cpf = limpar_cpf(request.form["cpf"])
        senha = request.form["senha"]

        conn = conectar()
        cursor = conn.cursor()

        # Admin pode entrar com CPF 00000000000
        cursor.execute("""
            SELECT id, nome, cpf, usuario, senha, tipo, senha_definida
            FROM usuarios
            WHERE cpf = ?
              AND (ativo = 1 OR ativo IS NULL)
        """, (cpf,))

        usuario = cursor.fetchone()
        conn.close()

        if not usuario:
            flash("CPF não encontrado.")
            return redirect("/")

        senha_hash = usuario[4]

        if not senha_hash:
            flash("Você ainda não cadastrou sua senha. Use a opção Primeiro acesso.")
            return redirect("/primeiro_acesso")

        if check_password_hash(senha_hash, senha):
            session["id"] = usuario[0]
            session["nome"] = usuario[1]
            session["cpf"] = usuario[2]
            session["tipo"] = usuario[5]

            if usuario[5] == "admin":
                return redirect("/admin")

            return redirect("/locatario")

        flash("CPF ou senha inválidos.")
        return redirect("/")

    return render_template("login.html")


@app.route("/primeiro_acesso", methods=["GET", "POST"])
def primeiro_acesso():
    if request.method == "POST":
        cpf = limpar_cpf(request.form["cpf"])
        senha = request.form["senha"]
        confirmar = request.form["confirmar"]

        if senha != confirmar:
            flash("As senhas não conferem.")
            return redirect("/primeiro_acesso")

        if len(senha) < 6:
            flash("A senha precisa ter pelo menos 6 caracteres.")
            return redirect("/primeiro_acesso")

        conn = conectar()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, senha_definida
            FROM usuarios
            WHERE cpf = ? AND tipo = 'locatario'
              AND (ativo = 1 OR ativo IS NULL)
        """, (cpf,))

        usuario = cursor.fetchone()

        if not usuario:
            conn.close()
            flash("CPF não encontrado na base.")
            return redirect("/primeiro_acesso")

        if usuario[1] == 1:
            conn.close()
            flash("Este CPF já possui senha cadastrada. Faça login normalmente.")
            return redirect("/")

        cursor.execute("""
            UPDATE usuarios
            SET senha = ?, senha_definida = 1
            WHERE cpf = ?
        """, (generate_password_hash(senha), cpf))

        conn.commit()
        conn.close()

        flash("Senha cadastrada com sucesso. Faça login.")
        return redirect("/")

    return render_template("primeiro_acesso.html")


@app.route("/admin")
def admin():
    if not login_obrigatorio_admin():
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE tipo = 'locatario' AND (ativo = 1 OR ativo IS NULL)")
    total_locatarios = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM boletos")
    total_boletos = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM informes")
    total_informes = cursor.fetchone()[0]

    conn.close()

    return render_template(
        "admin.html",
        nome=session.get("nome"),
        total_locatarios=total_locatarios,
        total_boletos=total_boletos,
        total_informes=total_informes
    )


def carregar_dados_locatario(cpf):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nome_arquivo, valor, vencimento, status, contrato, parcela, id
        FROM boletos
        WHERE cpf_locatario = ?
        ORDER BY vencimento DESC
    """, (cpf,))
    boletos = cursor.fetchall()

    cursor.execute("""
        SELECT id, nome_arquivo, ano, data_envio
        FROM informes
        WHERE cpf_locatario = ?
        ORDER BY ano DESC
    """, (cpf,))
    informes = cursor.fetchall()

    conn.close()
    return boletos, informes


def renderizar_area_locatario(pagina):
    cpf = session.get("cpf")

    if not cpf:
        return redirect("/")

    boletos, informes = carregar_dados_locatario(cpf)

    return render_template(
        "locatario.html",
        boletos=boletos,
        informes=informes,
        nome=session.get("nome"),
        pagina=pagina
    )


@app.route("/locatario")
def locatario():
    return renderizar_area_locatario("dashboard")


@app.route("/locatario/boletos")
def locatario_boletos():
    return renderizar_area_locatario("boletos")


@app.route("/locatario/pagamentos")
def locatario_pagamentos():
    return renderizar_area_locatario("pagamentos")


@app.route("/locatario/relatorios")
def locatario_relatorios():
    return renderizar_area_locatario("relatorios")


@app.route("/cadastro_locatario", methods=["GET", "POST"])
def cadastro_locatario():
    if not login_obrigatorio_admin():
        return redirect("/")

    if request.method == "POST":
        nome = request.form["nome"].strip()
        cpf = limpar_cpf(request.form["cpf"])
        contrato = request.form.get("contrato", "").strip()

        conn = conectar()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO usuarios (nome, cpf, usuario, senha, contrato, tipo, senha_definida)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (nome, cpf, cpf, None, contrato, "locatario", 0))

            conn.commit()
            flash("Locatário cadastrado com sucesso.")
        except sqlite3.IntegrityError:
            flash("CPF já cadastrado.")

        conn.close()
        return redirect("/cadastro_locatario")

    return render_template("cadastro_locatario.html")


@app.route("/importar_clientes", methods=["GET", "POST"])
def importar_clientes():
    if not login_obrigatorio_admin():
        return redirect("/")

    if request.method == "POST":
        arquivo = request.files["arquivo"]

        if not arquivo:
            flash("Selecione uma planilha.")
            return redirect("/importar_clientes")

        df = pd.read_excel(arquivo, dtype=str)
        df.columns = [str(col).strip().upper() for col in df.columns]

        conn = conectar()
        cursor = conn.cursor()

        importados = 0
        ignorados = 0
        lista_ignorados = []

        for _, row in df.iterrows():
            nome = str(row.get("NOME", "")).strip()
            cpf = limpar_cpf(row.get("CPF", ""))
            contrato = str(row.get("CONTRATO", "")).strip().replace("nan", "")

            if not nome or not cpf:
                ignorados += 1
                lista_ignorados.append(f"{nome} - CPF inválido/vazio")
                continue

            try:
                cursor.execute("""
                    INSERT INTO usuarios (nome, cpf, usuario, senha, contrato, tipo, senha_definida)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (nome, cpf, cpf, None, contrato, "locatario", 0))

                importados += 1



            except Exception as e:

                ignorados += 1

                lista_ignorados.append(f"{nome} - {cpf} - {str(e)}")

        with open("ignorados.txt", "w", encoding="utf-8") as f:
            for item in lista_ignorados:
                f.write(item + "\n")

        conn.commit()
        conn.close()

        flash(f"Importação concluída. Importados: {importados}. Ignorados: {ignorados}.")
        return redirect("/importar_clientes")

    return render_template("importar_clientes.html")


@app.route("/clientes_admin")
def clientes_admin():
    if not login_obrigatorio_admin():
        return redirect("/")

    busca = request.args.get("busca", "").strip()

    conn = conectar()
    cursor = conn.cursor()

    if busca:
        termo = f"%{busca}%"
        cursor.execute("""
            SELECT id, nome, cpf, contrato, senha_definida, ativo
            FROM usuarios
            WHERE tipo = 'locatario'
            AND (nome LIKE ? OR cpf LIKE ? OR contrato LIKE ?)
            ORDER BY nome
        """, (termo, termo, termo))
    else:
        cursor.execute("""
            SELECT id, nome, cpf, contrato, senha_definida, ativo
            FROM usuarios
            WHERE tipo = 'locatario'
            ORDER BY nome
        """)

    clientes = cursor.fetchall()
    conn.close()

    return render_template(
        "clientes_admin.html",
        clientes=clientes,
        busca=busca
    )



@app.route("/resetar_senha/<int:id_cliente>", methods=["POST"])
def resetar_senha(id_cliente):
    if not login_obrigatorio_admin():
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE usuarios
        SET senha = NULL,
            senha_definida = 0
        WHERE id = ?
          AND tipo = 'locatario'
    """, (id_cliente,))

    conn.commit()
    conn.close()

    flash("Senha resetada. O cliente deverá usar o Primeiro acesso para criar uma nova senha.")
    return redirect("/clientes_admin")


@app.route("/alternar_cliente/<int:id_cliente>", methods=["POST"])
def alternar_cliente(id_cliente):
    if not login_obrigatorio_admin():
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ativo
        FROM usuarios
        WHERE id = ?
          AND tipo = 'locatario'
    """, (id_cliente,))

    cliente = cursor.fetchone()

    if cliente:
        ativo_atual = cliente[0]
        novo_status = 0 if ativo_atual == 1 else 1

        cursor.execute("""
            UPDATE usuarios
            SET ativo = ?
            WHERE id = ?
              AND tipo = 'locatario'
        """, (novo_status, id_cliente))

        conn.commit()

        if novo_status == 1:
            flash("Cliente reativado com sucesso.")
        else:
            flash("Cliente inativado com sucesso. Ele não conseguirá acessar o portal.")
    else:
        flash("Cliente não encontrado.")

    conn.close()
    return redirect("/clientes_admin")


@app.route("/cadastro_admin", methods=["GET", "POST"])
def cadastro_admin():
    if not login_obrigatorio_admin():
        return redirect("/")

    if request.method == "POST":
        nome = request.form["nome"].strip()
        cpf = limpar_cpf(request.form["cpf"])
        contrato = request.form.get("contrato", "").strip()
        senha = request.form["senha"]

        conn = conectar()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO usuarios (nome, cpf, usuario, senha, tipo, senha_definida)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                nome,
                cpf,
                cpf,
                generate_password_hash(senha),
                "admin",
                1
            ))

            conn.commit()
            flash("Administrador cadastrado com sucesso.")

        except sqlite3.IntegrityError:
            flash("Este CPF já está cadastrado.")

        conn.close()
        return redirect("/cadastro_admin")

    return render_template("cadastro_admin.html")


@app.route("/upload_boleto", methods=["GET", "POST"])
def upload_boleto():
    if not login_obrigatorio_admin():
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT cpf, nome, contrato
        FROM usuarios
        WHERE tipo = 'locatario'
        ORDER BY nome
    """)
    locatarios = cursor.fetchall()

    cursor.execute(f"""
        SELECT cpf, nome, contrato
        FROM usuarios
        WHERE tipo = 'locatario'
        ORDER BY {ORDEM_CONTRATO_NUMERICA}
    """)
    locatarios_por_contrato = cursor.fetchall()

    if request.method == "POST":
        cpf_locatario = limpar_cpf(request.form.get("cpf_locatario", ""))
        contrato = request.form.get("contrato", "").strip()
        parcela = request.form["parcela"].strip()
        valor = request.form["valor"].strip()
        vencimento = request.form["vencimento"]
        status = request.form["status"]
        arquivo = request.files["arquivo"]

        cliente = None

        if cpf_locatario:
            cursor.execute("SELECT nome, contrato, cpf FROM usuarios WHERE cpf = ? AND tipo = 'locatario'", (cpf_locatario,))
            cliente = cursor.fetchone()

        if not cliente and contrato:
            cursor.execute("""
                SELECT nome, contrato, cpf
                FROM usuarios
                WHERE TRIM(contrato) = ? AND tipo = 'locatario'
            """, (contrato,))
            cliente = cursor.fetchone()

            if cliente:
                cpf_locatario = cliente[2]

        if not cliente:
            conn.close()
            flash("Locatário não encontrado. Busque pelo nome, CPF ou contrato antes de enviar o boleto.")
            return redirect("/upload_boleto")

        if not contrato and cliente[1]:
            contrato = cliente[1]

        if arquivo:
            nome_seguro = secure_filename(arquivo.filename)
            nome_final = f"{cpf_locatario}_{contrato}_{parcela}_{nome_seguro}"
            caminho = UPLOAD_FOLDER / nome_final
            arquivo.save(caminho)

            cursor.execute("""
                INSERT INTO boletos
                (nome_arquivo, cpf_locatario, nome_locatario, contrato, parcela, valor, vencimento, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                nome_final,
                cpf_locatario,
                cliente[0],
                contrato,
                parcela,
                valor,
                vencimento,
                status
            ))

            conn.commit()
            conn.close()

            flash("Boleto enviado com sucesso.")
            return redirect("/upload_boleto")

    conn.close()

    return render_template(
        "upload_boleto.html",
        locatarios=locatarios,
        locatarios_por_contrato=locatarios_por_contrato
    )


@app.route("/importar_boletos", methods=["GET", "POST"])
def importar_boletos():
    if not login_obrigatorio_admin():
        return redirect("/")

    if request.method == "POST":
        arquivo = request.files["arquivo"]

        if not arquivo:
            flash("Selecione uma planilha.")
            return redirect("/importar_boletos")

        df = pd.read_excel(arquivo)

        conn = conectar()
        cursor = conn.cursor()

        importados = 0
        ignorados = 0
        lista_ignorados = []

        for _, row in df.iterrows():
            nome = str(row.get("Nome", "")).strip()
            cpf = limpar_cpf(row.get("CPF", ""))
            contrato = str(row.get("Contrato", "")).strip()
            parcela = str(row.get("Parcela", "")).strip()
            vencimento = formatar_data(row.get("Vencimento", ""))
            valor = str(row.get("Valor", "")).strip().replace("nan", "")
            caminho_boleto = str(row.get("caminho do boleto", "")).strip()

            cursor.execute("SELECT nome FROM usuarios WHERE cpf = ?", (cpf,))
            cliente = cursor.fetchone()

            if not cliente or not caminho_boleto or not Path(caminho_boleto).exists():
                ignorados += 1
                continue

            origem = Path(caminho_boleto)
            nome_seguro = secure_filename(origem.name)
            nome_final = f"{cpf}_{contrato}_{parcela}_{nome_seguro}"
            destino = UPLOAD_FOLDER / nome_final

            try:
                shutil.copy2(origem, destino)

                cursor.execute("""
                    INSERT INTO boletos
                    (nome_arquivo, cpf_locatario, nome_locatario, contrato, parcela, vencimento, valor, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    nome_final,
                    cpf,
                    cliente[0],
                    contrato,
                    parcela,
                    vencimento,
                    valor,
                    "Em aberto"
                ))

                importados += 1

            except Exception:
                ignorados += 1

        conn.commit()
        conn.close()

        flash(f"Importação concluída. Importados: {importados}. Ignorados: {ignorados}.")
        return redirect("/importar_boletos")

    return render_template("importar_boletos.html")

@app.route("/upload_informe", methods=["GET", "POST"])
def upload_informe():
    if not login_obrigatorio_admin():
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT cpf, nome, contrato
        FROM usuarios
        WHERE tipo = 'locatario'
        ORDER BY nome
    """)
    locatarios = cursor.fetchall()

    cursor.execute(f"""
        SELECT cpf, nome, contrato
        FROM usuarios
        WHERE tipo = 'locatario'
        ORDER BY {ORDEM_CONTRATO_NUMERICA}
    """)
    locatarios_por_contrato = cursor.fetchall()

    if request.method == "POST":
        cpf_locatario = limpar_cpf(request.form["cpf_locatario"])
        ano = request.form["ano"].strip()
        arquivo = request.files["arquivo"]

        cursor.execute("SELECT nome FROM usuarios WHERE cpf = ?", (cpf_locatario,))
        cliente = cursor.fetchone()

        if not cliente:
            conn.close()
            flash("CPF do locatário não encontrado.")
            return redirect("/upload_informe")

        if arquivo:
            nome_seguro = secure_filename(arquivo.filename)
            nome_final = f"{cpf_locatario}_INFORME_{ano}_{nome_seguro}"
            caminho = INFORMES_FOLDER / nome_final
            arquivo.save(caminho)

            cursor.execute("""
                INSERT INTO informes
                (cpf_locatario, nome_locatario, nome_arquivo, ano, data_envio)
                VALUES (?, ?, ?, ?, date('now'))
            """, (
                cpf_locatario,
                cliente[0],
                nome_final,
                ano
            ))

            conn.commit()
            conn.close()

            flash("Informe enviado com sucesso.")
            return redirect("/upload_informe")

    conn.close()

    return render_template(
        "upload_informe.html",
        locatarios=locatarios,
        locatarios_por_contrato=locatarios_por_contrato
    )


@app.route("/informes_admin")
def informes_admin():
    if not login_obrigatorio_admin():
        return redirect("/")

    busca = request.args.get("busca", "").strip()

    conn = conectar()
    cursor = conn.cursor()

    if busca:
        termo = f"%{busca}%"
        cursor.execute("""
            SELECT id, nome_arquivo, nome_locatario, cpf_locatario, ano, data_envio
            FROM informes
            WHERE nome_locatario LIKE ? OR cpf_locatario LIKE ? OR ano LIKE ?
            ORDER BY id DESC
        """, (termo, termo, termo))
    else:
        cursor.execute("""
            SELECT id, nome_arquivo, nome_locatario, cpf_locatario, ano, data_envio
            FROM informes
            ORDER BY id DESC
        """)

    informes = cursor.fetchall()
    conn.close()

    return render_template("informes_admin.html", informes=informes, busca=busca)


@app.route("/excluir_informe/<int:id_informe>")
def excluir_informe(id_informe):
    if not login_obrigatorio_admin():
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("SELECT nome_arquivo FROM informes WHERE id = ?", (id_informe,))
    informe = cursor.fetchone()

    if informe:
        caminho = INFORMES_FOLDER / informe[0]
        if caminho.exists():
            caminho.unlink()

        cursor.execute("DELETE FROM informes WHERE id = ?", (id_informe,))
        conn.commit()
        flash("Informe excluído com sucesso.")
    else:
        flash("Informe não encontrado.")

    conn.close()
    return redirect("/informes_admin")

@app.route("/boletos_admin")
def boletos_admin():
    if not login_obrigatorio_admin():
        return redirect("/")

    excluir_boletos_vencidos_automaticamente()

    busca = request.args.get("busca", "").strip()

    conn = conectar()
    cursor = conn.cursor()

    if busca:
        termo = f"%{busca}%"
        cursor.execute("""
            SELECT id, nome_arquivo, nome_locatario, valor, vencimento, status, contrato, parcela
            FROM boletos
            WHERE nome_locatario LIKE ? OR cpf_locatario LIKE ? OR contrato LIKE ?
            ORDER BY id DESC
        """, (termo, termo, termo))
    else:
        cursor.execute("""
            SELECT id, nome_arquivo, nome_locatario, valor, vencimento, status, contrato, parcela
            FROM boletos
            ORDER BY id DESC
        """)

    boletos = cursor.fetchall()

    total_pagos = sum(1 for b in boletos if b[5] == "Pago")
    total_vencidos = sum(1 for b in boletos if b[5] == "Vencido")
    total_abertos = len(boletos) - total_pagos - total_vencidos

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
    if not login_obrigatorio_admin():
        return redirect("/")

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


@app.route("/baixar_boleto/<int:id_boleto>")
def baixar_boleto(id_boleto):
    cpf = session.get("cpf")
    tipo = session.get("tipo")

    if not cpf:
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    if tipo == "admin":
        cursor.execute("""
            SELECT nome_arquivo
            FROM boletos
            WHERE id = ?
        """, (id_boleto,))
    else:
        cursor.execute("""
            SELECT nome_arquivo
            FROM boletos
            WHERE id = ? AND cpf_locatario = ?
        """, (id_boleto, cpf))

    boleto = cursor.fetchone()
    conn.close()

    if not boleto:
        return "Acesso negado ou boleto não encontrado.", 403

    return send_from_directory(
        UPLOAD_FOLDER,
        boleto[0],
        as_attachment=False
    )

@app.route("/excluir_boleto/<int:id_boleto>")
def excluir_boleto(id_boleto):
    if not login_obrigatorio_admin():
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nome_arquivo
        FROM boletos
        WHERE id = ?
    """, (id_boleto,))

    boleto = cursor.fetchone()

    if boleto:
        caminho = UPLOAD_FOLDER / boleto[0]

        if caminho.exists():
            caminho.unlink()

        cursor.execute("""
            DELETE FROM boletos
            WHERE id = ?
        """, (id_boleto,))

        conn.commit()
        flash("Boleto excluído com sucesso.")
    else:
        flash("Boleto não encontrado.")

    conn.close()
    return redirect("/boletos_admin")


@app.route("/baixar_informe/<int:id_informe>")
def baixar_informe(id_informe):
    cpf = session.get("cpf")
    tipo = session.get("tipo")

    if not cpf:
        return redirect("/")

    conn = conectar()
    cursor = conn.cursor()

    if tipo == "admin":
        cursor.execute("""
            SELECT nome_arquivo
            FROM informes
            WHERE id = ?
        """, (id_informe,))
    else:
        cursor.execute("""
            SELECT nome_arquivo
            FROM informes
            WHERE id = ? AND cpf_locatario = ?
        """, (id_informe, cpf))

    informe = cursor.fetchone()
    conn.close()

    if not informe:
        return "Acesso negado ou informe não encontrado.", 403

    return send_from_directory(
        INFORMES_FOLDER,
        informe[0],
        as_attachment=False
    )


@app.route("/configuracoes", methods=["GET", "POST"])
def configuracoes():
    if not login_obrigatorio_admin():
        return redirect("/")

    if request.method == "POST":
        excluir = request.form.get("excluir_boletos_vencidos", "nao")
        dias = request.form.get("dias_excluir_boletos_vencidos", "10")

        salvar_configuracao("excluir_boletos_vencidos", excluir)
        salvar_configuracao("dias_excluir_boletos_vencidos", dias)

        flash("Configurações salvas com sucesso.")
        return redirect("/configuracoes")

    excluir = obter_configuracao("excluir_boletos_vencidos", "nao")
    dias = obter_configuracao("dias_excluir_boletos_vencidos", "10")

    return render_template(
        "configuracoes.html",
        excluir=excluir,
        dias=dias
    )

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    # Garante criação das tabelas ao rodar localmente
    try:
        import database
        database.criar_tabelas()
    except Exception:
        pass

    app.run(debug=True)
