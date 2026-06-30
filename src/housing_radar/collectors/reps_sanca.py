"""Coletor das repúblicas de São Carlos (planilha "Reps Sanca" do Google Drive).

Entrada manual (V1C-68): a planilha é exportada como tabelas markdown (uma por
aba) e parseada aqui. Não há endereço — só referências de localização ("5 min da
USP") —, então estes anúncios entram sem geocoding (dist/score limitados; a aba
República ranqueia por preço). Cada república vira um Listing com
`transacao=aluguel`, `tipo_imovel=quarto_republica`.

O arquivo-fonte (`data/reps_sanca.md`) contém telefones de terceiros e fica fora
do git (ver .gitignore). Atualize-o reexportando a planilha e rode `import-reps`.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from housing_radar.collectors.base import Collector
from housing_radar.models import RawListing

# Colunas, por posição, em todas as três tabelas (a 1ª tem 1-2 colunas extras de notas).
_NOME, _VAGAS, _MORADORES, _PRECO, _REF, _CONTATO, _INSTA, _EXTRA, _NOTAS = range(9)

# Cabeçalhos/títulos de seção que NÃO são repúblicas.
_HEADER_FIRST_CELL = {"nome", "area 51"}
_SEP_RE = re.compile(r"^:?-+:?$")  # linha separadora de tabela markdown (:-:, ---, :--)
_URL_RE = re.compile(r"https?://\S+")


def _unescape(text: str) -> str:
    """Remove o escape de markdown (\\_ \\~ \\| \\*) que o export adiciona."""
    return re.sub(r"\\([_~|*])", r"\1", text).strip()


def _slug(text: str) -> str:
    """Slug ASCII estável (sem acento) para o source_id."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")


def _ocupacao(value: str) -> str:
    """Limpa o campo de vagas/moradores ('9 atualmente' -> '9', '?' -> '')."""
    cleaned = re.sub(r"(?i)\batualmente\b", "", value).strip()
    return "" if cleaned in ("?", "") else cleaned


def parse_rep_price(raw: str | None) -> float | None:
    """Preço médio mensal por morador. Lida com 'R$ 450,00', '~R$ 680', '600-650',
    'R$700-750', '$450'. Faixa -> ponto médio. Sem número -> None.
    """
    if not raw:
        return None
    tokens = re.findall(r"\d+(?:[.,]\d{1,2})?", raw)
    nums = []
    for tok in tokens:
        try:
            nums.append(float(tok.replace(".", "").replace(",", ".")))
        except ValueError:
            continue
    nums = [n for n in nums if n > 0]
    if not nums:
        return None
    return round((min(nums) + max(nums)) / 2, 2)


def _instagram_url(insta: str) -> str | None:
    handle = insta.strip()
    if not handle:
        return None
    if _URL_RE.match(handle):
        return handle
    handle = handle.lstrip("@").strip()
    return f"https://instagram.com/{handle}" if handle else None


def _split_row(line: str) -> list[str]:
    """`| a | b |` -> ['a', 'b'] (já com unescape e strip)."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return [_unescape(c) for c in cells]


class RepsSancaCollector(Collector):
    name = "reps_sanca"

    def __init__(self, path: str | Path = "data/reps_sanca.md") -> None:
        self.path = Path(path)

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        if not self.path.exists():
            raise FileNotFoundError(f"Planilha das repúblicas não encontrada: {self.path}")

        out: list[RawListing] = []
        seen_ids: set[str] = set()
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = _split_row(line)
            if not cells or all(c == "" for c in cells):
                continue
            if all(_SEP_RE.match(c) for c in cells if c):  # linha separadora ---
                continue
            nome = cells[_NOME]
            if not nome or nome.lower() in _HEADER_FIRST_CELL:
                continue

            raw = self._build(cells, nome, seen_ids)
            out.append(raw)
        return out

    def _build(self, cells: list[str], nome: str, seen_ids: set[str]) -> RawListing:
        def cell(i: int) -> str:
            return cells[i] if i < len(cells) else ""

        # source_id estável e único (Lótus/LÓTUS colidem -> sufixo).
        base = _slug(nome) or "rep"
        sid = base
        n = 2
        while sid in seen_ids:
            sid = f"{base}-{n}"
            n += 1
        seen_ids.add(sid)

        ref, contato = cell(_REF), cell(_CONTATO)
        extra, notas = cell(_EXTRA), cell(_NOTAS)
        vagas, moradores = _ocupacao(cell(_VAGAS)), _ocupacao(cell(_MORADORES))

        desc_parts = []
        if ref:
            desc_parts.append(f"Referência: {ref}")
        ocup = " · ".join(p for p in (
            f"{vagas} vaga(s)" if vagas and vagas != "?" else "",
            f"{moradores} morador(es)" if moradores else "",
        ) if p)
        if ocup:
            desc_parts.append(ocup)
        if contato:
            desc_parts.append(f"Contato: {contato}")
        for x in (extra, notas):
            if x and not _URL_RE.match(x):
                desc_parts.append(x)

        url = _instagram_url(cell(_INSTA))
        fotos = next(
            (m.group(0) for x in (extra, notas) if (m := _URL_RE.search(x))), None
        )
        if fotos:
            desc_parts.append(f"Fotos: {fotos}")
        if not url:
            url = fotos

        title = nome if re.match(r"(?i)^(rep\b|rep[uú]blica)", nome) else f"República {nome}"
        return RawListing(
            source=self.name,
            source_id=sid,
            url=url,
            title=title,
            rent_price=parse_rep_price(cell(_PRECO)),
            transacao="aluguel",
            tipo_imovel="quarto_republica",
            bedrooms=1,  # quarto em república
            description=" | ".join(desc_parts) or None,
            raw={
                "nome": nome, "vagas": vagas, "moradores": moradores,
                "preco": cell(_PRECO), "referencia": ref, "contato": contato,
                "insta": cell(_INSTA), "extra": extra, "notas": notas,
            },
        )
