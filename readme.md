
---

# dados_chikungunya: Epidemiological Workflow

This repository contains an automated, reproducible workflow for the ingestion, cleaning, and analysis of Chikungunya epidemiological surveillance data in the Federal District (Distrito Federal), Brazil.

## Project Overview

This workflow serves as a bridge between raw notification data (SINAN/SUS) and actionable clinical intelligence. It is designed to support the **Residência Multiprofissional em Saúde Digital do HUBRASIL** by providing standardized, high-quality datasets for public health monitoring.

## Technical Domain Specifications

| Field | Description |
| --- | --- |
| **Primary Data Source** | Sistema de Informação de Agravos de Notificação (SINAN) - Ministério da Saúde/Brasil |
| **Geographic Scope** | Distrito Federal, Brazil (ISO 3166-2:BR-DF) |
| **Temporal Coverage** | 2021 – Present |
| **Subject Category** | Public Health, Epidemiology, Arboviral Diseases |
| **Data Format** | CSV (UTF-8), standardized for longitudinal analysis |
| **Language** | Portuguese (PT-BR) |

## Methodology

The pipeline follows a modular architecture:

1. **Ingestion:** Automated retrieval of raw case notification files from 2021 to the present.
2. **Cleaning:** Systematic validation of variable consistency, date formatting, and application of standard epidemiological case definitions.
3. **Analysis:** Aggregation and computation of epidemiological indicators to support clinical situation reports (sitreps).

## How to Cite

If you utilize this workflow or the processed data in your research or clinical reports, please cite it as:

> Haltenburg, F. (2026). *dados_chikungunya: Epidemiological Workflow for Chikungunya Data in the Federal District* [workflow]. Zenodo. [https://doi.org/10.5281/zenodo.21458604](https://doi.org/10.5281/zenodo.21458604)

## Usage

To reproduce the analysis or incorporate this data into your local tools:

1. Clone the repository: `git clone [https://github.com/FEHALTENBURG1/dados_chikungunya.git](https://github.com/FEHALTENBURG1/dados_chikungunya.git)`
2. Install dependencies: `pip install -r requirements.txt`
3. Run the pipeline: `python main.py`

## Privacy & Ethics

All datasets processed within this repository are from public sources to protect individual privacy in accordance with Brazilian data protection laws (LGPD). No Protected Health Information (PHI) is included.

## License

This project is licensed under the **Creative Commons Attribution 4.0 International (CC-BY 4.0)** license.
