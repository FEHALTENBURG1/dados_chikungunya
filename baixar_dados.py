import requests
import pandas as pd
import time

url = "https://apidadosabertos.saude.gov.br/arboviroses/chikungunya"
ano = "2026"
limit = 100
offset = 0
todos_dados = []

print("Iniciando coleta de dados...")

while True:
    params = {
        'nu_ano': ano,
        'limit': limit,
        'offset': offset
    }
    
    response = requests.get(url, params=params)
    
    # Verifica se a requisição foi bem sucedida
    if response.status_code == 200:
        dados = response.json()
        
        # Se a lista de dados estiver vazia, encerramos o loop
        if not dados:
            print("Coleta finalizada.")
            break
            
        todos_dados.extend(dados)
        print(f"Coletados {len(todos_dados)} registros... (Offset atual: {offset})")
        
        # Incrementa o offset para a próxima página
        offset += limit
        
        # Pequena pausa para não sobrecarregar a API
        time.sleep(0.5)
    else:
        print(f"Erro na requisição: {response.status_code}")
        break

# Converte para DataFrame e salva o arquivo
df = pd.DataFrame(todos_dados)
df.to_csv("chikungunya_completo_2026.csv", index=False)
print("Arquivo 'chikungunya_completo_2026.csv' salvo com sucesso!")
