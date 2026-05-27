import sqlite3

from werkzeug.security import generate_password_hash


DB_PATH = "portal.db"


def conectar():
    return sqlite3.connect(DB_PATH)


def coluna_existe(cursor, tabela, coluna):
    cursor.execute(f"PRAGMA table_info({tabela})")
    colunas = [linha[1] for linha in cursor.fetchall()]
    return coluna in colunas



def registrar_log_importacao(tipo, importados=0, atualizados=0, ignorados=0, mensagem=""):
    conn = conectar()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO importacoes_log
        (tipo, importados, atualizados, ignorados, mensagem, data_importacao)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
    """, (tipo, importados, atualizados, ignorados, mensagem))
    conn.commit()
    conn.close()


def listar_logs_importacao(tipo=None, limite=10):
    conn = conectar()
    cursor = conn.cursor()
    if tipo:
        cursor.execute("""
            SELECT tipo, importados, atualizados, ignorados, mensagem, data_importacao
            FROM importacoes_log
            WHERE tipo = ?
            ORDER BY id DESC
            LIMIT ?
        """, (tipo, limite))
    else:
        cursor.execute("""
            SELECT tipo, importados, atualizados, ignorados, mensagem, data_importacao
            FROM importacoes_log
            ORDER BY id DESC
            LIMIT ?
        """, (limite,))
    logs = cursor.fetchall()
    conn.close()
    return logs


def ajustar_tabela_usuarios(cursor):
    cursor.execute("PRAGMA table_info(usuarios)")
    colunas = [linha[1] for linha in cursor.fetchall()]
    if not colunas:
        return

    cursor.execute("PRAGMA index_list(usuarios)")
    indices = cursor.fetchall()
    tem_cpf_unico_antigo = False
    for indice in indices:
        nome_indice = indice[1]
        unico = indice[2]
        if not unico:
            continue
        cursor.execute(f"PRAGMA index_info({nome_indice})")
        cols_indice = [linha[2] for linha in cursor.fetchall()]
        if cols_indice == ["cpf"]:
            tem_cpf_unico_antigo = True
            break

    if not tem_cpf_unico_antigo:
        return

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_novo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            cpf TEXT,
            usuario TEXT,
            senha TEXT,
            contrato TEXT,
            tipo TEXT NOT NULL DEFAULT 'locatario',
            senha_definida INTEGER DEFAULT 0,
            ativo INTEGER DEFAULT 1,
            UNIQUE(cpf, tipo)
        )
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO usuarios_novo
        (id, nome, cpf, usuario, senha, contrato, tipo, senha_definida, ativo)
        SELECT id, nome, cpf, usuario, senha, contrato, tipo, senha_definida, ativo
        FROM usuarios
    """)
    cursor.execute("DROP TABLE usuarios")
    cursor.execute("ALTER TABLE usuarios_novo RENAME TO usuarios")

def criar_tabelas():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        cpf TEXT,
        usuario TEXT,
        senha TEXT,
        contrato TEXT,
        tipo TEXT NOT NULL DEFAULT 'locatario',
        senha_definida INTEGER DEFAULT 0,
        ativo INTEGER DEFAULT 1,
        UNIQUE(cpf, tipo)
    )
    """)

    ajustar_tabela_usuarios(cursor)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS boletos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_arquivo TEXT NOT NULL,
        usuario_locatario TEXT,
        cpf_locatario TEXT,
        nome_locatario TEXT,
        contrato TEXT,
        parcela TEXT,
        vencimento TEXT,
        valor TEXT,
        status TEXT DEFAULT 'Em aberto'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS informes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf_locatario TEXT NOT NULL,
        nome_locatario TEXT,
        nome_arquivo TEXT NOT NULL,
        ano TEXT,
        data_envio TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contratos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf_locatario TEXT NOT NULL,
        nome_locatario TEXT,
        codigo_contrato TEXT NOT NULL,
        endereco_imovel TEXT,
        inicio_vigencia TEXT,
        fim_vigencia TEXT,
        status TEXT DEFAULT 'Ativo',
        data_importacao TEXT
    )
    """)



    cursor.execute("""
    CREATE TABLE IF NOT EXISTS demonstrativos_locador (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf_locador TEXT NOT NULL,
        nome_locador TEXT,
        nome_arquivo TEXT NOT NULL,
        competencia TEXT,
        data_repasse TEXT,
        valor TEXT,
        data_importacao TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS informes_locador (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf_locador TEXT NOT NULL,
        nome_locador TEXT,
        nome_arquivo TEXT NOT NULL,
        ano TEXT,
        data_envio TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contratos_locador (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cpf_locador TEXT NOT NULL,
        nome_locador TEXT,
        codigo_contrato TEXT NOT NULL,
        endereco_imovel TEXT,
        nome_locatario TEXT,
        cpf_locatario TEXT,
        inicio_vigencia TEXT,
        fim_vigencia TEXT,
        status TEXT DEFAULT 'Ativo',
        data_importacao TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS configuracoes (
        chave TEXT PRIMARY KEY,
        valor TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS importacoes_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL,
        importados INTEGER DEFAULT 0,
        atualizados INTEGER DEFAULT 0,
        ignorados INTEGER DEFAULT 0,
        mensagem TEXT,
        data_importacao TEXT
    )
    """)

    # Atualizações para bancos antigos
    for coluna, tipo in [
        ("cpf", "TEXT"),
        ("contrato", "TEXT"),
        ("senha_definida", "INTEGER DEFAULT 0"),
        ("ativo", "INTEGER DEFAULT 1"),
    ]:
        if not coluna_existe(cursor, "usuarios", coluna):
            cursor.execute(f"ALTER TABLE usuarios ADD COLUMN {coluna} {tipo}")

    for coluna, tipo in [
        ("cpf_locatario", "TEXT"),
        ("nome_locatario", "TEXT"),
        ("contrato", "TEXT"),
        ("parcela", "TEXT"),
        ("vencimento", "TEXT"),
        ("valor", "TEXT"),
        ("status", "TEXT DEFAULT 'Em aberto'"),
        ("data_pagamento", "TEXT"),
        ("valor_pago", "TEXT"),
        ("origem_baixa", "TEXT"),
        ("arquivo_baixa", "TEXT"),
        ("observacao_baixa", "TEXT"),
    ]:
        if not coluna_existe(cursor, "boletos", coluna):
            cursor.execute(f"ALTER TABLE boletos ADD COLUMN {coluna} {tipo}")

    for coluna, tipo in [
        ("cpf_locatario", "TEXT"),
        ("nome_locatario", "TEXT"),
        ("nome_arquivo", "TEXT"),
        ("ano", "TEXT"),
        ("data_envio", "TEXT"),
    ]:
        if not coluna_existe(cursor, "informes", coluna):
            cursor.execute(f"ALTER TABLE informes ADD COLUMN {coluna} {tipo}")

    for coluna, tipo in [
        ("cpf_locatario", "TEXT"),
        ("nome_locatario", "TEXT"),
        ("codigo_contrato", "TEXT"),
        ("endereco_imovel", "TEXT"),
        ("inicio_vigencia", "TEXT"),
        ("fim_vigencia", "TEXT"),
        ("status", "TEXT DEFAULT 'Ativo'"),
        ("data_importacao", "TEXT"),
    ]:
        if not coluna_existe(cursor, "contratos", coluna):
            cursor.execute(f"ALTER TABLE contratos ADD COLUMN {coluna} {tipo}")


    for tabela, colunas in {
        "demonstrativos_locador": [
            ("cpf_locador", "TEXT"), ("nome_locador", "TEXT"), ("nome_arquivo", "TEXT"),
            ("competencia", "TEXT"), ("data_repasse", "TEXT"), ("valor", "TEXT"), ("data_importacao", "TEXT"),
        ],
        "informes_locador": [
            ("cpf_locador", "TEXT"), ("nome_locador", "TEXT"), ("nome_arquivo", "TEXT"),
            ("ano", "TEXT"), ("data_envio", "TEXT"),
        ],
        "contratos_locador": [
            ("cpf_locador", "TEXT"), ("nome_locador", "TEXT"), ("codigo_contrato", "TEXT"),
            ("endereco_imovel", "TEXT"), ("nome_locatario", "TEXT"), ("cpf_locatario", "TEXT"),
            ("inicio_vigencia", "TEXT"), ("fim_vigencia", "TEXT"), ("status", "TEXT DEFAULT 'Ativo'"),
            ("data_importacao", "TEXT"),
        ],
    }.items():
        for coluna, tipo in colunas:
            if not coluna_existe(cursor, tabela, coluna):
                cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")

    conn.commit()

    # Admin padrão
    cursor.execute("SELECT * FROM usuarios WHERE usuario = 'admin'")
    admin = cursor.fetchone()

    if not admin:
        cursor.execute("""
            INSERT INTO usuarios (nome, cpf, usuario, senha, tipo, senha_definida)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            "Administrador",
            "00000000000",
            "admin",
            generate_password_hash("123"),
            "admin",
            1
        ))

        conn.commit()
        print("ADMIN criado com sucesso.")
    else:
        print("ADMIN já existe.")

    cursor.execute("""
    INSERT OR IGNORE INTO configuracoes (chave, valor)
    VALUES ('excluir_boletos_vencidos', 'nao')
    """)

    cursor.execute("""
    INSERT OR IGNORE INTO configuracoes (chave, valor)
    VALUES ('dias_excluir_boletos_vencidos', '10')
    """)

    conn.commit()

    conn.close()


if __name__ == "__main__":
    criar_tabelas()
