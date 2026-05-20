import sqlite3


def conectar():
    conn = sqlite3.connect("portal.db")
    return conn


def criar_tabelas():

    conn = conectar()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (

        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        usuario TEXT NOT NULL UNIQUE,
        senha TEXT NOT NULL,
        tipo TEXT NOT NULL

    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS boletos (

        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome_arquivo TEXT NOT NULL,
        usuario_locatario TEXT NOT NULL,
        valor TEXT,
        vencimento TEXT,
        status TEXT DEFAULT 'Em aberto'

    )
    """)

    try:
        cursor.execute("ALTER TABLE boletos ADD COLUMN valor TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE boletos ADD COLUMN vencimento TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE boletos ADD COLUMN status TEXT DEFAULT 'Em aberto'")
    except sqlite3.OperationalError:
        pass

    conn.commit()

    cursor.execute("""
    SELECT * FROM usuarios
    WHERE usuario = 'admin'
    """)

    admin = cursor.fetchone()

    if not admin:
        cursor.execute("""
        INSERT INTO usuarios
        (nome, usuario, senha, tipo)
        VALUES (?, ?, ?, ?)
        """, (
            "Administrador",
            "admin",
            "123",
            "admin"
        ))

        conn.commit()
        print("ADMIN criado com sucesso!")
    else:
        print("ADMIN já existe.")

    conn.close()


criar_tabelas()