# Fontes de dados (plataformas) — São Carlos

Levantamento das plataformas de imóveis com cobertura em São Carlos, o que cada
uma rende e o estado do coletor. Última revisão: **30/06/2026**.

## Em uso

| Fonte | Coletor | Transação | Tipo | Observação |
|---|---|---|---|---|
| VivaReal | `vivareal`, `vivareal_aluguel` | venda, aluguel | apto | JSON-LD `Apartment`. **Só do host** (Cloudflare 403 no container). |
| ZAP | `zap`, `zap_aluguel` | venda, aluguel | apto | Mesmo grupo do VivaReal (inventário se sobrepõe → "Agrupar" colapsa). Só do host. |
| Cardinali | `cardinali`, `cardinali_aluguel` | venda, aluguel | apto | HTML server-rendered (ISO-8859-1). Local, scoped em SC. |
| MSYS (roca, iplano, top, e2, mariaaires, center) | `roca`, … | venda | apto | Next.js + sitemap. 6 imobiliárias locais. **Locação ainda não** (ver abaixo). |
| ImovelWeb | `imovelweb` | venda | apto | Playwright (browser real passa o Cloudflare). Opcional/pesado, fora do Docker. |
| Manual (CSV) | `import-csv` | — | — | Semente manual. |
| Repúblicas (planilha) | `import-reps` | aluguel | quarto_republica | Planilha "Reps Sanca" do Drive; só masculinas/mistas. |

## Avaliadas e descartadas (ou pendentes)

| Plataforma | Status SC | Decisão |
|---|---|---|
| **OLX** | Ignora o filtro de região na URL → devolve SP inteiro; **0 imóveis de São Carlos** após filtrar por cidade (venda e locação). | **Descartada** enquanto não houver filtro de localização confiável. Vale para apto **e** kitnet. |
| **QuintoAndar** | Sem inventário em São Carlos (página responde, mas total 0). | Descartada. |
| **ImovelWeb (locação)** | HTTP 403 a httpx (Cloudflare); a venda já usa Playwright. | Pendente: adicionar locação via o coletor de browser. |
| **Chaves na Mão** | ~5.000+ aluguéis em SC; URL já é scoped em `sp-sao-carlos`. Mas o ld+json é só resumo da página — os imóveis exigem parser HTML próprio. | **Pendente** (coletor novo; bom potencial). |

## Próximo passo de maior valor: MSYS locação (com kitnet)

As 6 imobiliárias MSYS têm muita locação no sitemap (ex.: `roca` sozinho: ~2.482
`locacao` + ~1.239 `venda-e-locacao`), e o parser de detalhe **já lê o aluguel**
(`valLocation`). O sitemap inclui `apartamentos`, `casas` e **`kitnets`** por URL
(`/imovel/locacao/<tipo>/sao-carlos/<bairro>/<id>`) — é o caminho que finalmente
preenche a aba **Kitnet**.

Falta: um regex de detalhe para `locacao` (hoje `_SC_APT_DETAIL` exclui de
propósito), mapear `<tipo>` da URL → `tipo_imovel`, e marcar `transacao=aluguel`.

## Operação

Todas as fontes de portal (Cloudflare) rodam **no host** via cron —
ver [coleta-cron.md](coleta-cron.md).
