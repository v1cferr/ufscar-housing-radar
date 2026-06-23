# 🏢 UFSCar Housing Radar

Pipeline semi-automatizado para **coletar, normalizar, geolocalizar, ranquear e servir**
apartamentos próximos à **UFSCar / FAI.UFSCar** (São Carlos – SP).

A ideia (card [V1C-68](https://v1cferr.atlassian.net/browse/V1C-68)): sair da busca por
"impressão" e transformar a procura de imóvel num processo baseado em dados — gerar uma
lista qualificada e comparável para discutir com mais maturidade na hora da decisão.

> ⚠️ Etapa de **preparação**, não de compra. E o scraping respeita baixo volume e os
> termos de uso das plataformas.

## Stack

`uv` + Python 3.13 · **FastAPI** (API + dashboard) · SQLite (via SQLModel) · `httpx` +
BeautifulSoup (coleta) · `geopy`/Nominatim (geocoding) · Docker + docker-compose, atrás
de **Caddy** (TLS + domínio).

## Como funciona

```
coletar → normalizar → deduplicar → geocodar → tempo até a UFSCar → score → exportar/servir
```

- **Coletores plugáveis** (`src/housing_radar/collectors/`): `manual` (CSV) e `olx`
  (best-effort). ZAP/VivaReal/QuintoAndar entram depois — a interface já está pronta.
- **Tempo até a UFSCar**: estimativa por distância + velocidade média (a pé/bici/carro)
  sem nenhuma chave; fica preciso por modal se você setar `HR_ORS_API_KEY`
  (OpenRouteService). Ônibus exige GTFS/Google e está no roadmap.
- **Score 0–100** transparente e configurável (`pipeline/score.py`): proximidade, preço,
  área, condomínio, quartos e vagas, com pesos e âncoras calibráveis.

## Quickstart

```bash
uv sync                                            # cria o venv e instala tudo

# 1) Semear com o CSV de exemplo e rodar o enriquecimento
uv run housing-radar import-csv data/seed/listings_example.csv
uv run housing-radar enrich        # geocode (se faltar) + tempo até UFSCar + score
uv run housing-radar stats         # ranking no terminal

# 2) Coletar da OLX (best-effort) e rodar o pipeline completo
uv run housing-radar run olx -p 3

# 3) Exportar planilha para mandar pra avaliação
uv run housing-radar export --fmt xlsx     # -> exports/apartamentos_ufscar.xlsx

# 4) Subir a API + dashboard
uv run housing-radar serve --reload        # http://localhost:8000
```

Copie `.env.example` para `.env` para ajustar coordenadas da UFSCar, chave do ORS, etc.

### Comandos da CLI

| Comando | O que faz |
|---|---|
| `init-db` | Cria as tabelas |
| `import-csv PATH` | Importa anúncios manuais de um CSV |
| `collect [olx]` | Coleta de uma fonte remota (sem enriquecer) |
| `enrich` | Geocoda, calcula tempo até a UFSCar e (re)calcula o score |
| `run [olx]` | Pipeline completo de uma fonte (coleta + enrich) |
| `export --fmt xlsx\|csv` | Exporta o ranking para planilha |
| `stats --top N` | Resumo + melhores anúncios |
| `serve` | Sobe a API/dashboard (uvicorn) |

## Deploy (Docker + Caddy)

O app escuta HTTP na porta 8000 e fica **atrás do Caddy**, que cuida de TLS e domínio.

```bash
docker compose up -d --build         # app em 127.0.0.1:8000
```

No seu Caddy central (host), adicione o bloco de `Caddyfile.example` (caso A). Para uma
stack self-contained com Caddy junto: `docker compose --profile edge up -d --build`.

## Roadmap

- [ ] Coletores ZAP/VivaReal/QuintoAndar + imobiliárias locais de São Carlos
- [ ] Mapear bairros mais próximos da UFSCar e usar como referência
- [ ] Tempo de ônibus (GTFS São Carlos ou Google Distance Matrix)
- [ ] Calibrar pesos do score com avaliações reais
- [ ] Migrar SQLite → Postgres quando o volume crescer
- [ ] Alertas de novos anúncios acima de um score
