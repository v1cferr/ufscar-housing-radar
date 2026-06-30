#!/usr/bin/env bash
# Coleta no HOST (não no container) — venda + locação — e enriquece.
#
# Por que no host: VivaReal/ZAP barram o fingerprint TLS da imagem Docker slim
# (HTTP 403), mas o host passa. Ver docs/coleta-cron.md. Escreve no MESMO banco
# que o container serve; com WAL (db.py) leitura e escrita coexistem sem downtime.
#
# Uso:  scripts/collect_host.sh            # fontes-padrão (venda + locação)
#       scripts/collect_host.sh roca top   # só as fontes passadas
#
# Cron (exemplo, 04:30 todo dia) — ver docs/coleta-cron.md:
#   30 4 * * * cd /home/v1cferr/Projects/GitHub/v1cferr/ufscar-housing-radar && \
#     scripts/collect_host.sh >> data/collect.log 2>&1
set -euo pipefail

cd "$(dirname "$0")/.."

# Banco de produção (o mesmo bind-mountado em ./data e servido pelo container).
export HR_DATABASE_URL="${HR_DATABASE_URL:-sqlite:///$(pwd)/data/housing_radar.db}"

# Fontes padrão: venda (grupozap + locais) + locação (apto, kitnet, chavesnamao).
DEFAULT_SOURCES=(
  vivareal zap cardinali
  vivareal_aluguel zap_aluguel cardinali_aluguel
  chavesnamao_aluguel chavesnamao_casa_aluguel
  vivareal_kitnet chavesnamao_kitnet
)
SOURCES=("${@:-${DEFAULT_SOURCES[@]}}")
[ "$#" -gt 0 ] && SOURCES=("$@")

echo "=== $(date '+%F %T') coleta no host -> $HR_DATABASE_URL ==="
for src in "${SOURCES[@]}"; do
  echo "--- collect $src ---"
  uv run housing-radar collect "$src" || echo "  (falhou $src; segue)"
done

echo "--- enrich (geocode + score) ---"
uv run housing-radar enrich

echo "=== $(date '+%F %T') concluído ==="
