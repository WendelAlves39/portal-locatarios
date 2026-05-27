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

from services.uploads import validar_pdf, validar_excel, nome_disponivel

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque_esta_senha_depois")

DB_PATH = "portal.db"
UPLOAD_FOLDER = Path("uploads") / "boletos"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
INFORMES_FOLDER = Path("uploads") / "informes"
INFORMES_FOLDER.mkdir(parents=True, exist_ok=True)
DEMONSTRATIVOS_LOCADOR_FOLDER = Path("uploads") / "demonstrativos_locador"
DEMONSTRATIVOS_LOCADOR_FOLDER.mkdir(parents=True, exist_ok=True)
INFORMES_LOCADOR_FOLDER = Path("uploads") / "informes_locador"
INFORMES_LOCADOR_FOLDER.mkdir(parents=True, exist_ok=True)

# Garante que tabelas novas também sejam criadas ao subir em produção.
try:
    import database
    database.criar_tabelas()
except Exception as erro:
    print(f"Aviso: não foi possível atualizar/criar tabelas automaticamente: {erro}")


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


def normalizar_coluna(nome):
    texto = str(nome or "").strip().upper()
    mapa = str.maketrans({
        "Á": "A", "À": "A", "Â": "A", "Ã": "A", "Ä": "A",
        "É": "E", "È": "E", "Ê": "E", "Ë": "E",
        "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
        "Ó": "O", "Ò": "O", "Ô": "O", "Õ": "O", "Ö": "O",
        "Ú": "U", "Ù": "U", "Û": "U", "Ü": "U",
        "Ç": "C",
    })
    texto = texto.translate(mapa)
    texto = re.sub(r"[^A-Z0-9]+", "_", texto)
    texto = re.sub(r"_+", "_", texto).strip("_")
    return texto


def formatar_data(valor):
    if valor is None:
        return ""

    texto = str(valor).strip()
    if not texto or texto.lower() in ("nan", "nat", "none"):
        return ""

    # Datas vindas do Excel às vezes chegam como número serial, por exemplo:
    # 42116 = 22/04/2015. O pandas sozinho pode interpretar isso errado
    # dependendo do tipo recebido, então tratamos esse caso explicitamente.
    try:
        numero_excel = float(texto.replace(",", "."))
        if 20000 <= numero_excel <= 60000:
            data_excel = pd.to_datetime(numero_excel, unit="D", origin="1899-12-30", errors="coerce")
            if not pd.isna(data_excel):
                return data_excel.strftime("%Y-%m-%d")
    except Exception:
        pass

    try:
        data = pd.to_datetime(valor, dayfirst=True, errors="coerce")
        if pd.isna(data):
            return texto
        return data.strftime("%Y-%m-%d")
    except Exception:
        return texto


def formatar_data_br(valor):
    if not valor:
        return ""
    try:
        return datetime.strptime(str(valor), "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return str(valor)


def texto_planilha(row, *nomes):
    # As planilhas podem vir com acentos, espaços ou nomes ligeiramente diferentes.
    # Normalizamos os cabeçalhos para evitar que campos como ENDEREÇO fiquem vazios.
    for nome in nomes:
        chave = normalizar_coluna(nome)
        valor = row.get(chave, "")
        if valor is not None:
            texto = str(valor).strip()
            if texto and texto.lower() not in ("nan", "nat", "none"):
                return texto
    return ""


def login_obrigatorio_admin():
    return session.get("tipo") == "admin"


def normalizar_numero_texto(valor):
    if valor is None:
        return ""

    texto = str(valor).strip()
    if not texto or texto.lower() in ("nan", "none"):
        return ""

    # Quando o Excel entrega 11877.0, não podemos transformar em 118770.
    try:
        if re.fullmatch(r"\d+\.0+", texto):
            texto = texto.split(".")[0]
    except Exception:
        pass

    texto = re.sub(r"\D", "", texto)
    if not texto:
        return ""
    return str(int(texto))


def valor_br_para_centavos(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.replace("R$", "").replace(" ", "")
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return int(round(float(texto) * 100))
    except Exception:
        return None


def extrair_baixas_recebimentos_txt(arquivo_storage):
    bruto = arquivo_storage.read()
    texto = None

    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            texto = bruto.decode(encoding)
            break
        except Exception:
            pass

    if texto is None:
        texto = bruto.decode("latin-1", errors="ignore")

    # Remove comandos de impressão do relatório, preservando as linhas úteis.
    texto = re.sub(r"<[^>]*>", "", texto)
    texto = texto.replace(":inipag", "").replace(":fimpag", "")

    data_baixa = ""
    m_data = re.search(r"Recebimentos\s+de\s+(\d{2}/\d{2}/\d{4})\s+a\s+\d{2}/\d{2}/\d{4}", texto)
    if m_data:
        data_baixa = datetime.strptime(m_data.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")

    baixas = []
    tipo_recebimento = ""

    padrao_linha = re.compile(
        r"^(.{5,40}?)\s+(\d{4,6})\s+(\d{1,4})\s+([\d\.]+,\d{2})(.*)$"
    )

    for linha in texto.splitlines():
        linha_original = linha.rstrip("\n")
        linha_limpa = linha_original.strip()

        if not linha_limpa:
            continue

        linha_upper = linha_limpa.upper()

        if "RECEBIMENTOS FEITOS EM BANCO" in linha_upper:
            tipo_recebimento = "Banco"
            continue

        if "RECEBIMENTOS FEITOS EM CAIXA" in linha_upper:
            tipo_recebimento = "Caixa"
            continue

        if linha_upper.startswith("TOTAL") or "TOTAL EM" in linha_upper or "TOTAL GERAL" in linha_upper:
            continue

        if "RESUMO POR" in linha_upper:
            tipo_recebimento = ""
            continue

        m = padrao_linha.match(linha_original)
        if not m:
            continue

        nome = m.group(1).strip()
        contrato = m.group(2).strip()
        parcela = m.group(3).strip()
        valor_a_pagar = m.group(4).strip()
        restante = m.group(5)

        valores = re.findall(r"[\d\.]+,\d{2}", restante)
        valor_pago = valores[-1] if valores else valor_a_pagar

        baixas.append({
            "nome": nome,
            "contrato": contrato,
            "parcela": parcela,
            "valor_a_pagar": valor_a_pagar,
            "valor_pago": valor_pago,
            "data_baixa": data_baixa,
            "tipo_recebimento": tipo_recebimento,
        })

    return baixas, data_baixa



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
        perfil = request.form.get("perfil", "locatario")

        conn = conectar()
        cursor = conn.cursor()

        if cpf == "00000000000":
            cursor.execute("""
                SELECT id, nome, cpf, usuario, senha, tipo, senha_definida
                FROM usuarios
                WHERE cpf = ?
                  AND tipo = 'admin'
                  AND (ativo = 1 OR ativo IS NULL)
            """, (cpf,))
        else:
            cursor.execute("""
                SELECT id, nome, cpf, usuario, senha, tipo, senha_definida
                FROM usuarios
                WHERE cpf = ?
                  AND tipo = ?
                  AND (ativo = 1 OR ativo IS NULL)
            """, (cpf, perfil))

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
            if usuario[5] == "locador":
                return redirect("/locador")

            return redirect("/locatario")

        flash("CPF ou senha inválidos.")
        return redirect("/")

    return render_template("login.html")


@app.route("/primeiro_acesso", methods=["GET", "POST"])
def primeiro_acesso():
    if request.method == "POST":
        cpf = limpar_cpf(request.form["cpf"])
        perfil = request.form.get("perfil", "locatario")
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
            WHERE cpf = ? AND tipo = ?
              AND (ativo = 1 OR ativo IS NULL)
        """, (cpf, perfil))

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
            WHERE cpf = ? AND tipo = ?
        """, (generate_password_hash(senha), cpf, perfil))

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

    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE tipo = 'locador' AND (ativo = 1 OR ativo IS NULL)")
    total_locadores = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM boletos")
    total_boletos = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM informes")
    total_informes = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM contratos WHERE COALESCE(status, 'Ativo') = 'Ativo'")
    total_contratos = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM demonstrativos_locador")
    total_demonstrativos_locador = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM contratos_locador WHERE COALESCE(status, 'Ativo') = 'Ativo'")
    total_contratos_locador = cursor.fetchone()[0]

    conn.close()

    return render_template(
        "admin.html",
        nome=session.get("nome"),
        total_locatarios=total_locatarios,
        total_boletos=total_boletos,
        total_informes=total_informes,
        total_contratos=total_contratos,
        total_locadores=total_locadores,
        total_demonstrativos_locador=total_demonstrativos_locador,
        total_contratos_locador=total_contratos_locador
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

    cursor.execute("""
        SELECT contrato
        FROM usuarios
        WHERE cpf = ? AND tipo = 'locatario'
        LIMIT 1
    """, (cpf,))
    usuario = cursor.fetchone()

    contrato_usuario = usuario[0].strip() if usuario and usuario[0] else ""
    contrato_normalizado = normalizar_numero_texto(contrato_usuario)

    # Busca primeiro pelo CPF, pois é o vínculo mais confiável.
    # Depois, usa o contrato do cadastro do usuário como segunda opção.
    cursor.execute("""
        SELECT
            codigo_contrato,
            COALESCE(NULLIF(TRIM(endereco_imovel), ''), '') AS endereco_imovel,
            COALESCE(NULLIF(TRIM(inicio_vigencia), ''), '') AS inicio_vigencia,
            COALESCE(NULLIF(TRIM(fim_vigencia), ''), '') AS fim_vigencia,
            COALESCE(NULLIF(TRIM(status), ''), 'Ativo') AS status
        FROM contratos
        WHERE
            REPLACE(REPLACE(REPLACE(COALESCE(cpf_locatario, ''), '.', ''), '-', ''), '/', '') = ?
            OR (
                ? <> ''
                AND CAST(
                    CASE
                        WHEN REPLACE(REPLACE(REPLACE(COALESCE(codigo_contrato, ''), '.', ''), '-', ''), '/', '') GLOB '[0-9]*'
                        THEN REPLACE(REPLACE(REPLACE(COALESCE(codigo_contrato, ''), '.', ''), '-', ''), '/', '')
                        ELSE '0'
                    END AS INTEGER
                ) = CAST(? AS INTEGER)
            )
        ORDER BY
            CASE
                WHEN COALESCE(NULLIF(TRIM(endereco_imovel), ''), '') <> ''
                 AND COALESCE(NULLIF(TRIM(inicio_vigencia), ''), '') <> ''
                 AND COALESCE(NULLIF(TRIM(fim_vigencia), ''), '') <> ''
                THEN 0
                ELSE 1
            END,
            data_importacao DESC,
            id DESC
    """, (cpf, contrato_normalizado, contrato_normalizado or "0"))

    linhas = cursor.fetchall()
    contratos = []
    usados = set()

    for codigo, endereco, inicio, fim, status in linhas:
        codigo_norm = normalizar_numero_texto(codigo) or str(codigo or "").strip()
        if not codigo_norm or codigo_norm in usados:
            continue

        usados.add(codigo_norm)
        contratos.append((
            codigo or codigo_norm,
            endereco or "",
            inicio or "",
            fim or "",
            status or "Ativo"
        ))

    # Fallback: só usa o contrato do cadastro do usuário se não achou nada
    # na tabela contratos. Neste caso realmente não há endereço/vigência importados.
    if not contratos and contrato_usuario:
        contratos.append((contrato_usuario, "", "", "", "Ativo"))

    conn.close()
    return boletos, informes, contratos


def renderizar_area_locatario(pagina):
    cpf = session.get("cpf")

    if not cpf:
        return redirect("/")

    boletos, informes, contratos = carregar_dados_locatario(cpf)

    return render_template(
        "locatario.html",
        boletos=boletos,
        informes=informes,
        contratos=contratos,
        nome=session.get("nome"),
        pagina=pagina,
        formatar_data_br=formatar_data_br
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


@app.route("/locatario/contratos")
def locatario_contratos():
    return renderizar_area_locatario("contratos")



def carregar_dados_locador(cpf):
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, nome_arquivo, competencia, data_repasse, valor
        FROM demonstrativos_locador
        WHERE cpf_locador = ?
        ORDER BY data_repasse DESC, id DESC
    """, (cpf,))
    demonstrativos = cursor.fetchall()

    cursor.execute("""
        SELECT id, nome_arquivo, ano, data_envio
        FROM informes_locador
        WHERE cpf_locador = ?
        ORDER BY ano DESC, id DESC
    """, (cpf,))
    informes_locador = cursor.fetchall()

    cursor.execute("""
        SELECT codigo_contrato, endereco_imovel, nome_locatario, cpf_locatario, inicio_vigencia, fim_vigencia, status
        FROM contratos_locador
        WHERE cpf_locador = ?
          AND COALESCE(status, 'Ativo') = 'Ativo'
        ORDER BY CAST(codigo_contrato AS INTEGER), codigo_contrato
    """, (cpf,))
    contratos_locador = cursor.fetchall()

    conn.close()
    return demonstrativos, informes_locador, contratos_locador


def renderizar_area_locador(pagina):
    cpf = session.get("cpf")
    if not cpf or session.get("tipo") != "locador":
        return redirect("/")
    demonstrativos, informes_locador, contratos_locador = carregar_dados_locador(cpf)
    return render_template("locador.html", demonstrativos=demonstrativos, informes_locador=informes_locador, contratos_locador=contratos_locador, nome=session.get("nome"), pagina=pagina, formatar_data_br=formatar_data_br)


@app.route("/locador")
def locador():
    return renderizar_area_locador("dashboard")

@app.route("/locador/demonstrativos")
def locador_demonstrativos():
    return renderizar_area_locador("demonstrativos")

@app.route("/locador/informes")
def locador_informes():
    return renderizar_area_locador("informes")

@app.route("/locador/contratos")
def locador_contratos():
    return renderizar_area_locador("contratos")

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
        df.columns = [normalizar_coluna(col) for col in df.columns]

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

        valido, mensagem_erro = validar_pdf(arquivo)
        if not valido:
            conn.close()
            flash(mensagem_erro)
            return redirect("/upload_boleto")

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
            nome_base = f"{cpf_locatario}_{contrato}_{parcela}_{secure_filename(arquivo.filename)}"
            nome_final = nome_disponivel(UPLOAD_FOLDER, nome_base)
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

        valido, mensagem_erro = validar_excel(arquivo)
        if not valido:
            flash(mensagem_erro)
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



@app.route("/importar_baixas", methods=["GET", "POST"])
def importar_baixas():
    if not login_obrigatorio_admin():
        return redirect("/")

    resultado = None

    if request.method == "POST":
        arquivo = request.files.get("arquivo")

        if not arquivo or not arquivo.filename:
            flash("Selecione o arquivo TXT de recebimentos.")
            return redirect("/importar_baixas")

        baixas, data_baixa = extrair_baixas_recebimentos_txt(arquivo)

        if not baixas:
            flash("Nenhuma baixa foi encontrada no arquivo. Verifique se o TXT é o relatório de recebimentos.")
            return redirect("/importar_baixas")

        conn = conectar()
        cursor = conn.cursor()

        baixados = []
        divergentes = []
        nao_localizados = []
        ja_pagos = []
        arquivo_nome = secure_filename(arquivo.filename)

        for baixa in baixas:
            contrato_num = int(normalizar_numero_texto(baixa["contrato"]) or 0)
            parcela_num = int(normalizar_numero_texto(baixa["parcela"]) or 0)

            cursor.execute("""
                SELECT id, nome_locatario, contrato, parcela, valor, status
                FROM boletos
                WHERE CAST(contrato AS INTEGER) = ?
                  AND CAST(parcela AS INTEGER) = ?
                ORDER BY id DESC
            """, (contrato_num, parcela_num))

            encontrados = cursor.fetchall()

            if not encontrados:
                nao_localizados.append(baixa)
                continue

            boleto = encontrados[0]
            id_boleto, nome_locatario, contrato_boleto, parcela_boleto, valor_boleto, status_atual = boleto

            if status_atual in ("Pago", "Pago com divergência"):
                ja_pagos.append({**baixa, "nome_boleto": nome_locatario})
                continue

            valor_boleto_cent = valor_br_para_centavos(valor_boleto)
            valor_pago_cent = valor_br_para_centavos(baixa["valor_pago"])
            status_novo = "Pago"
            observacao = "Baixa importada automaticamente pelo relatório de recebimentos."

            if valor_boleto_cent is not None and valor_pago_cent is not None and valor_boleto_cent != valor_pago_cent:
                status_novo = "Pago com divergência"
                observacao = f"Valor do boleto: {valor_boleto}. Valor pago no relatório: {baixa['valor_pago']}."

            cursor.execute("""
                UPDATE boletos
                SET status = ?,
                    data_pagamento = ?,
                    valor_pago = ?,
                    origem_baixa = ?,
                    arquivo_baixa = ?,
                    observacao_baixa = ?
                WHERE id = ?
            """, (
                status_novo,
                baixa["data_baixa"] or data_baixa,
                baixa["valor_pago"],
                baixa["tipo_recebimento"] or "Relatório de recebimentos",
                arquivo_nome,
                observacao,
                id_boleto,
            ))

            item = {**baixa, "nome_boleto": nome_locatario, "status_novo": status_novo}
            if status_novo == "Pago com divergência":
                divergentes.append(item)
            else:
                baixados.append(item)

        conn.commit()
        conn.close()

        resultado = {
            "data_baixa": formatar_data_br(data_baixa),
            "total_linhas": len(baixas),
            "baixados": baixados,
            "divergentes": divergentes,
            "nao_localizados": nao_localizados,
            "ja_pagos": ja_pagos,
        }

        flash(
            f"Importação concluída. Pagos: {len(baixados)}. "
            f"Com divergência: {len(divergentes)}. "
            f"Não localizados: {len(nao_localizados)}. "
            f"Já estavam pagos: {len(ja_pagos)}."
        )

    return render_template("importar_baixas.html", resultado=resultado)

@app.route("/importar_contratos", methods=["GET", "POST"])
def importar_contratos():
    if not login_obrigatorio_admin():
        return redirect("/")

    if request.method == "POST":
        arquivo = request.files.get("arquivo")

        valido, mensagem_erro = validar_excel(arquivo)
        if not valido:
            flash(mensagem_erro)
            return redirect("/importar_contratos")

        df = pd.read_excel(arquivo, dtype=str)
        df.columns = [normalizar_coluna(col) for col in df.columns]

        conn = conectar()
        cursor = conn.cursor()

        importados = 0
        clientes_criados = 0
        clientes_atualizados = 0
        ignorados = 0
        lista_ignorados = []

        for _, row in df.iterrows():
            cpf = limpar_cpf(texto_planilha(
                row,
                "CPF", "CPF_LOCATARIO", "CPF LOCATARIO", "CPF LOCATÁRIO", "CPF/CNPJ", "CNPJ"
            ))

            nome = texto_planilha(
                row,
                "NOME", "NOME_LOCATARIO", "NOME LOCATARIO", "LOCATARIO", "LOCATÁRIO", "CLIENTE"
            )

            codigo_contrato = normalizar_numero_texto(texto_planilha(
                row,
                "CONTRATO", "CODIGO_CONTRATO", "CÓDIGO CONTRATO", "CODIGO DO CONTRATO", "CÓDIGO DO CONTRATO"
            ))

            endereco = texto_planilha(
                row,
                "ENDERECO", "ENDEREÇO", "ENDERECO_IMOVEL", "ENDEREÇO IMÓVEL",
                "ENDERECO DO IMOVEL", "ENDEREÇO DO IMÓVEL", "IMOVEL", "IMÓVEL"
            )

            inicio_vigencia = formatar_data(texto_planilha(
                row,
                "INICIO_VIGENCIA", "INÍCIO_VIGÊNCIA", "INÍCIO VIGÊNCIA",
                "INICIO DA VIGENCIA", "INÍCIO DA VIGÊNCIA",
                "INICIO", "INÍCIO", "DATA INICIO", "DATA INÍCIO",
                "DATA_INICIO", "DATA_INICIO_VIGENCIA"
            ))

            fim_vigencia = formatar_data(texto_planilha(
                row,
                "FIM_VIGENCIA", "FIM VIGÊNCIA", "FIM DA VIGENCIA", "FIM DA VIGÊNCIA",
                "FIM", "DATA FIM", "DATA_FIM", "DATA_FIM_VIGENCIA",
                "TERMINO", "TÉRMINO"
            ))

            status_original = texto_planilha(row, "STATUS", "SITUACAO", "SITUAÇÃO") or "Ativo"
            status = "Inativo" if status_original.strip().upper() in (
                "INATIVO", "INATIVA", "ENCERRADO", "ENCERRADA", "BAIXADO", "BAIXADA"
            ) else "Ativo"

            if not cpf or not codigo_contrato:
                ignorados += 1
                lista_ignorados.append(f"{nome or 'Sem nome'} - CPF/contrato inválido")
                continue

            if not nome:
                ignorados += 1
                lista_ignorados.append(f"Contrato {codigo_contrato} - nome do locatário vazio")
                continue

            # A planilha de contratos passa a ser a base principal:
            # cria/atualiza o usuário locatário e vincula pelo CPF.
            cursor.execute("""
                SELECT id
                FROM usuarios
                WHERE cpf = ? AND tipo = 'locatario'
            """, (cpf,))
            usuario_existente = cursor.fetchone()

            if usuario_existente:
                cursor.execute("""
                    UPDATE usuarios
                    SET nome = ?,
                        usuario = ?,
                        contrato = ?,
                        ativo = 1
                    WHERE id = ?
                """, (nome, cpf, codigo_contrato, usuario_existente[0]))
                clientes_atualizados += 1
            else:
                cursor.execute("""
                    INSERT INTO usuarios
                    (nome, cpf, usuario, senha, contrato, tipo, senha_definida, ativo)
                    VALUES (?, ?, ?, NULL, ?, 'locatario', 0, 1)
                """, (nome, cpf, cpf, codigo_contrato))
                clientes_criados += 1

            # Remove registros antigos/incompletos deste mesmo CPF + contrato.
            # Isso evita que o portal continue exibindo '-' por causa de importações antigas.
            cursor.execute("""
                DELETE FROM contratos
                WHERE REPLACE(REPLACE(REPLACE(COALESCE(cpf_locatario, ''), '.', ''), '-', ''), '/', '') = ?
                  AND CAST(
                        CASE
                            WHEN REPLACE(REPLACE(REPLACE(COALESCE(codigo_contrato, ''), '.', ''), '-', ''), '/', '') GLOB '[0-9]*'
                            THEN REPLACE(REPLACE(REPLACE(COALESCE(codigo_contrato, ''), '.', ''), '-', ''), '/', '')
                            ELSE '0'
                        END AS INTEGER
                      ) = CAST(? AS INTEGER)
            """, (cpf, codigo_contrato))

            try:
                cursor.execute("""
                    INSERT INTO contratos
                    (cpf_locatario, nome_locatario, codigo_contrato, endereco_imovel,
                     inicio_vigencia, fim_vigencia, status, data_importacao)
                    VALUES (?, ?, ?, ?, ?, ?, ?, date('now'))
                """, (
                    cpf,
                    nome,
                    codigo_contrato,
                    endereco,
                    inicio_vigencia,
                    fim_vigencia,
                    status
                ))
                importados += 1
            except Exception as erro:
                ignorados += 1
                lista_ignorados.append(
                    f"Linha {_ + 2} - contrato {codigo_contrato} - erro ao gravar contrato: {erro}"
                )

        with open("contratos_ignorados.txt", "w", encoding="utf-8") as f:
            for item in lista_ignorados:
                f.write(item + "\n")

        try:
            database.registrar_log_importacao(
                "contratos_locatario",
                importados=importados,
                atualizados=clientes_atualizados,
                ignorados=ignorados,
                mensagem=f"Clientes criados: {clientes_criados}."
            )
        except Exception as erro_log:
            print(f"Aviso: não foi possível gravar log da importação de contratos: {erro_log}")

        conn.commit()
        conn.close()

        flash(
            f"Importação concluída. Contratos importados/atualizados: {importados}. "
            f"Clientes criados: {clientes_criados}. "
            f"Clientes atualizados: {clientes_atualizados}. "
            f"Ignorados: {ignorados}."
        )
        return redirect("/importar_contratos")

    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT codigo_contrato, nome_locatario, cpf_locatario, endereco_imovel,
               inicio_vigencia, fim_vigencia, status
        FROM contratos
        ORDER BY id DESC
        LIMIT 50
    """)
    contratos = cursor.fetchall()
    conn.close()

    logs_importacao = []
    try:
        logs_importacao = database.listar_logs_importacao("contratos_locatario", 8)
    except Exception:
        logs_importacao = []

    return render_template(
        "importar_contratos.html",
        contratos=contratos,
        logs_importacao=logs_importacao,
        formatar_data_br=formatar_data_br
    )


@app.route("/importar_informes", methods=["GET", "POST"])
def importar_informes():
    if not login_obrigatorio_admin():
        return redirect("/")

    lista_ignorados = []
    importados = None
    ignorados = None

    if request.method == "POST":
        arquivo = request.files.get("arquivo")

        if not arquivo:
            flash("Selecione uma planilha.")
            return redirect("/importar_informes")

        df = pd.read_excel(arquivo, dtype=str)
        df.columns = [normalizar_coluna(col) for col in df.columns]

        conn = conectar()
        cursor = conn.cursor()

        importados = 0
        ignorados = 0

        for indice, row in df.iterrows():
            linha_excel = indice + 2

            nome = str(row.get("NOME", "") or "").strip()
            cpf = limpar_cpf(row.get("CPF", ""))
            ano = str(row.get("ANO", "") or "").strip()

            caminho_pdf = (
                row.get("CAMINHO") or
                row.get("CAMINHO DO INFORME") or
                row.get("CAMINHO DO ARQUIVO") or
                row.get("ARQUIVO") or
                ""
            )
            caminho_pdf = str(caminho_pdf).strip()

            if not cpf:
                ignorados += 1
                lista_ignorados.append(f"Linha {linha_excel}: CPF vazio ou inválido. Nome: {nome or '-'}")
                continue

            if not ano or ano.lower() == "nan":
                ignorados += 1
                lista_ignorados.append(f"Linha {linha_excel}: Ano vazio ou inválido. CPF: {cpf}")
                continue

            if not caminho_pdf or caminho_pdf.lower() == "nan":
                ignorados += 1
                lista_ignorados.append(f"Linha {linha_excel}: Caminho do PDF vazio. CPF: {cpf}")
                continue

            cursor.execute("""
                SELECT nome
                FROM usuarios
                WHERE cpf = ? AND tipo = 'locatario'
            """, (cpf,))
            cliente = cursor.fetchone()

            if not cliente:
                ignorados += 1
                lista_ignorados.append(f"Linha {linha_excel}: CPF {cpf} não encontrado no cadastro de locatários.")
                continue

            origem = Path(caminho_pdf)

            if not origem.exists():
                ignorados += 1
                lista_ignorados.append(f"Linha {linha_excel}: Arquivo não encontrado: {caminho_pdf}")
                continue

            try:
                nome_seguro = secure_filename(origem.name)
                nome_final = f"{cpf}_INFORME_{ano}_{nome_seguro}"
                destino = INFORMES_FOLDER / nome_final

                shutil.copy2(origem, destino)

                cursor.execute("""
                    INSERT INTO informes
                    (cpf_locatario, nome_locatario, nome_arquivo, ano, data_envio)
                    VALUES (?, ?, ?, ?, date('now'))
                """, (
                    cpf,
                    cliente[0],
                    nome_final,
                    ano
                ))

                importados += 1

            except Exception as e:
                ignorados += 1
                lista_ignorados.append(f"Linha {linha_excel}: CPF {cpf} - Erro ao importar: {str(e)}")

        conn.commit()
        conn.close()

        # Salva também um TXT na pasta do projeto para conferência posterior.
        with open("informes_ignorados.txt", "w", encoding="utf-8") as f:
            for item in lista_ignorados:
                f.write(item + "\n")

        flash(f"Importação concluída. Importados: {importados}. Ignorados: {ignorados}.")

    return render_template(
        "importar_informes.html",
        lista_ignorados=lista_ignorados,
        importados=importados,
        ignorados=ignorados
    )


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

        valido, mensagem_erro = validar_pdf(arquivo)
        if not valido:
            conn.close()
            flash(mensagem_erro)
            return redirect("/upload_informe")

        cursor.execute("SELECT nome FROM usuarios WHERE cpf = ?", (cpf_locatario,))
        cliente = cursor.fetchone()

        if not cliente:
            conn.close()
            flash("CPF do locatário não encontrado.")
            return redirect("/upload_informe")

        if arquivo:
            nome_base = f"{cpf_locatario}_INFORME_{ano}_{secure_filename(arquivo.filename)}"
            nome_final = nome_disponivel(INFORMES_FOLDER, nome_base)
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
            SELECT id, nome_arquivo, nome_locatario, valor, vencimento, status, contrato, parcela, data_pagamento, valor_pago, origem_baixa, observacao_baixa
            FROM boletos
            WHERE nome_locatario LIKE ? OR cpf_locatario LIKE ? OR contrato LIKE ?
            ORDER BY id DESC
        """, (termo, termo, termo))
    else:
        cursor.execute("""
            SELECT id, nome_arquivo, nome_locatario, valor, vencimento, status, contrato, parcela, data_pagamento, valor_pago, origem_baixa, observacao_baixa
            FROM boletos
            ORDER BY id DESC
        """)

    boletos = cursor.fetchall()

    total_pagos = sum(1 for b in boletos if b[5] in ("Pago", "Pago com divergência"))
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
        SET status = 'Pago',
            data_pagamento = ?,
            origem_baixa = 'Baixa manual',
            observacao_baixa = 'Marcado como pago manualmente pelo administrador.'
        WHERE id = ?
    """, (date.today().strftime("%Y-%m-%d"), id_boleto,))

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



@app.route("/importar_locadores", methods=["GET", "POST"])
def importar_locadores():
    if not login_obrigatorio_admin():
        return redirect("/")
    if request.method == "POST":
        arquivo = request.files.get("arquivo")
        valido, mensagem_erro = validar_excel(arquivo)
        if not valido:
            flash(mensagem_erro); return redirect("/importar_locadores")
        df = pd.read_excel(arquivo, dtype=str); df.columns = [normalizar_coluna(c) for c in df.columns]
        conn = conectar(); cursor = conn.cursor(); importados = atualizados = ignorados = 0
        for _, row in df.iterrows():
            nome = texto_planilha(row, "NOME", "NOME_LOCADOR", "PROPRIETARIO", "PROPRIETÁRIO", "LOCADOR")
            cpf = limpar_cpf(texto_planilha(row, "CPF", "CPF_LOCADOR", "CPF LOCADOR", "CPF PROPRIETARIO", "CPF PROPRIETÁRIO"))
            if not nome or not cpf:
                ignorados += 1; continue
            cursor.execute("SELECT id FROM usuarios WHERE cpf = ? AND tipo = 'locador'", (cpf,)); existente = cursor.fetchone()
            if existente:
                cursor.execute("UPDATE usuarios SET nome = ?, ativo = 1 WHERE id = ?", (nome, existente[0])); atualizados += 1
            else:
                cursor.execute("INSERT INTO usuarios (nome, cpf, usuario, senha, tipo, senha_definida, ativo) VALUES (?, ?, ?, ?, ?, ?, ?)", (nome, cpf, cpf, None, "locador", 0, 1)); importados += 1
        conn.commit(); conn.close()
        try:
            database.registrar_log_importacao("locadores", importados, atualizados, ignorados, "")
        except Exception as erro_log:
            print(f"Aviso: não foi possível gravar log da importação de locadores: {erro_log}")
        flash(f"Importação concluída. Novos: {importados}. Atualizados: {atualizados}. Ignorados: {ignorados}.")
        return redirect("/importar_locadores")
    return render_template("importar_locadores.html")


@app.route("/importar_demonstrativos_locador", methods=["GET", "POST"])
def importar_demonstrativos_locador():
    if not login_obrigatorio_admin():
        return redirect("/")
    if request.method == "POST":
        arquivo = request.files.get("arquivo")
        valido, mensagem_erro = validar_excel(arquivo)
        if not valido:
            flash(mensagem_erro); return redirect("/importar_demonstrativos_locador")
        df = pd.read_excel(arquivo, dtype=str); df.columns = [normalizar_coluna(c) for c in df.columns]
        conn = conectar(); cursor = conn.cursor(); importados = ignorados = 0
        for _, row in df.iterrows():
            cpf = limpar_cpf(texto_planilha(row, "CPF", "CPF_LOCADOR", "CPF LOCADOR", "CPF PROPRIETARIO", "CPF PROPRIETÁRIO"))
            nome = texto_planilha(row, "NOME", "NOME_LOCADOR", "PROPRIETARIO", "PROPRIETÁRIO", "LOCADOR")
            competencia = texto_planilha(row, "COMPETENCIA", "COMPETÊNCIA", "MES", "MÊS", "REFERENCIA", "REFERÊNCIA")
            data_repasse = formatar_data(texto_planilha(row, "DATA_REPASSE", "DATA REPASSE", "DATA", "REPASSE"))
            valor = texto_planilha(row, "VALOR", "VALOR_REPASSE", "VALOR REPASSE")
            caminho_doc = texto_planilha(row, "CAMINHO", "CAMINHO DO ARQUIVO", "CAMINHO DO DEMONSTRATIVO", "ARQUIVO")
            if not cpf or not caminho_doc or not Path(caminho_doc).exists():
                ignorados += 1; continue
            if not nome:
                cursor.execute("SELECT nome FROM usuarios WHERE cpf = ? AND tipo = 'locador'", (cpf,)); u = cursor.fetchone(); nome = u[0] if u else ""
            origem = Path(caminho_doc); nome_seguro = secure_filename(origem.name); nome_base = f"{cpf}_DEMONSTRATIVO_{competencia}_{nome_seguro}"
            nome_final = nome_disponivel(DEMONSTRATIVOS_LOCADOR_FOLDER, nome_base)
            shutil.copy2(origem, DEMONSTRATIVOS_LOCADOR_FOLDER / nome_final)
            cursor.execute("INSERT INTO demonstrativos_locador (cpf_locador, nome_locador, nome_arquivo, competencia, data_repasse, valor, data_importacao) VALUES (?, ?, ?, ?, ?, ?, date('now'))", (cpf, nome, nome_final, competencia, data_repasse, valor)); importados += 1
        conn.commit(); conn.close()
        flash(f"Importação concluída. Importados: {importados}. Ignorados: {ignorados}.")
        return redirect("/importar_demonstrativos_locador")
    return render_template("importar_demonstrativos_locador.html")


@app.route("/importar_informes_locador", methods=["GET", "POST"])
def importar_informes_locador():
    if not login_obrigatorio_admin():
        return redirect("/")
    if request.method == "POST":
        arquivo = request.files.get("arquivo")
        valido, mensagem_erro = validar_excel(arquivo)
        if not valido:
            flash(mensagem_erro); return redirect("/importar_informes_locador")
        df = pd.read_excel(arquivo, dtype=str); df.columns = [normalizar_coluna(c) for c in df.columns]
        conn = conectar(); cursor = conn.cursor(); importados = ignorados = 0
        for _, row in df.iterrows():
            cpf = limpar_cpf(texto_planilha(row, "CPF", "CPF_LOCADOR", "CPF LOCADOR", "CPF PROPRIETARIO", "CPF PROPRIETÁRIO"))
            nome = texto_planilha(row, "NOME", "NOME_LOCADOR", "PROPRIETARIO", "PROPRIETÁRIO", "LOCADOR")
            ano = texto_planilha(row, "ANO", "EXERCICIO", "EXERCÍCIO")
            caminho_doc = texto_planilha(row, "CAMINHO", "CAMINHO DO ARQUIVO", "CAMINHO DO INFORME", "ARQUIVO")
            if not cpf or not ano or not caminho_doc or not Path(caminho_doc).exists():
                ignorados += 1; continue
            if not nome:
                cursor.execute("SELECT nome FROM usuarios WHERE cpf = ? AND tipo = 'locador'", (cpf,)); u = cursor.fetchone(); nome = u[0] if u else ""
            origem = Path(caminho_doc); nome_seguro = secure_filename(origem.name); nome_base = f"{cpf}_INFORME_RENDIMENTOS_{ano}_{nome_seguro}"
            nome_final = nome_disponivel(INFORMES_LOCADOR_FOLDER, nome_base)
            shutil.copy2(origem, INFORMES_LOCADOR_FOLDER / nome_final)
            cursor.execute("INSERT INTO informes_locador (cpf_locador, nome_locador, nome_arquivo, ano, data_envio) VALUES (?, ?, ?, ?, date('now'))", (cpf, nome, nome_final, ano)); importados += 1
        conn.commit(); conn.close()
        flash(f"Importação concluída. Importados: {importados}. Ignorados: {ignorados}.")
        return redirect("/importar_informes_locador")
    return render_template("importar_informes_locador.html")


@app.route("/importar_contratos_locador", methods=["GET", "POST"])
def importar_contratos_locador():
    if not login_obrigatorio_admin():
        return redirect("/")
    if request.method == "POST":
        arquivo = request.files.get("arquivo")
        valido, mensagem_erro = validar_excel(arquivo)
        if not valido:
            flash(mensagem_erro); return redirect("/importar_contratos_locador")
        df = pd.read_excel(arquivo, dtype=str); df.columns = [normalizar_coluna(c) for c in df.columns]
        conn = conectar(); cursor = conn.cursor(); importados = atualizados = ignorados = 0
        for _, row in df.iterrows():
            cpf_locador = limpar_cpf(texto_planilha(row, "CPF_LOCADOR", "CPF LOCADOR", "CPF PROPRIETARIO", "CPF PROPRIETÁRIO", "CPF"))
            nome_locador = texto_planilha(row, "NOME_LOCADOR", "NOME LOCADOR", "PROPRIETARIO", "PROPRIETÁRIO", "LOCADOR")
            codigo = normalizar_numero_texto(texto_planilha(row, "CONTRATO", "CODIGO_CONTRATO", "CÓDIGO CONTRATO", "CODIGO DO CONTRATO"))
            endereco = texto_planilha(row, "ENDERECO", "ENDEREÇO", "ENDERECO_IMOVEL", "ENDEREÇO IMÓVEL", "IMOVEL", "IMÓVEL")
            nome_locatario = texto_planilha(row, "NOME_LOCATARIO", "NOME LOCATARIO", "LOCATARIO", "LOCATÁRIO", "INQUILINO")
            cpf_locatario = limpar_cpf(texto_planilha(row, "CPF_LOCATARIO", "CPF LOCATARIO", "CPF LOCATÁRIO", "CPF INQUILINO"))
            inicio = formatar_data(texto_planilha(row, "INICIO_VIGENCIA", "INÍCIO VIGÊNCIA", "INICIO", "INÍCIO", "DATA INICIO"))
            fim = formatar_data(texto_planilha(row, "FIM_VIGENCIA", "FIM VIGÊNCIA", "FIM", "DATA FIM", "TERMINO", "TÉRMINO"))
            status = texto_planilha(row, "STATUS", "SITUACAO", "SITUAÇÃO") or "Ativo"
            if not cpf_locador or not codigo:
                ignorados += 1; continue
            cursor.execute("SELECT id FROM contratos_locador WHERE cpf_locador = ? AND codigo_contrato = ?", (cpf_locador, codigo)); existente = cursor.fetchone()
            if existente:
                cursor.execute("UPDATE contratos_locador SET nome_locador=?, endereco_imovel=?, nome_locatario=?, cpf_locatario=?, inicio_vigencia=?, fim_vigencia=?, status=?, data_importacao=date('now') WHERE id=?", (nome_locador, endereco, nome_locatario, cpf_locatario, inicio, fim, status, existente[0])); atualizados += 1
            else:
                cursor.execute("INSERT INTO contratos_locador (cpf_locador, nome_locador, codigo_contrato, endereco_imovel, nome_locatario, cpf_locatario, inicio_vigencia, fim_vigencia, status, data_importacao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, date('now'))", (cpf_locador, nome_locador, codigo, endereco, nome_locatario, cpf_locatario, inicio, fim, status)); importados += 1
        conn.commit(); conn.close()
        try:
            database.registrar_log_importacao("contratos_locador", importados, atualizados, ignorados, "")
        except Exception as erro_log:
            print(f"Aviso: não foi possível gravar log da importação de contratos do locador: {erro_log}")
        flash(f"Importação concluída. Novos: {importados}. Atualizados: {atualizados}. Ignorados: {ignorados}.")
        return redirect("/importar_contratos_locador")
    conn = conectar(); cursor = conn.cursor()
    cursor.execute("SELECT codigo_contrato, nome_locador, cpf_locador, endereco_imovel, nome_locatario, cpf_locatario, inicio_vigencia, fim_vigencia, status FROM contratos_locador ORDER BY id DESC LIMIT 50")
    contratos = cursor.fetchall(); conn.close()
    return render_template("importar_contratos_locador.html", contratos=contratos, formatar_data_br=formatar_data_br)


@app.route("/baixar_demonstrativo_locador/<int:id_demonstrativo>")
def baixar_demonstrativo_locador(id_demonstrativo):
    cpf = session.get("cpf"); tipo = session.get("tipo")
    if not cpf: return redirect("/")
    conn = conectar(); cursor = conn.cursor()
    if tipo == "admin": cursor.execute("SELECT nome_arquivo FROM demonstrativos_locador WHERE id = ?", (id_demonstrativo,))
    else: cursor.execute("SELECT nome_arquivo FROM demonstrativos_locador WHERE id = ? AND cpf_locador = ?", (id_demonstrativo, cpf))
    item = cursor.fetchone(); conn.close()
    if not item: return "Acesso negado ou demonstrativo não encontrado.", 403
    return send_from_directory(DEMONSTRATIVOS_LOCADOR_FOLDER, item[0], as_attachment=False)


@app.route("/baixar_informe_locador/<int:id_informe>")
def baixar_informe_locador(id_informe):
    cpf = session.get("cpf"); tipo = session.get("tipo")
    if not cpf: return redirect("/")
    conn = conectar(); cursor = conn.cursor()
    if tipo == "admin": cursor.execute("SELECT nome_arquivo FROM informes_locador WHERE id = ?", (id_informe,))
    else: cursor.execute("SELECT nome_arquivo FROM informes_locador WHERE id = ? AND cpf_locador = ?", (id_informe, cpf))
    item = cursor.fetchone(); conn.close()
    if not item: return "Acesso negado ou informe não encontrado.", 403
    return send_from_directory(INFORMES_LOCADOR_FOLDER, item[0], as_attachment=False)

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
