#!/usr/bin/env python3
"""
Baixa e descompacta os CSVs de Chikungunya do FTP de Dados Abertos do DATASUS.
Desenhado para rodar de forma desassistida no GitHub Actions:
  - falha ruidosamente (exit != 0) para o workflow ficar vermelho e notificar
  - retry com backoff (o FTP do DATASUS é instável)
  - timeout explícito (evita job pendurado até o limite de 6h do runner)
  - baixa apenas os anos de interesse
"""

import io
import os
import sys
import time
import zipfile
import logging
from ftplib import FTP, all_errors
from pathlib import Path

FTP_HOST = "ftp.datasus.gov.br"
FTP_PATH = "/dissemin/publicos/Dados_Abertos/SINAN/Chikungunya/csv/"
DESTINO = Path("./dados_chikungunya")

# Só os anos que o painel usa. Ajuste conforme necessário.
# Deixe ANOS = None para baixar tudo (histórico completo, pesado).
ANOS = ["2023", "2024", "2025", "2026"]

TIMEOUT = 120          # segundos
TENTATIVAS = 4         # por arquivo
ESPERA_BASE = 5        # segundos, dobra a cada falha

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chik")


def conectar() -> FTP:
    """Abre conexão anônima em modo passivo."""
    ftp = FTP(FTP_HOST, timeout=TIMEOUT)
    ftp.login()              # anônimo
    ftp.set_pasv(True)       # DATASUS exige passivo
    ftp.cwd(FTP_PATH)
    return ftp


def interessa(nome: str) -> bool:
    """Filtra por extensão e, se ANOS estiver definido, pelo ano no nome do arquivo."""
    if not nome.lower().endswith(".zip"):
        return False
    if ANOS is None:
        return True
    return any(ano in nome for ano in ANOS)


def extrair_seguro(bio: io.BytesIO, destino: Path) -> list[str]:
    """Extrai o ZIP validando os nomes (evita path traversal) e devolve os arquivos escritos."""
    escritos = []
    with zipfile.ZipFile(bio) as zf:
        ruim = zf.testzip()
        if ruim is not None:
            raise zipfile.BadZipFile(f"Arquivo corrompido dentro do ZIP: {ruim}")
        for membro in zf.namelist():
            alvo = (destino / membro).resolve()
            if not str(alvo).startswith(str(destino.resolve())):
                raise ValueError(f"Caminho suspeito no ZIP: {membro}")
            zf.extract(membro, destino)
            escritos.append(membro)
    return escritos


def baixar_arquivo(ftp: FTP, arquivo: str, destino: Path) -> list[str]:
    """Baixa um ZIP para memória e extrai. Não faz retry — quem chama cuida disso."""
    bio = io.BytesIO()
    ftp.retrbinary(f"RETR {arquivo}", bio.write)
    bio.seek(0)
    tamanho_mb = len(bio.getvalue()) / 1_048_576
    log.info("  baixado (%.1f MB), extraindo...", tamanho_mb)
    return extrair_seguro(bio, destino)


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)

    ftp = conectar()
    try:
        todos = ftp.nlst()
        alvos = sorted(a for a in todos if interessa(a))
    finally:
        try:
            ftp.quit()
        except all_errors:
            ftp.close()

    if not alvos:
        # Nenhum arquivo bate com o filtro: quase sempre significa que o DATASUS
        # mudou o padrão de nome dos arquivos. Falhar aqui é melhor que seguir vazio.
        raise RuntimeError(
            f"Nenhum ZIP encontrado com os anos {ANOS}. "
            f"Arquivos disponíveis no FTP: {sorted(todos)[:20]}"
        )

    log.info("%d arquivo(s) a baixar: %s", len(alvos), ", ".join(alvos))

    falhas = []
    for arquivo in alvos:
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                log.info("Baixando %s (tentativa %d/%d)", arquivo, tentativa, TENTATIVAS)
                # Reconecta a cada arquivo: o DATASUS derruba sessões longas.
                ftp = conectar()
                try:
                    escritos = baixar_arquivo(ftp, arquivo, DESTINO)
                finally:
                    try:
                        ftp.quit()
                    except all_errors:
                        ftp.close()
                log.info("  OK -> %s", ", ".join(escritos))
                break
            except (all_errors, zipfile.BadZipFile, OSError) as e:
                espera = ESPERA_BASE * (2 ** (tentativa - 1))
                log.warning("  falhou (%s: %s)", type(e).__name__, e)
                if tentativa == TENTATIVAS:
                    falhas.append(arquivo)
                    log.error("  desistindo de %s", arquivo)
                else:
                    log.info("  aguardando %ds...", espera)
                    time.sleep(espera)

    if falhas:
        # Sai com código != 0 -> o workflow fica VERMELHO e o GitHub te notifica.
        raise RuntimeError(f"Falha ao baixar: {', '.join(falhas)}")

    baixados = sorted(p.name for p in DESTINO.glob("*.csv"))
    log.info("Tudo pronto. %d CSV(s) em %s: %s", len(baixados), DESTINO, ", ".join(baixados))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("ERRO FATAL: %s: %s", type(e).__name__, e)
        sys.exit(1)   # <- essencial: sem isso o Actions marca o job como sucesso
