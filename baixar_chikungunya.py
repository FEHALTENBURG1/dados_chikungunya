#!/usr/bin/env python3
"""
Baixa e descompacta os CSVs nacionais de Chikungunya do FTP de Dados Abertos do DATASUS.

O DATASUS publica um arquivo por ano, nomeado CHIKBR<AA>.csv.zip (ano com 2 digitos),
contendo um unico CSV nacional. Nao existe recorte por municipio na origem — o filtro
da RIDE-DF e feito depois, por processar_ride.py.

Desenhado para rodar desassistido no GitHub Actions:
  - falha ruidosamente (exit != 0) para o workflow ficar vermelho e notificar
  - retry com backoff (o FTP do DATASUS e instavel)
  - timeout explicito (evita job pendurado ate o limite de 6h do runner)
  - baixa apenas os anos de interesse
"""

import io
import sys
import time
import zipfile
import logging
from ftplib import FTP, all_errors
from pathlib import Path

FTP_HOST = "ftp.datasus.gov.br"
FTP_PATH = "/dissemin/publicos/Dados_Abertos/SINAN/Chikungunya/csv/"
DESTINO = Path("./bruto")          # fora do Git — ver .gitignore

# Anos de interesse, com 4 digitos. Use None para baixar a serie completa.
ANOS = [2023, 2024, 2025, 2026]

PADRAO = "CHIKBR{:02d}.csv.zip"    # DATASUS usa ano com 2 digitos: CHIKBR26.csv.zip

TIMEOUT = 120          # segundos
TENTATIVAS = 4         # por arquivo
ESPERA_BASE = 5        # segundos, dobra a cada falha

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chik")


def nomes_esperados() -> set:
    """Monta os nomes exatos dos arquivos a partir dos anos configurados."""
    if ANOS is None:
        return set()
    return {PADRAO.format(ano % 100) for ano in ANOS}


def conectar() -> FTP:
    """Abre conexao anonima em modo passivo."""
    ftp = FTP(FTP_HOST, timeout=TIMEOUT)
    ftp.login()              # anonimo
    ftp.set_pasv(True)       # DATASUS exige passivo
    ftp.cwd(FTP_PATH)
    return ftp


def fechar(ftp: FTP) -> None:
    try:
        ftp.quit()
    except all_errors:
        ftp.close()


def interessa(nome: str) -> bool:
    """Nome exato bate com um dos anos pedidos? (comparacao exata, nao substring)"""
    if ANOS is None:
        return nome.lower().endswith(".zip")
    return nome in nomes_esperados()


def extrair_seguro(bio: io.BytesIO, destino: Path) -> list:
    """Extrai o ZIP validando integridade e nomes (evita path traversal)."""
    escritos = []
    with zipfile.ZipFile(bio) as zf:
        ruim = zf.testzip()
        if ruim is not None:
            raise zipfile.BadZipFile(f"Arquivo corrompido dentro do ZIP: {ruim}")
        raiz = str(destino.resolve())
        for membro in zf.namelist():
            alvo = str((destino / membro).resolve())
            if not alvo.startswith(raiz):
                raise ValueError(f"Caminho suspeito no ZIP: {membro}")
            zf.extract(membro, destino)
            escritos.append(membro)
    return escritos


def baixar_arquivo(ftp: FTP, arquivo: str, destino: Path) -> list:
    """Baixa um ZIP para memoria e extrai. Retry fica a cargo de quem chama."""
    bio = io.BytesIO()
    ftp.retrbinary(f"RETR {arquivo}", bio.write)
    bio.seek(0)
    log.info("  baixado (%.1f MB), extraindo...", len(bio.getvalue()) / 1_048_576)
    return extrair_seguro(bio, destino)


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)

    ftp = conectar()
    try:
        disponiveis = sorted(ftp.nlst())
    finally:
        fechar(ftp)

    alvos = sorted(a for a in disponiveis if interessa(a))

    if not alvos:
        # Quase sempre significa que o DATASUS mudou o padrao de nome dos arquivos.
        # Falhar aqui e melhor que seguir com a pasta vazia e o painel congelado.
        raise RuntimeError(
            "Nenhum ZIP encontrado.\n"
            f"  Esperado:   {sorted(nomes_esperados())}\n"
            f"  Disponivel: {disponiveis}"
        )

    faltando = nomes_esperados() - set(disponiveis)
    if faltando:
        # Nao e erro: o ano corrente pode ainda nao ter sido publicado.
        log.warning("Anos pedidos mas ausentes no FTP: %s", sorted(faltando))

    log.info("%d arquivo(s) a baixar: %s", len(alvos), ", ".join(alvos))

    falhas = []
    for arquivo in alvos:
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                log.info("Baixando %s (tentativa %d/%d)", arquivo, tentativa, TENTATIVAS)
                # Reconecta a cada arquivo: o DATASUS derruba sessoes longas.
                ftp = conectar()
                try:
                    escritos = baixar_arquivo(ftp, arquivo, DESTINO)
                finally:
                    fechar(ftp)
                log.info("  OK -> %s", ", ".join(escritos))
                break
            except (all_errors, zipfile.BadZipFile, OSError) as e:
                log.warning("  falhou (%s: %s)", type(e).__name__, e)
                if tentativa == TENTATIVAS:
                    falhas.append(arquivo)
                    log.error("  desistindo de %s", arquivo)
                else:
                    espera = ESPERA_BASE * (2 ** (tentativa - 1))
                    log.info("  aguardando %ds...", espera)
                    time.sleep(espera)

    if falhas:
        raise RuntimeError(f"Falha ao baixar: {', '.join(falhas)}")

    csvs = sorted(DESTINO.glob("*.csv"))
    if not csvs:
        raise RuntimeError("Downloads OK, mas nenhum CSV foi extraido. ZIP vazio?")

    for c in csvs:
        log.info("  %s (%.1f MB)", c.name, c.stat().st_size / 1_048_576)
    log.info("Tudo pronto. %d CSV(s) em %s/", len(csvs), DESTINO)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("ERRO FATAL: %s: %s", type(e).__name__, e)
        sys.exit(1)   # essencial: sem isso o Actions marca o job como sucesso
