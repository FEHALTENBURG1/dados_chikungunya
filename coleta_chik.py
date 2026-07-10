import requests
import pandas as pd
import time

def buscar_dados():
    url = "https://apidadosabertos.saude.gov.br/arboviroses/chikungunya"
    params = {'nu_ano': '2026', 'limit': 100, 'offset': 0}
    todos_dados = []
    
    while True:
        resp = requests.get(url, params=params)
        if resp.status_code == 200 and resp.json():
            pagina = resp.json()
            todos_dados.extend(pagina)
            params['offset'] += 100
            time.sleep(0.5) # Respeita a API
        else:
            break
            
    df = pd.DataFrame(todos_dados)
    df.to_csv("chikungunya_atualizado.csv", index=False)

if __name__ == "__main__":
    buscar_dados()
