"""Interface de linha de comando do housing-radar."""

from __future__ import annotations

import logging

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from housing_radar.collectors import REMOTE_COLLECTORS, ManualCSVCollector
from housing_radar.config import get_settings
from housing_radar.db import init_db, session_scope
from housing_radar.models import Listing
from housing_radar.pipeline.run import enrich as run_enrich
from housing_radar.pipeline.run import ingest

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


@app.command()
def collect(
    source: str = typer.Argument("all", help=f"Fonte: {_SOURCES_HELP}"),
    max_pages: int | None = typer.Option(None, "--max-pages", "-p"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Coleta de uma ou todas as fontes remotas e faz upsert no banco (sem enriquecer)."""
    _setup_logging(verbose)
    init_db()
    for name in _resolve_sources(source):
        stats = ingest(REMOTE_COLLECTORS[name](), max_pages=max_pages)
        console.print(f"[green]{name} coletado:[/green] {stats}")


@app.command()
def enrich(
    limit: int | None = typer.Option(None, "--limit", "-n"),
    regeocode: bool = typer.Option(False, "--regeocode"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Geocoda, calcula tempo até a UFSCar e (re)calcula o score."""
    _setup_logging(verbose)
    init_db()
    stats = run_enrich(limit=limit, regeocode=regeocode)
    console.print(f"[green]Enriquecimento concluído:[/green] {stats}")


@app.command()
def run(
    source: str = typer.Argument("all", help=f"Fonte: {_SOURCES_HELP}"),
    max_pages: int | None = typer.Option(None, "--max-pages", "-p"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pipeline completo: coleta de uma/todas as fontes + enriquecimento."""
    _setup_logging(verbose)
    init_db()
    for name in _resolve_sources(source):
        stats = ingest(REMOTE_COLLECTORS[name](), max_pages=max_pages)
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
