import os
import zipfile
from ftplib import FTP
import io

def baixar_e_descompactar_chikungunya():
    ftp_host = "ftp.datasus.gov.br"
    ftp_path = "/dissemin/publicos/Dados_Abertos/SINAN/Chikungunya/csv/"
    destino = "./dados_chikungunya"

    if not os.path.exists(destino):
        os.makedirs(destino)

    try:
        ftp = FTP(ftp_host)
        ftp.login()
        ftp.cwd(ftp_path)
        
        arquivos = ftp.nlst()
        
        for arquivo in arquivos:
            if arquivo.endswith(".zip"):
                print(f"Baixando e extraindo: {arquivo}...")
                
                with io.BytesIO() as bio:
                    ftp.retrbinary(f"RETR {arquivo}", bio.write)
                    bio.seek(0)
                    
                    with zipfile.ZipFile(bio) as zf:
                        zf.extractall(destino)
                
                print(f"Concluído: {arquivo}")
                    
        ftp.quit()
        print("Tudo pronto!")
        
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    baixar_e_descompactar_chikungunya()
