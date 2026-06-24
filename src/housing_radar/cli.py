"""Interface de linha de comando do housing-radar."""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from housing_radar.collectors import REMOTE_COLLECTORS, ManualCSVCollector
from housing_radar.collectors.msys import MSYS_SITES, MSYSCollector
from housing_radar.config import get_settings
from housing_radar.db import init_db, session_scope
from housing_radar.models import Listing
from housing_radar.pipeline.run import enrich as run_enrich
from housing_radar.pipeline.run import (
    backfill_rent_from_raw,
    ingest,
    refine_routes_ors,
    rescore_all,
)

app = typer.Typer(
    help="Pipeline para coletar, ranquear e servir apartamentos próximos à UFSCar.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command("init-db")
def init_db_cmd() -> None:
    """Cria as tabelas do banco."""
    init_db()
    console.print("[green]Banco inicializado.[/green]")


@app.command("import-csv")
def import_csv(
    path: str = typer.Argument(..., help="Caminho do CSV com anúncios manuais."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Importa anúncios de um CSV (coletor manual) e faz upsert no banco."""
    _setup_logging(verbose)
    init_db()
    stats = ingest(ManualCSVCollector(path))
    console.print(f"[green]CSV importado:[/green] {stats}")


_SOURCES_HELP = "all, " + ", ".join(REMOTE_COLLECTORS)


def _resolve_sources(source: str) -> list[str]:
    if source == "all":
        return list(REMOTE_COLLECTORS)
    if source not in REMOTE_COLLECTORS:
        raise typer.BadParameter(f"Fonte desconhecida: {source}. Use: {_SOURCES_HELP}")
    return [source]


def _existing_source_ids(source: str) -> set[str]:
    with session_scope() as session:
        rows = session.exec(
            select(Listing.source_id).where(
                Listing.source == source, Listing.source_id.is_not(None)
            )
        ).all()
    return {r for r in rows if r}


def _build_collector(name: str, *, full: bool, cap: int, delay: float):
    """Constrói o coletor de uma fonte; em --full, roteia MSYS p/ varredura do sitemap."""
    if full and name in MSYS_SITES:
        return MSYSCollector(
            MSYS_SITES[name],
            name=name,
            full=True,
            cap=cap,
            delay=delay,
            skip_ids=_existing_source_ids(name),  # incremental: não re-busca o que já há
        )
    if full and name not in MSYS_SITES:
        console.print(f"[yellow]--full ignorado para '{name}' (só vale para fontes MSYS).[/yellow]")
    return REMOTE_COLLECTORS[name]()


@app.command()
def collect(
    source: str = typer.Argument("all", help=f"Fonte: {_SOURCES_HELP}"),
    max_pages: int | None = typer.Option(None, "--max-pages", "-p"),
    full: bool = typer.Option(False, "--full", help="MSYS: varre o sitemap (acervo completo)"),
    cap: int = typer.Option(300, "--cap", help="--full: máx. de imóveis por execução"),
    delay: float = typer.Option(0.4, "--delay", help="--full: pausa (s) entre requisições"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Coleta de uma ou todas as fontes remotas e faz upsert no banco (sem enriquecer)."""
    _setup_logging(verbose)
    init_db()
    for name in _resolve_sources(source):
        collector = _build_collector(name, full=full, cap=cap, delay=delay)
        stats = ingest(collector, max_pages=max_pages)
        console.print(f"[green]{name} coletado:[/green] {stats}")


@app.command()
def enrich(
    limit: int | None = typer.Option(None, "--limit", "-n"),
    regeocode: bool = typer.Option(False, "--regeocode"),
    recompute_ors: bool = typer.Option(
        False, "--recompute-ors", help="Recalcula por estimativa MESMO quem já tem ORS (perde o refino)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Geocoda, calcula tempo até a UFSCar e (re)calcula o score (preserva ORS por padrão)."""
    _setup_logging(verbose)
    init_db()
    stats = run_enrich(limit=limit, regeocode=regeocode, recompute_ors=recompute_ors)
    console.print(f"[green]Enriquecimento concluído:[/green] {stats}")


@app.command("refine-ors")
def refine_ors_cmd(
    limit: int = typer.Option(40, "--limit", "-n", help="quantos top-por-score refinar"),
    delay: float = typer.Option(5.0, "--delay", help="pausa (s) entre imóveis (respeita ~40/min)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Refina o deslocamento dos top-N por score com OpenRouteService (tempos reais por modal)."""
    _setup_logging(verbose)
    init_db()
    stats = refine_routes_ors(limit=limit, delay=delay)
    console.print(f"[green]Refino ORS:[/green] {stats}")


@app.command()
def rescore(
    backfill_rent: bool = typer.Option(
        False, "--backfill-rent", help="Antes, preenche aluguel (valLocation) de registros antigos"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Recalcula só o score (sem mexer em geocode/tempo — preserva o refino ORS)."""
    _setup_logging(verbose)
    init_db()
    if backfill_rent:
        console.print(f"[green]Aluguel preenchido:[/green] {backfill_rent_from_raw()}")
    console.print(f"[green]Score recalculado:[/green] {rescore_all()}")


@app.command()
def run(
    source: str = typer.Argument("all", help=f"Fonte: {_SOURCES_HELP}"),
    max_pages: int | None = typer.Option(None, "--max-pages", "-p"),
    full: bool = typer.Option(False, "--full", help="MSYS: varre o sitemap (acervo completo)"),
    cap: int = typer.Option(300, "--cap", help="--full: máx. de imóveis por execução"),
    delay: float = typer.Option(0.4, "--delay", help="--full: pausa (s) entre requisições"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pipeline completo: coleta de uma/todas as fontes + enriquecimento."""
    _setup_logging(verbose)
    init_db()
    for name in _resolve_sources(source):
        collector = _build_collector(name, full=full, cap=cap, delay=delay)
        stats = ingest(collector, max_pages=max_pages)
        console.print(f"[green]{name} coletado:[/green] {stats}")
    enrich_stats = run_enrich()
    console.print(f"[green]Enriquecimento:[/green] {enrich_stats}")


@app.command("export")
def export_cmd(
    fmt: str = typer.Option("xlsx", "--fmt", help="xlsx | csv"),
    out: str | None = typer.Option(None, "--out", "-o"),
) -> None:
    """Exporta os anúncios ranqueados para planilha."""
    from housing_radar.export import export as do_export

    path = do_export(fmt=fmt, path=out)
    console.print(f"[green]Exportado para:[/green] {path}")


@app.command()
def stats(top: int = typer.Option(10, "--top", "-t")) -> None:
    """Mostra um resumo e os melhores anúncios por score."""
    init_db()
    with session_scope() as session:
        listings = session.exec(select(Listing).where(Listing.status == "active")).all()

    total = len(listings)
    scored = [x for x in listings if x.score is not None]
    geocoded = [x for x in listings if x.geocoded]
    console.print(
        f"Total: [bold]{total}[/bold] · com score: [bold]{len(scored)}[/bold] · "
        f"geocodados: [bold]{len(geocoded)}[/bold]"
    )

    table = Table(title=f"Top {top} por score")
    for col in ("Score", "Título", "Bairro", "Preço", "m²", "Q", "Carro(min)", "Fonte"):
        table.add_column(col)
    best = sorted(scored, key=lambda x: -(x.score or 0))[:top]
    for x in best:
        table.add_row(
            f"{x.score:.0f}",
            (x.title or "—")[:40],
            x.neighborhood or "—",
            f"{x.price:,.0f}".replace(",", ".") if x.price else "—",
            f"{x.area_m2:.0f}" if x.area_m2 else "—",
            str(x.bedrooms) if x.bedrooms is not None else "—",
            f"{x.time_car_min:.0f}" if x.time_car_min else "—",
            x.source,
        )
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Sobe a API/dashboard FastAPI (uvicorn)."""
    import uvicorn

    get_settings()  # valida config cedo
    uvicorn.run("housing_radar.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
