# Carregamento de Pacotes
suppressPackageStartupMessages({
  library(dplyr)
  library(readr)
  library(jsonlite)
  library(lubridate)
  library(spdep)
})

# 1. Ingestão de Dados
# Certifique-se de que os caminhos refletem a raiz do seu repositório
casos <- read_csv("dados/chikungunya_ride.csv", show_col_types = FALSE)
pop <- read_csv("populacao_ride.csv", show_col_types = FALSE)

# 2. Tratamento e Classificação (Filtro RIDE-DF)
codigos_ride <- as.character(pop$cod6)

dados_limpos <- casos %>%
  mutate(
    ID_MN_RESI = as.character(ID_MN_RESI),
    ANO_EPI = as.numeric(substr(SEM_PRI, 1, 4)),
    SEMANA = as.numeric(substr(SEM_PRI, 5, 6)),
    CONFIRMADO = CLASSI_FIN == "13",
    DESCARTADO = CLASSI_FIN == "5",
    EM_INVESTIGACAO = CLASSI_FIN %in% c("", "0", "8", "9"),
    PCR_REAGENTE = RESUL_PCR_ == "1",
    PCR_NAO_REAGENTE = RESUL_PCR_ == "2",
    FORA_MUNICIPIO = FORA_DO_MUN == "1" | (!is.na(ID_MN_RESI) & !is.na(ID_MUNICIP) & ID_MN_RESI != ID_MUNICIP)
  ) %>%
  filter(ID_MN_RESI %in% codigos_ride, ANO_EPI >= 2021)

ano_atual <- max(dados_limpos$ANO_EPI, na.rm = TRUE)
casos_ano_atual <- dados_limpos %>% filter(ANO_EPI == ano_atual)

# 3. Modelagem Espacial: Taxa Bayesiana Empírica
# Substitui o cálculo de incidência bruta que gerava ruído visual
resumo_municipal <- casos_ano_atual %>%
  group_by(ID_MN_RESI) %>%
  summarise(
    notificados = n(),
    confirmados = sum(CONFIRMADO, na.rm = TRUE),
    evasao = sum(FORA_MUNICIPIO, na.rm = TRUE)
  ) %>%
  right_join(pop %>% mutate(cod6 = as.character(cod6)), by = c("ID_MN_RESI" = "cod6")) %>%
  mutate(
    notificados = coalesce(notificados, 0),
    confirmados = coalesce(confirmados, 0),
    evasao = coalesce(evasao, 0),
    populacao = as.numeric(populacao)
  )

# Aplicação do Estimador de Marshall (Empirical Bayes) via spdep
bayes_est <- EBest(resumo_municipal$confirmados, resumo_municipal$populacao)

resumo_municipal <- resumo_municipal %>%
  mutate(
    incidencia_bruta = (confirmados / populacao) * 100000,
    incidencia_bayes = (bayes_est$estmm) * 100000, # Taxa suavizada
    taxa_notificacao = (notificados / populacao) * 100000,
    pct_evasao = ifelse(notificados > 0, (evasao / notificados) * 100, 0)
  )

# 4. Canal Endêmico Paramétrico (Séries Históricas)
historico_semanal <- dados_limpos %>%
  filter(ANO_EPI %in% c(2021, 2023, 2024, ano_atual - 1)) %>%
  filter(CONFIRMADO == TRUE) %>%
  group_by(SEMANA) %>%
  summarise(
    media_casos = mean(n()),
    sd_casos = sd(n())
  ) %>%
  mutate(
    sd_casos = replace_na(sd_casos, 0),
    limiar_seguranca = media_casos,
    limiar_alerta = media_casos + sd_casos,
    limiar_epidemia = media_casos + (1.96 * sd_casos)
  )

# 5. Agregação Semanal (Ano Atual)
semanal_atual <- casos_ano_atual %>%
  group_by(SEMANA) %>%
  summarise(
    notificados = n(),
    confirmados = sum(CONFIRMADO, na.rm = TRUE),
    pcr_reagente = sum(PCR_REAGENTE, na.rm = TRUE),
    pcr_nao = sum(PCR_NAO_REAGENTE, na.rm = TRUE)
  ) %>%
  mutate(
    pcr_conclusivos = pcr_reagente + pcr_nao,
    positividade = ifelse(pcr_conclusivos > 0, (pcr_reagente / pcr_conclusivos) * 100, 0)
  ) %>%
  left_join(historico_semanal, by = "SEMANA")

# 6. Estruturação do JSON
output_list <- list(
  metadados = list(
    ano_epidemiologico = ano_atual,
    data_processamento = Sys.time(),
    casos_confirmados = sum(resumo_municipal$confirmados),
    notificacoes_totais = sum(resumo_municipal$notificados)
  ),
  dados_semanais = semanal_atual,
  dados_municipais = resumo_municipal
)

# 7. Exportação
dir.create("data_processed", showWarnings = FALSE)
write_json(output_list, "data_processed/painel_chik.json", pretty = TRUE, auto_unbox = TRUE)
