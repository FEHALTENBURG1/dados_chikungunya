"""
Municípios da RIDE-DF — 34 unidades (29 GO + 4 MG + Distrito Federal).

Composição: Lei Complementar nº 163/2018.
Códigos: tabela oficial de composição de regiões metropolitanas (COD_MUN, IBGE).

Chave = código IBGE de 7 dígitos.
Valor = (código de 6 dígitos usado pelo DATASUS/SINAN, nome, UF).

O DATASUS omite o dígito verificador (o último) nos campos de município do SINAN.
Verificado: os 34 códigos de 6 dígitos são únicos entre si — o corte não gera ambiguidade.
"""

MUNICIPIOS_RIDE = {
    "5300108": ("530010", "Brasília", "DF"),
    "5200100": ("520010", "Abadiânia", "GO"),
    "5200308": ("520030", "Alexânia", "GO"),
    "5200605": ("520060", "Alto Paraíso de Goiás", "GO"),
    "5200803": ("520080", "Alvorada do Norte", "GO"),
    "5203203": ("520320", "Barro Alto", "GO"),
    "5204003": ("520400", "Cabeceiras", "GO"),
    "5205307": ("520530", "Cavalcante", "GO"),
    "5205497": ("520549", "Cidade Ocidental", "GO"),
    "5205513": ("520551", "Cocalzinho de Goiás", "GO"),
    "5205802": ("520580", "Corumbá de Goiás", "GO"),
    "5206206": ("520620", "Cristalina", "GO"),
    "5207907": ("520790", "Flores de Goiás", "GO"),
    "5208004": ("520800", "Formosa", "GO"),
    "5208608": ("520860", "Goianésia", "GO"),
    "5212501": ("521250", "Luziânia", "GO"),
    "5213053": ("521305", "Mimoso de Goiás", "GO"),
    "5214606": ("521460", "Niquelândia", "GO"),
    "5215231": ("521523", "Novo Gama", "GO"),
    "5215603": ("521560", "Padre Bernardo", "GO"),
    "5217302": ("521730", "Pirenópolis", "GO"),
    "5217609": ("521760", "Planaltina", "GO"),
    "5219753": ("521975", "Santo Antônio do Descoberto", "GO"),
    "5220686": ("522068", "Simolândia", "GO"),
    "5220009": ("522000", "São João d'Aliança", "GO"),
    "5221858": ("522185", "Valparaíso de Goiás", "GO"),
    "5222203": ("522220", "Vila Boa", "GO"),
    "5222302": ("522230", "Vila Propício", "GO"),
    "5200175": ("520017", "Água Fria de Goiás", "GO"),
    "5200258": ("520025", "Águas Lindas de Goiás", "GO"),
    "3104502": ("310450", "Arinos", "MG"),
    "3109303": ("310930", "Buritis", "MG"),
    "3109451": ("310945", "Cabeceira Grande", "MG"),
    "3170404": ("317040", "Unaí", "MG"),
}

CODIGOS_7 = set(MUNICIPIOS_RIDE)
CODIGOS_6 = {v[0] for v in MUNICIPIOS_RIDE.values()}
NOME_POR_COD6 = {v[0]: v[1] for v in MUNICIPIOS_RIDE.values()}
UF_POR_COD6 = {v[0]: v[2] for v in MUNICIPIOS_RIDE.values()}


def na_ride(codigo) -> bool:
    """Aceita código de 6 ou 7 dígitos, em str ou int."""
    if codigo is None:
        return False
    c = str(codigo).strip()
    return c in CODIGOS_7 or c in CODIGOS_6 or c[:6] in CODIGOS_6
