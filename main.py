from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from sys import exit
from time import sleep

from click import argument, group, IntRange, option, Path as ClickPath
from serial.tools import list_ports
from rich.console import Console
from rich.live import Live
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Prompt
from rich.table import Table

from profibus_debug.bus import discover_devices
from profibus_debug.diagnostics import SlaveDiagnostics, read_diagnostics
from profibus_debug.exchange import exchange_data
from profibus_debug.gsd import GsdDevice, GsdModule, parse_gsd, _decode_cfg_sizes
from profibus_debug.session import load_last_hwid, save_last_hwid

console = Console()


def _parse_hex(value: str) -> bytes:
    cleaned = value.replace(" ", "").replace(":", "").replace("-", "")
    if len(cleaned) % 2:
        raise ValueError(f"odd number of hex digits: {value!r}")
    return bytes(int(cleaned[i:i + 2], 16) for i in range(0, len(cleaned), 2))


def resolve_port(port: str | None) -> str:
    if port:
        return port

    ports = list(list_ports.comports())
    if not ports:
        console.print("[yellow]No serial ports detected.[/yellow]")
        console.print("[dim]Connect a device and try again, or pass --port explicitly.[/dim]")
        exit(1)

    last_hwid = load_last_hwid()
    default_idx: int | None = None
    if last_hwid:
        for i, p in enumerate(ports):
            if p.hwid and p.hwid == last_hwid:
                default_idx = i
                break

    table = Table(title="Available Serial Ports", show_lines=True)
    table.add_column("#", style="bold cyan", justify="right")
    table.add_column("Port", style="bold")
    table.add_column("Description")
    table.add_column("Hardware ID", style="dim")
    for i, p in enumerate(ports):
        marker = " [green](last used)[/green]" if i == default_idx else ""
        table.add_row(str(i), p.device + marker, p.description or "", p.hwid or "")
    console.print(table)

    hint = f"0–{len(ports) - 1}, or x to exit"
    default_str = str(default_idx) if default_idx is not None else None
    while True:
        answer = Prompt.ask(f"[bold cyan]Select port[/bold cyan] [dim]{hint}[/dim]", default=default_str).strip()
        if answer.lower() == "x":
            exit(0)
        if answer.isdigit() and 0 <= int(answer) < len(ports):
            selected = ports[int(answer)]
            if selected.hwid:
                save_last_hwid(selected.hwid)
            return selected.device
        console.print(f"[red]Invalid selection.[/red] Enter a number 0–{len(ports) - 1} or x.")


@group()
def cli() -> None:
    """PROFIBUS DP debug tool."""


@cli.command()
@option("--port", "-p", default=None, help="Serial port (e.g. COM3 or /dev/ttyUSB0).")
@option("--baudrate", "-b", default=9600, show_default=True, help="Bus baud rate.")
@option("--master-addr", default=0, show_default=True, help="Master station address.")
@option("--timeout", default=0.05, show_default=True, help="Per-address probe timeout (s).")
@option("--autoreconnect", "-r", is_flag=True, default=False, help="Retry on serial disconnect.")
@option("--debug", is_flag=True, default=False, help="Enable PHY debug output.")
def discover(
    port: str | None,
    baudrate: int,
    master_addr: int,
    timeout: float,
    autoreconnect: bool,
    debug: bool,
) -> None:
    """Discover all active slaves on the PROFIBUS DP network."""
    resolved_port = resolve_port(port)

    while True:
        try:
            _run_discover(resolved_port, baudrate, master_addr, timeout, debug)
            break
        except Exception as exc:
            if autoreconnect and _is_serial_error(exc):
                console.print(f"[yellow]Serial error: {exc}[/yellow]")
                console.print("[yellow]Waiting for device to reconnect...[/yellow]")
                _wait_for_port(resolved_port)
                console.print("[green]Port available again, retrying...[/green]")
            else:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                exit(1)


@cli.command()
@argument("address", type=IntRange(0, 125))
@option("--port", "-p", default=None, help="Serial port (e.g. COM3 or /dev/ttyUSB0).")
@option("--baudrate", "-b", default=9600, show_default=True, help="Bus baud rate.")
@option("--master-addr", default=0, show_default=True, help="Master station address.")
@option("--timeout", default=0.5, show_default=True, help="Response timeout per attempt (s).")
@option("--retries", default=3, show_default=True, help="Number of SlaveDiag retries.")
@option("--warmup-probes", default=10, show_default=True, help="FdlStat probes sent before SlaveDiag to trigger baud-rate lock on devices like Siemens CBP2.")
@option("--warmup-interval", default=0.1, show_default=True, help="Interval between warm-up probes (s).")
@option("--debug", is_flag=True, default=False, help="Enable PHY debug output.")
def diagnose(
    address: int,
    port: str | None,
    baudrate: int,
    master_addr: int,
    timeout: float,
    retries: int,
    warmup_probes: int,
    warmup_interval: float,
    debug: bool,
) -> None:
    """Read DP slave diagnostics from a single slave ADDRESS (0–125)."""
    resolved_port = resolve_port(port)
    with console.status(f"[cyan]Reading diagnostics from slave {address}...[/cyan]"):
        diag = read_diagnostics(
            port=resolved_port,
            addr=address,
            baudrate=baudrate,
            master_addr=master_addr,
            timeout=timeout,
            retries=retries,
            warmup_probes=warmup_probes,
            warmup_interval=warmup_interval,
            debug=debug,
        )
    if diag is None:
        console.print(f"[bold red]No response from slave {address}.[/bold red]")
        exit(1)
    _print_diagnostics(diag)


@cli.command()
@argument("address", type=IntRange(0, 125))
@argument("gsd_file", type=ClickPath(exists=True, dir_okay=False))
@option("--port", "-p", default=None, help="Serial port (e.g. COM3 or /dev/ttyUSB0).")
@option("--baudrate", "-b", default=9600, show_default=True, help="Bus baud rate.")
@option("--master-addr", default=0, show_default=True, help="Master station address.")
@option("--module", "-m", default=None, help="Module name (partial match). Default: first module.")
@option("--count", "-n", default=1, show_default=True, help="Number of data exchange cycles (0 = continuous).")
@option("--interval", default=0.2, show_default=True, help="Interval between cycles (s).")
@option("--timeout", default=0.5, show_default=True, help="Response timeout (s).")
@option("--warmup-probes", default=5, show_default=True, help="FdlStat warm-up probes before startup.")
@option("--cfg-bytes", default=None, help="Override ChkCfg bytes (hex, e.g. '10 20 30').")
@option("--prm-bytes", default=None, help="Override SetPrm user parameter bytes (hex).")
@option("--debug", is_flag=True, default=False, help="Enable PHY debug output.")
def exchange(
    address: int,
    gsd_file: str,
    port: str | None,
    baudrate: int,
    master_addr: int,
    module: str | None,
    count: int,
    interval: float,
    timeout: float,
    warmup_probes: int,
    cfg_bytes: str | None,
    prm_bytes: str | None,
    debug: bool,
) -> None:
    """Run DP data exchange with slave ADDRESS using GSD_FILE device description.

    Performs the full DP startup sequence (SetPrm + ChkCfg) then reads
    cyclic process data. ADDRESS is 0-125. GSD_FILE is the path to the
    device's GSD/GSE file.
    """
    resolved_port = resolve_port(port)

    try:
        device = parse_gsd(Path(gsd_file))
    except Exception as exc:
        console.print(f"[bold red]Failed to parse GSD:[/bold red] {exc}")
        exit(1)

    if not device.modules:
        console.print("[bold red]No modules defined in GSD file.[/bold red]")
        exit(1)

    selected: GsdModule | None = None
    if module:
        needle = module.lower()
        for m in device.modules:
            if needle in m.name.lower():
                selected = m
                break
        if selected is None:
            console.print(f"[bold red]Module '{module}' not found.[/bold red] Available:")
            for m in device.modules:
                console.print(f"  {m.name}  ({m.input_bytes}B in, {m.output_bytes}B out)")
            exit(1)
    else:
        selected = device.modules[0]

    if cfg_bytes is not None:
        try:
            parsed_cfg = _parse_hex(cfg_bytes)
        except ValueError as exc:
            console.print(f"[bold red]Invalid --cfg-bytes:[/bold red] {exc}")
            exit(1)
        in_b, out_b = _decode_cfg_sizes(parsed_cfg)
        selected = replace(selected, cfg_bytes=parsed_cfg, input_bytes=in_b, output_bytes=out_b)

    if prm_bytes is not None:
        try:
            parsed_prm = _parse_hex(prm_bytes)
        except ValueError as exc:
            console.print(f"[bold red]Invalid --prm-bytes:[/bold red] {exc}")
            exit(1)
        selected = replace(selected, user_prm_data=parsed_prm)

    console.print(f"[cyan]Device:[/cyan] {device.vendor_name} {device.model_name}  "
                  f"(ident [bold]{device.ident_number:#06x}[/bold])")
    console.print(f"[cyan]Module:[/cyan] {selected.name}  "
                  f"({selected.input_bytes}B in, {selected.output_bytes}B out)")

    live_panel: list = [None]  # mutable container so on_cycle can update it

    def on_cycle(i: int, data: bytes) -> None:
        live_panel[0] = _build_exchange_panel(address, selected, data)  # type: ignore[arg-type]

    try:
        with console.status(f"[cyan]Starting up slave {address}...[/cyan]"):
            # Run the first cycle before entering Live so the panel exists
            results = exchange_data(
                port=resolved_port,
                addr=address,
                device=device,
                module=selected,
                baudrate=baudrate,
                master_addr=master_addr,
                timeout=timeout,
                warmup_probes=warmup_probes,
                count=min(count, 1) if count != 0 else 1,
                interval=interval,
                debug=debug,
                on_cycle=on_cycle,
            )
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        exit(1)

    if not results:
        console.print(f"[yellow]No data received from slave {address}.[/yellow]")
        exit(1)

    if count == 1:
        console.print(live_panel[0])
        return

    # Continuous or multi-cycle: update in place
    remaining = count - 1 if count > 1 else 0

    try:
        with Live(live_panel[0], console=console, refresh_per_second=10) as live:
            def on_cycle_live(i: int, data: bytes) -> None:
                live_panel[0] = _build_exchange_panel(address, selected, data)  # type: ignore[arg-type]
                live.update(live_panel[0])

            exchange_data(
                port=resolved_port,
                addr=address,
                device=device,
                module=selected,
                baudrate=baudrate,
                master_addr=master_addr,
                timeout=timeout,
                warmup_probes=0,
                count=remaining,
                interval=interval,
                debug=debug,
                on_cycle=on_cycle_live,
            )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        exit(1)


def _build_exchange_panel(addr: int, module: GsdModule, data: bytes):
    from rich.panel import Panel

    hex_str = " ".join(f"{b:02X}" for b in data)
    rows: list[tuple[str, str]] = [("Raw bytes", hex_str)]

    if module.input_bytes == 2 and len(data) == 2:
        rows.append(("Position (16-bit)", str(int.from_bytes(data, "big"))))
    elif module.input_bytes == 4 and len(data) == 4:
        rows.append(("Position (32-bit)", str(int.from_bytes(data, "big"))))
    elif module.input_bytes == 6 and len(data) == 6:
        rows.append(("Position (32-bit)", str(int.from_bytes(data[:4], "big"))))
        rows.append(("Speed raw (16-bit)", str(int.from_bytes(data[4:6], "big"))))

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column(style="dim")
    for label, value in rows:
        grid.add_row(label, value)

    return Panel(grid, title=f"[bold]Slave {addr} - Data Exchange[/bold]", expand=False)


def _print_diagnostics(diag: SlaveDiagnostics) -> None:
    from rich.panel import Panel

    def flag(label: str, value: bool, warn: bool = True) -> str:
        if value:
            colour = "red" if warn else "yellow"
            return f"[{colour}][!] {label}[/{colour}]"
        return f"[green][+] {label}[/green]"

    ident_str = f"0x{diag.ident_number:04X}"
    master_str = str(diag.master_addr) if diag.master_addr != 255 else "none"

    info = Table.grid(padding=(0, 2))
    info.add_column(style="bold")
    info.add_column()
    info.add_row("Slave address", str(diag.addr))
    info.add_row("Ident number", ident_str)
    info.add_row("Owned by master", master_str)
    info.add_row("Ready for data exchange", "yes" if diag.ready_for_data_exchange else "[red]no[/red]")

    status = Table.grid(padding=(0, 2))
    status.add_column()
    status.add_column()
    status.add_row(flag("Non-existent",       diag.station_non_existent),
                   flag("Not ready",          diag.station_not_ready))
    status.add_row(flag("Config fault",       diag.cfg_fault),
                   flag("Param fault",        diag.prm_fault))
    status.add_row(flag("Param request",      diag.prm_req),
                   flag("Not supported",      diag.not_supported))
    status.add_row(flag("Master lock",        diag.master_lock,         warn=False),
                   flag("Invalid response",   diag.invalid_slave_response))
    status.add_row(flag("Watchdog",           diag.watchdog_on,         warn=False),
                   flag("Freeze mode",        diag.freeze_mode,         warn=False))
    status.add_row(flag("Sync mode",          diag.sync_mode,           warn=False),
                   flag("Deactivated",        diag.deactivated,         warn=False))
    if diag.ext_diag_overflow:
        status.add_row("[yellow]⚠ Ext diag overflow[/yellow]", "")

    console.print(Panel(info, title=f"[bold]Slave {diag.addr} - Diagnostics[/bold]", expand=False))
    console.print(Panel(status, title="Station Status", expand=False))

    if diag.ext_diag_data:
        hex_str = " ".join(f"{b:02X}" for b in diag.ext_diag_data)
        ext = Table.grid(padding=(0, 2))
        ext.add_column(style="bold")
        ext.add_column(style="dim")
        ext.add_row("Raw bytes", hex_str)
        console.print(Panel(ext, title="Extended Diagnostics", expand=False))
    else:
        console.print("[dim]No extended diagnostic data.[/dim]")


def _run_discover(port: str, baudrate: int, master_addr: int, timeout: float, debug: bool) -> None:
    found: list[int] = []
    total = 126

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[cyan]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    with progress:
        task = progress.add_task(f"Scanning {port}", total=total)

        def on_probe(addr: int) -> None:
            progress.update(task, completed=addr, description=f"Scanning {port}  addr {addr:3d}/{total - 1}")

        for addr in discover_devices(
            port=port,
            baudrate=baudrate,
            master_addr=master_addr,
            timeout_per_addr=timeout,
            debug=debug,
            on_probe=on_probe,
        ):
            found.append(addr)
            progress.console.log(f"[green]Found slave at address {addr}[/green]")

        progress.update(task, completed=total, description=f"Scanning {port}  done")

    if not found:
        console.print("[yellow]No slaves found.[/yellow]")
        return

    table = Table(title="Discovered PROFIBUS DP Slaves", show_lines=True)
    table.add_column("Address", style="bold cyan", justify="right")
    for addr in found:
        table.add_row(str(addr))
    console.print(table)


def _is_serial_error(exc: Exception) -> bool:
    try:
        from serial import SerialException
        return isinstance(exc, (SerialException, OSError))
    except ImportError:
        return isinstance(exc, OSError)


def _wait_for_port(port: str, interval: float = 2.0) -> None:
    from serial import Serial, SerialException
    while True:
        try:
            s = Serial(port)
            s.close()
            return
        except SerialException:
            sleep(interval)


if __name__ == "__main__":
    cli()
