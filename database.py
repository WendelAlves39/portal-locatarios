import sqlite3

from werkzeug.security import generate_password_hash


DB_PATH = "portal.db"


def conectar():
    return sqlite3.connect(DB_PATH)


def coluna_existe(cursor, tabela, coluna):
    cursor.execute(f"PRAGMA table_info({tabela})")
    colunas = [linha[1] for linha in cursor.fetchall()]
    return coluna in colunas


def criar_tabelas():
    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        cpf TEXT UNIQUE,
        usuario TEXT UNIQUE,
        senha TEXT,
        contrato TEXT,
        tipo TEXT NOT NULL DEFAULT 'locatario',
        senha_definida INTEGER DEFAULT 0,
        ativo INTEGER DEFAULT 1
    )
    """)

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
    CREATE TABLE IF NOT EXISTS configuracoes (
        chave TEXT PRIMARY KEY,
        valor TEXT
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
