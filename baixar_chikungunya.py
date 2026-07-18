#!/usr/bin/env python3
"""
Baixa e descompacta os CSVs nacionais de Chikungunya.

Fontes:
- 2021 a 2025: FTP do DATASUS
- 2026: arquivo HTTPS/S3 do Portal de Dados Abertos do Ministério da Saúde

O arquivo extraído é sempre salvo no formato:
    bruto/CHIKBR<AA>.csv

Desenhado para execução automática no GitHub Actions:
- usa apenas a biblioteca padrão do Python;
- faz novas tentativas com espera progressiva;
- valida a integridade do ZIP;
- grava em arquivo temporário antes de substituir o arquivo anterior;
- encerra com erro quando um download obrigatório falha.
"""

from __future__ import annotations

import logging
import shutil
import sys
import time
import zipfile
from ftplib import FTP, all_errors
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# CONFIGURAÇÃO
# ---------------------------------------------------------------------------

DESTINO = Path("./bruto")

ANOS_FTP = [2021, 2022, 2023, 2024, 2025]

URLS_HTTPS = {
    2026: (
        "https://s3.sa-east-1.amazonaws.com/"
        "ckan.saude.gov.br/SINAN/Chikungunya/csv/CHIKBR26.csv.zip"
    )
}

FTP_HOST = "ftp.datasus.gov.br"
FTP_PATH = "/dissemin/publicos/Dados_Abertos/SINAN/Chikungunya/csv/"

TIMEOUT = 180
TENTATIVAS = 4
ESPERA_BASE = 5
TAMANHO_BLOCO = 1024 * 1024  # 1 MB


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("chik")

ERROS_DOWNLOAD = all_errors + (
    HTTPError,
    URLError,
    TimeoutError,
    zipfile.BadZipFile,
    OSError,
    RuntimeError,
)


# ---------------------------------------------------------------------------
# FUNÇÕES AUXILIARES
# ---------------------------------------------------------------------------

def nome_zip(ano: int) -> str:
    """Nome publicado pelo DATASUS."""
    return f"CHIKBR{ano % 100:02d}.csv.zip"


def nome_csv(ano: int) -> str:
    """Nome estável usado pelo processar_ride.py."""
    return f"CHIKBR{ano % 100:02d}.csv"


def tamanho_mb(caminho: Path) -> float:
    return caminho.stat().st_size / 1_048_576


def fechar_ftp(ftp: FTP) -> None:
    try:
        ftp.quit()
    except all_errors:
        try:
            ftp.close()
        except all_errors:
            pass


def baixar_https(url: str, destino_zip: Path) -> None:
    """
    Baixa o ZIP por HTTPS em fluxo, sem carregar o arquivo inteiro na memória.
    """
    temporario = destino_zip.with_suffix(destino_zip.suffix + ".part")
    temporario.unlink(missing_ok=True)

    requisicao = Request(
        url,
        headers={
            "User-Agent": "dados-chikungunya-github-actions/1.0",
            "Accept": "application/zip,application/octet-stream,*/*",
            "Cache-Control": "no-cache",
        },
    )

    try:
        with urlopen(requisicao, timeout=TIMEOUT) as resposta:
            status = getattr(resposta, "status", 200)
            if status != 200:
                raise RuntimeError(f"Resposta HTTP inesperada: {status}")

            with temporario.open("wb") as arquivo:
                shutil.copyfileobj(resposta, arquivo, length=TAMANHO_BLOCO)

        if not temporario.exists() or temporario.stat().st_size == 0:
            raise RuntimeError("O servidor retornou um arquivo vazio.")

        temporario.replace(destino_zip)

    except Exception:
        temporario.unlink(missing_ok=True)
        raise


def baixar_ftp(ano: int, destino_zip: Path) -> None:
    """
    Baixa um ZIP do FTP do DATASUS.
    """
    arquivo_remoto = nome_zip(ano)
    temporario = destino_zip.with_suffix(destino_zip.suffix + ".part")
    temporario.unlink(missing_ok=True)

    ftp = FTP(FTP_HOST, timeout=TIMEOUT)

    try:
        ftp.login()
        ftp.set_pasv(True)
        ftp.cwd(FTP_PATH)

        with temporario.open("wb") as arquivo:
            ftp.retrbinary(
                f"RETR {arquivo_remoto}",
                arquivo.write,
                blocksize=TAMANHO_BLOCO,
            )

        if not temporario.exists() or temporario.stat().st_size == 0:
            raise RuntimeError("O FTP retornou um arquivo vazio.")

        temporario.replace(destino_zip)

    except Exception:
        temporario.unlink(missing_ok=True)
        raise

    finally:
        fechar_ftp(ftp)


def extrair_csv_seguro(caminho_zip: Path, ano: int, destino: Path) -> Path:
    """
    Valida o ZIP e extrai exatamente um CSV.

    O arquivo interno do ZIP é renomeado para CHIKBR<AA>.csv, garantindo
    compatibilidade com processar_ride.py mesmo que o nome interno mude.
    """
    saida_csv = destino / nome_csv(ano)
    temporario = saida_csv.with_suffix(saida_csv.suffix + ".part")
    temporario.unlink(missing_ok=True)

    try:
        with zipfile.ZipFile(caminho_zip) as zf:
            arquivo_corrompido = zf.testzip()
            if arquivo_corrompido is not None:
                raise zipfile.BadZipFile(
                    f"Arquivo corrompido dentro do ZIP: {arquivo_corrompido}"
                )

            membros_csv = [
                membro
                for membro in zf.infolist()
                if not membro.is_dir()
                and membro.filename.lower().endswith(".csv")
            ]

            if len(membros_csv) != 1:
                nomes = [m.filename for m in membros_csv]
                raise RuntimeError(
                    "Era esperado exatamente um CSV no ZIP, "
                    f"mas foram encontrados {len(membros_csv)}: {nomes}"
                )

            with zf.open(membros_csv[0], "r") as origem, temporario.open("wb") as destino_arq:
                shutil.copyfileobj(origem, destino_arq, length=TAMANHO_BLOCO)

        if not temporario.exists() or temporario.stat().st_size == 0:
            raise RuntimeError("O CSV extraído está vazio.")

        temporario.replace(saida_csv)
        return saida_csv

    except Exception:
        temporario.unlink(missing_ok=True)
        raise


def baixar_com_retry(ano: int, fonte: str) -> Path:
    """
    Baixa, valida e extrai um ano, repetindo em caso de falha.
    """
    arquivo_zip = DESTINO / nome_zip(ano)

    for tentativa in range(1, TENTATIVAS + 1):
        try:
            log.info(
                "Baixando %s pela fonte %s (tentativa %d/%d)",
                nome_zip(ano),
                fonte,
                tentativa,
                TENTATIVAS,
            )

            if fonte == "HTTPS":
                baixar_https(URLS_HTTPS[ano], arquivo_zip)
            elif fonte == "FTP":
                baixar_ftp(ano, arquivo_zip)
            else:
                raise ValueError(f"Fonte desconhecida: {fonte}")

            log.info(
                "ZIP recebido: %s (%.1f MB)",
                arquivo_zip.name,
                tamanho_mb(arquivo_zip),
            )

            csv = extrair_csv_seguro(arquivo_zip, ano, DESTINO)

            log.info(
                "OK -> %s (%.1f MB)",
                csv,
                tamanho_mb(csv),
            )

            # O ZIP não é necessário depois da extração.
            arquivo_zip.unlink(missing_ok=True)
            return csv

        except ERROS_DOWNLOAD as erro:
            arquivo_zip.unlink(missing_ok=True)

            log.warning(
                "Falha no ano %d (%s: %s)",
                ano,
                type(erro).__name__,
                erro,
            )

            if tentativa == TENTATIVAS:
                raise RuntimeError(
                    f"Falha definitiva ao baixar o ano {ano} pela fonte {fonte}."
                ) from erro

            espera = ESPERA_BASE * (2 ** (tentativa - 1))
            log.info("Aguardando %d segundos antes de tentar novamente...", espera)
            time.sleep(espera)

    raise RuntimeError(f"Falha inesperada no ano {ano}.")


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------

def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)

    arquivos_gerados: list[Path] = []

    # Série histórica mantida no FTP.
    for ano in ANOS_FTP:
        arquivos_gerados.append(baixar_com_retry(ano, "FTP"))

    # Ano corrente obtido diretamente do S3.
    for ano in sorted(URLS_HTTPS):
        arquivos_gerados.append(baixar_com_retry(ano, "HTTPS"))

    esperados = {
        DESTINO / nome_csv(ano)
        for ano in [*ANOS_FTP, *sorted(URLS_HTTPS)]
    }

    ausentes = sorted(str(caminho) for caminho in esperados if not caminho.exists())
    if ausentes:
        raise RuntimeError(
            "A execução terminou, mas faltam CSVs esperados: "
            + ", ".join(ausentes)
        )

    log.info("")
    log.info("Tudo pronto. %d CSV(s) em %s/", len(arquivos_gerados), DESTINO)

    for csv in sorted(arquivos_gerados):
        log.info("  %s (%.1f MB)", csv.name, tamanho_mb(csv))


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        log.exception("ERRO FATAL: %s: %s", type(erro).__name__, erro)
        sys.exit(1)
