from pathlib import Path
from werkzeug.utils import secure_filename

EXTENSOES_PDF = {".pdf"}
EXTENSOES_EXCEL = {".xlsx", ".xls"}


def extensao_permitida(nome_arquivo, extensoes):
    return Path(nome_arquivo or "").suffix.lower() in extensoes


def validar_pdf(arquivo):
    if not arquivo or not arquivo.filename:
        return False, "Selecione um arquivo PDF."
    if not extensao_permitida(arquivo.filename, EXTENSOES_PDF):
        return False, "Envie apenas arquivos PDF."
    return True, ""


def validar_excel(arquivo):
    if not arquivo or not arquivo.filename:
        return False, "Selecione uma planilha Excel."
    if not extensao_permitida(arquivo.filename, EXTENSOES_EXCEL):
        return False, "Envie apenas planilhas .xlsx ou .xls."
    return True, ""


def nome_disponivel(pasta, nome_arquivo):
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    nome_seguro = secure_filename(nome_arquivo or "arquivo") or "arquivo"
    destino = pasta / nome_seguro
    if not destino.exists():
        return nome_seguro

    base = destino.stem
    ext = destino.suffix
    contador = 2
    while True:
        candidato = pasta / f"{base}_{contador}{ext}"
        if not candidato.exists():
            return candidato.name
        contador += 1
