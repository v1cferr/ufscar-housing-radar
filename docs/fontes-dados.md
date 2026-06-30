# Fontes de dados (plataformas) — São Carlos

Levantamento das plataformas de imóveis com cobertura em São Carlos, o que cada
uma rende e o estado do coletor. Última revisão: **30/06/2026**.

## Em uso

| Fonte | Coletor | Transação | Tipo | Observação |
|---|---|---|---|---|
| VivaReal | `vivareal`, `vivareal_aluguel` | venda, aluguel | apto | JSON-LD `Apartment`. **Só do host** (Cloudflare 403 no container). |
| ZAP | `zap`, `zap_aluguel` | venda, aluguel | apto | Mesmo grupo do VivaReal (inventário se sobrepõe → "Agrupar" colapsa). Só do host. |
| Cardinali | `cardinali`, `cardinali_aluguel` | venda, aluguel | apto | HTML server-rendered (ISO-8859-1). Local, scoped em SC. |
| VivaReal (kitnet) | `vivareal_kitnet` | aluguel | kitnet | Página de kitnet usa JSON-LD `@type "Product"`. Preenche a aba Kitnet. |
| Chaves na Mão | `chavesnamao_aluguel` | aluguel | apto | Parse pela **URL SEO** do anúncio (tipo/quartos/bairro/área/preço/id); pagina por `?pg=N`. |
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
| **OLX (kitnet)** | A OLX tem filtro de kitnet, mas ignora a região → 0 de São Carlos. | Descartada (mesmo motivo da OLX em geral). |

## MSYS locação — avaliado e descartado

Apesar do volume no sitemap (~2.482 `locacao` em `roca`), **não vale**:
não há categoria **kitnet** em São Carlos (só apto/casa/comercial/terreno) e o
aluguel vem **oculto** no detalhe (`flgHideValLocationSite=1`; 0 de 20 da amostra
mostravam preço). Sem preço, não dá para comparar/score — não justifica o coletor.
A aba Kitnet foi preenchida pelo `vivareal_kitnet`.

## Pendências (próximos, se quiser mais volume)

- **Chaves na Mão**: estender para `casas`/`kitnets` (mesmo parser de URL, outro
  `search_path`).
- **ImovelWeb locação**: via o coletor de browser (Playwright) já usado na venda.

## Operação

Todas as fontes de portal (Cloudflare) rodam **no host** via cron —
ver [coleta-cron.md](coleta-cron.md).
