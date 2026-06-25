# UFSCar Housing Radar

Pipeline semi-automatizado para **coletar, normalizar, geolocalizar, ranquear e servir**
apartamentos próximos à **UFSCar / FAI.UFSCar** (São Carlos – SP).

A ideia (card [V1C-68](https://v1cferr.atlassian.net/browse/V1C-68)): sair da busca por
"impressão" e transformar a procura de imóvel num processo baseado em dados — gerar uma
lista qualificada e comparável para discutir com mais maturidade na hora da decisão.

> Etapa de **preparação**, não de compra. E o scraping respeita baixo volume e os
> termos de uso das plataformas.

## Stack

`uv` + Python 3.13 · **FastAPI** (API JSON + shell HTML) · **Tabulator** (grid
interativo no cliente — ordena/filtra/pagina na hora) + **Leaflet** (mapa) +
modal de detalhe com galeria de fotos; tudo vendorizado, sem build ·
SQLite (via SQLModel) · `httpx` + BeautifulSoup (coleta) · `geopy`/Nominatim (geocoding) ·
Docker + docker-compose, atrás de **Caddy** (TLS + domínio).

## Como funciona

```
coletar → normalizar → deduplicar → geocodar → tempo até a UFSCar → score → exportar/servir
```

- **Coletores plugáveis** (`src/housing_radar/collectors/`):
  - `manual` — CSV (base confiável, ToS-safe);
  - `cardinali` — imobiliária local, HTML, paginação `?pag=N` (acervo grande);
  - `roca` / `iplano` / `top` / `e2` / `mariaaires` / `center` — imobiliárias na
    plataforma **MSYS Imob** (um coletor genérico, `msys.py`, cobre todas e já traz
    lat/lon, dispensando geocoding);
  - `vivareal` / `zap` — **Grupo ZAP** (um coletor genérico, `grupozap.py`, lê os
    ~30 anúncios por página do `application/ld+json` público; best-effort, ToS);
  - `imovelweb` — **Playwright** (browser real p/ passar o Cloudflare; `imovelweb.py`).
    Opcional/pesado (extra `browser` + Chromium), fora do `collect all` e do Docker;
  - `olx` — best-effort (a OLX ignora o filtro de região na URL; rende pouco).
  - `all` — roda todas as fontes remotas de uma vez.
- **Tempo até a UFSCar**: estimativa por distância + velocidade média (a pé/bici/carro)
  sem nenhuma chave; fica preciso por modal se você setar `HR_ORS_API_KEY`
  (OpenRouteService). Ônibus exige GTFS/Google e está no roadmap.
- **Score 0–100** transparente e configurável (`pipeline/score.py`): proximidade, preço,
  área, condomínio, quartos, vagas e um bônus leve de custo-benefício (relação
  preço/aluguel, quando o anúncio também loca), com pesos e âncoras calibráveis.

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

# 2c) (opcional) Imovelweb via browser real (passa o Cloudflare) — extra "browser"
uv sync --extra browser && uv run playwright install chromium
uv run housing-radar collect imovelweb -p 5 && uv run housing-radar enrich

# 3) (opcional) Refinar os top candidatos com tempos REAIS por modal (OpenRouteService)
uv sync --extra ors                          # instala o cliente ORS
# defina HR_ORS_API_KEY no .env (chave grátis em https://openrouteservice.org)
uv run housing-radar refine-ors --limit 40   # só os top-40 por score (cota grátis é pequena)

# 4) Exportar planilha para mandar pra avaliação
uv run housing-radar export --fmt xlsx     # -> exports/apartamentos_ufscar.xlsx

# 5) Subir a API + dashboard
uv run housing-radar serve --reload        # http://localhost:8000
```

Copie `.env.example` para `.env` para ajustar coordenadas da UFSCar, chave do ORS, etc.

### Comandos da CLI

| Comando | O que faz |
|---|---|
| `init-db` | Cria as tabelas |
| `import-csv PATH` | Importa anúncios manuais de um CSV |
| `collect [all\|<fonte>]` | Coleta de uma/todas as fontes (cardinali, MSYS, vivareal, zap, olx) sem enriquecer |
| `enrich` | Geocoda, calcula tempo até a UFSCar (estimativa) e (re)calcula o score |
| `rescore [--backfill-rent]` | Recalcula só o score (preserva tempos do ORS); opcional: preenche aluguel de registros antigos |
| `refine-ors --limit N` | Refina os top-N por score com tempos reais do ORS (cota-aware) |
| `run [all\|...]` | Pipeline completo (coleta + enrich) |
| `export --fmt xlsx\|csv` | Exporta o ranking para planilha |
| `stats --top N` | Resumo + melhores anúncios |
| `serve` | Sobe a API/dashboard (uvicorn) |

## Deploy (Docker + Caddy → ap.v1cferr.dev)

O container escuta em `127.0.0.1:3005` e fica **atrás do Caddy** (systemd), que cuida
de TLS (cert wildcard `*.v1cferr.dev`) e do domínio.

```bash
# no servidor:
git pull
cp .env.example .env            # ajuste HR_ORS_API_KEY etc. se quiser
docker compose up -d --build    # app em 127.0.0.1:3005

# popular a base no servidor:
docker compose exec app housing-radar collect all
docker compose exec app housing-radar collect roca --full --cap 500
docker compose exec app housing-radar enrich
docker compose exec app housing-radar refine-ors --limit 40   # tempos reais (ORS)
```

Caddy: adicione o bloco de `Caddyfile.example` dentro do seu `*.v1cferr.dev { ... }`,
depois `caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy`.

## Roadmap

- [x] Coletores de imobiliárias locais de São Carlos (Cardinali + plataforma MSYS)
- [x] **Acervo completo MSYS** via sitemap (`collect <fonte> --full`) — educado (cap + delay) e incremental (pula ids já no banco)
- [x] **VivaReal + ZAP** (Grupo ZAP) via JSON-LD público (`collect vivareal|zap`)
- [x] **Imovelweb** via Playwright (browser real passa o Cloudflare) — extra `browser`, opcional
- [x] **+3 imobiliárias MSYS** (e2, mariaaires, center) — 9 fontes, ~3.100 anúncios
- [x] **Modal de detalhe** com galeria de fotos (em alta resolução) e composição do score
- [x] **Mapa interativo** (Leaflet + OpenStreetMap) dos imóveis, coloridos por score, com a UFSCar marcada
- [x] **Favoritos compartilhados** (estrela + filtro), estado no servidor (eu + mãe)
- [x] **Simulação de financiamento** (Tabela Price) com filtro por parcela máxima; juros ancorados em fontes oficiais do **Banco Central** (Selic + taxa média do financiamento imobiliário PF, via SGS/BCB), com botão "usar"
- [x] **Origem por CEP** (ViaCEP): basta o CEP, sem número — resolve o endereço e geocoda (com fallback p/ bairro+cidade)
- [x] **Legível por IAs** (boas práticas p/ colar o link no ChatGPT/Gemini/Claude): o site é um SPA, então embuti o conteúdo no HTML cru — `<noscript>` com os top imóveis + **JSON-LD** (schema.org ItemList) no `/`, página **`/lista`** server-rendered (sem JS, com fotos/descrições/preços e filtros por querystring), **`/llms.txt`** (convenção llmstxt.org) documentando os endpoints, e `robots.txt`/`sitemap.xml`
- [x] **Estimativa de mudança** (distância origem→imóvel + ordem de grandeza; links p/ orçamento real). Origem em São Carlos; o campo fica vazio por padrão, com estado compartilhado/persistente (preenche temporário, limpa depois — o domínio é público)
- [x] **Overlay de carregamento** (spinner enquanto a lista é buscada, para não parecer um site vazio)
- [ ] **Cardinali:** abrir páginas de detalhe p/ preencher área/bairro faltantes (~30% dos cards)
- [ ] **Dedup entre fontes**: o mesmo imóvel aparece em VivaReal/ZAP/imovelweb (Navent) e nas imobiliárias com ids distintos — unir por endereço/atributos
- [ ] QuintoAndar — captcha/anti-bot bloqueia até via Playwright (precisaria stealth/proxy)
- [ ] Tempo de ônibus (GTFS São Carlos ou Google Distance Matrix)
- [ ] Calibrar pesos do score com avaliações reais (com a mãe, corretora CRECI-SP, que ajuda a distância — eu visito em São Carlos, ela analisa preço/documentação/financiamento remoto)
- [ ] Migrar SQLite → Postgres quando o volume crescer
- [ ] Alertas de novos anúncios acima de um score
