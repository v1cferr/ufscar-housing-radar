# Coleta automática (cron no host)

Como manter o banco de produção atualizado com venda + locação, rodando a coleta
no **host** (não no container) via cron.

## Por que no host, e não no container

VivaReal e ZAP ficam atrás do Cloudflare. Da imagem Docker (Python slim), as
requisições levam **HTTP 403** — o *fingerprint TLS* da imagem é barrado, mesmo
saindo do **mesmo IP** do host. Do host, a mesma requisição passa (HTTP 200).

Verificado em 30/06/2026: `docker compose exec app ... collect vivareal_aluguel`
→ `403`; `uv run housing-radar collect vivareal_aluguel` no host → `200`, ~130
anúncios. Por isso a coleta dos portais roda no host.

> O `docker compose exec app housing-radar collect ...` do README serve para as
> fontes que **não** passam pelo Cloudflare (imobiliárias locais). Para
> VivaReal/ZAP, use o host.

## Concorrência: WAL (sem downtime)

A coleta no host **escreve** no mesmo arquivo SQLite que o container **lê**
(`./data/housing_radar.db`, bind-mountado). Para não dar `database is locked`, o
banco roda em **WAL** com `busy_timeout=10s` — configurado no `db.py`
(`_enable_wal`, aplicado em toda conexão SQLite). Com WAL, leitura e escrita
coexistem; **não é preciso parar o container** durante a coleta.

(Se algum dia o banco voltar a `journal_mode=delete`, a alternativa é
`docker compose stop app` antes da coleta e `start` depois — com downtime.)

## Script

[`scripts/collect_host.sh`](../scripts/collect_host.sh) coleta as fontes-padrão
(venda + locação) e roda o `enrich` (geocode + score):

```bash
scripts/collect_host.sh             # vivareal/zap/cardinali (venda) + *_aluguel (locação)
scripts/collect_host.sh roca top    # só as fontes passadas
```

Aponta para `./data/housing_radar.db` por padrão (sobrescreva com
`HR_DATABASE_URL`).

## Cron

Editar com `crontab -e` e adicionar (ajuste o caminho absoluto do projeto):

```cron
# Atualiza moradia (venda + locação) todo dia às 04:30; log em data/collect.log
30 4 * * * cd /home/v1cferr/Projects/GitHub/v1cferr/ufscar-housing-radar && scripts/collect_host.sh >> data/collect.log 2>&1
```

Notas:
- `uv` precisa estar no `PATH` do cron (cron usa um ambiente mínimo). Se faltar,
  use o caminho absoluto do `uv` ou um `PATH=...` no topo do crontab.
- `data/collect.log` está no `.gitignore` (junto com `data/*.db`).
- O acervo completo das imobiliárias MSYS usa `collect <fonte> --full` (educado,
  incremental) — rode esporadicamente à parte, não no cron diário.

## Verificar depois

```bash
curl -s "https://ap.v1cferr.dev/api/listings?limit=5000&aba=aluguel" \
  | python3 -c "import sys,json; print(len(json.load(sys.stdin)), 'aluguéis')"
```
