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

- **Coletores plugáveis** (`src/housing_radar/collectors/`):
  - `manual` — CSV (base confiável, ToS-safe);
  - `cardinali` — imobiliária local, HTML, paginação `?pag=N` (acervo grande);
  - `roca` / `iplano` / `top` — plataforma **MSYS Imob** (um coletor genérico,
    `msys.py`, cobre as três e já traz lat/lon, dispensando geocoding);
  - `olx` — best-effort (a OLX ignora o filtro de região na URL; rende pouco).
  - `all` — roda todas as fontes remotas de uma vez.
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

# 2) Coletar das imobiliárias locais + rodar o pipeline completo (todas as fontes)
uv run housing-radar run all -p 3       # ou: run cardinali / run roca / ...

# 2b) Acervo COMPLETO das imobiliárias MSYS (varre o sitemap, educado e incremental)
uv run housing-radar collect roca --full --cap 300   # repita p/ avançar 300 por vez
uv run housing-radar enrich

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
| `collect [all\|cardinali\|roca\|iplano\|top\|olx]` | Coleta de uma/todas as fontes (sem enriquecer) |
| `enrich` | Geocoda, calcula tempo até a UFSCar e (re)calcula o score |
| `run [all\|...]` | Pipeline completo (coleta + enrich) |
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

- [x] Coletores de imobiliárias locais de São Carlos (Cardinali + plataforma MSYS)
- [x] **Acervo completo MSYS** via sitemap (`collect <fonte> --full`) — educado (cap + delay) e incremental (pula ids já no banco)
- [ ] **Cardinali:** abrir páginas de detalhe p/ preencher área/bairro faltantes (~30% dos cards)
- [ ] ZAP/VivaReal/QuintoAndar e OLX (location real) — sob demanda, se faltar volume
- [ ] Tempo de ônibus (GTFS São Carlos ou Google Distance Matrix)
- [ ] Calibrar pesos do score com avaliações reais (com a mãe corretora)
- [ ] Migrar SQLite → Postgres quando o volume crescer
- [ ] Alertas de novos anúncios acima de um score
