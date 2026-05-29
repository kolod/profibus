# PROFIBUS DP Debug Tool

A Python console application for debugging PROFIBUS DP networks. Built with [pyprofibus](https://github.com/mbuesch/pyprofibus) and [Rich](https://github.com/Textualize/rich).

## Requirements

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)
- A serial RS-485 adapter connected to the PROFIBUS bus

## Installation

```bash
uv sync
```

## Usage

```bash
uv run python main.py [COMMAND] [OPTIONS]
```

If `--port` is not supplied, the tool lists all detected serial ports and prompts you to choose one. The last-used port is remembered and offered as the default on the next run.

### discover

Scan the bus for active PROFIBUS DP slaves (addresses 0–125) by sending FDL status requests to each address.

```bash
uv run python main.py discover [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `-p, --port TEXT` | _(prompt)_ | Serial port, e.g. `COM3` or `/dev/ttyUSB0` |
| `-b, --baudrate INTEGER` | `9600` | Bus baud rate |
| `--master-addr INTEGER` | `0` | Master station address used in outgoing telegrams |
| `--timeout FLOAT` | `0.05` | Per-address probe timeout in seconds |
| `-r, --autoreconnect` | off | Re-run the scan automatically if the serial device disconnects and reconnects |
| `--debug` | off | Print PHY-level telegram hex dumps |

**Examples:**

```bash
# Prompt for port, scan at 9600 baud
uv run python main.py discover

# Specify port explicitly
uv run python main.py discover -p COM3

# Higher baud rate with autoreconnect
uv run python main.py discover -p /dev/ttyUSB0 -b 19200 -r

# PHY debug output
uv run python main.py discover -p COM3 --debug
```

### diagnose

Read DP slave diagnostics (SlaveDiag) from a single slave address. Displays station status flags, ident number, master assignment, and extended diagnostic bytes if present.

```bash
uv run python main.py diagnose ADDRESS [OPTIONS]
```

| Argument / Option | Default | Description |
|---|---|---|
| `ADDRESS` | _(required)_ | Slave address to query (0–125) |
| `-p, --port TEXT` | _(prompt)_ | Serial port |
| `-b, --baudrate INTEGER` | `9600` | Bus baud rate |
| `--master-addr INTEGER` | `0` | Master station address used in outgoing telegrams |
| `--timeout FLOAT` | `0.5` | Response timeout per attempt in seconds |
| `--retries INTEGER` | `3` | Number of SlaveDiag attempts before giving up |
| `--warmup-probes INTEGER` | `10` | FdlStat probes sent before SlaveDiag to trigger baud-rate lock on devices like the Siemens CBP2 |
| `--warmup-interval FLOAT` | `0.1` | Interval between warm-up probes in seconds |
| `--debug` | off | Print PHY-level telegram hex dumps |

**Examples:**

```bash
# Read diagnostics from slave 3
uv run python main.py diagnose 3 -p COM1

# Fewer warm-up probes, shorter timeout
uv run python main.py diagnose 5 -p /dev/ttyUSB0 --warmup-probes 5 --timeout 0.3

# Debug mode to inspect raw telegrams
uv run python main.py diagnose 3 -p COM1 --debug
```

### exchange

Perform the full DP startup sequence (SetPrm + ChkCfg) with a slave and then run cyclic DataExchange to read process data. Requires the device's GSD or GSE file to obtain the ident number, module configuration, and default parameters.

```bash
uv run python main.py exchange ADDRESS GSD_FILE [OPTIONS]
```

| Argument / Option | Default | Description |
|---|---|---|
| `ADDRESS` | _(required)_ | Slave address (0–125) |
| `GSD_FILE` | _(required)_ | Path to the device's GSD or GSE file |
| `-p, --port TEXT` | _(prompt)_ | Serial port |
| `-b, --baudrate INTEGER` | `9600` | Bus baud rate |
| `--master-addr INTEGER` | `0` | Master station address used in outgoing telegrams |
| `-m, --module TEXT` | _(first module)_ | Module name to use (partial match) |
| `-n, --count INTEGER` | `1` | Number of DataExchange cycles (0 = continuous) |
| `--interval FLOAT` | `0.2` | Interval between cycles in seconds |
| `--timeout FLOAT` | `0.5` | Response timeout in seconds |
| `--warmup-probes INTEGER` | `5` | FdlStat probes sent before startup |
| `--debug` | off | Print PHY-level telegram hex dumps |

Position data is decoded automatically: 2-byte modules print a 16-bit value, 4-byte modules print a 32-bit value, and 6-byte TR-Mode modules print position plus speed (all big-endian).

**Examples:**

```bash
# Read one position sample from encoder at address 11
uv run python main.py exchange 11 GSE/TR09AAAB.GSE -p COM1

# Continuous read with an explicit module selection
uv run python main.py exchange 11 GSE/TR09AAAB.GSE -p COM1 -m "32 Bit" -n 0

# Debug mode to trace the startup sequence
uv run python main.py exchange 11 GSE/TR09AAAB.GSE -p COM1 --debug
```

## Hardware Notes

- The serial adapter must support RS-485 half-duplex operation.
- RTS/CTS-based TX/RX switching (`useRS485Class=False`) is used by default via `pyprofibus.phy_serial.CpPhySerial`. If your adapter requires RTS toggling via `serial.rs485.RS485Settings`, set `useRS485Class=True` in `profibus_debug/bus.py`.
- The `discover` command acts as a passive master that only sends FDL status request telegrams. It does not parameterize or exchange data with any slave.
- The `diagnose` command sends FdlStat warm-up probes followed by a SlaveDiag request. Tested against a Siemens CBP2 PROFIBUS adapter at 9600 baud.
- The `exchange` command implements the full DPM1 startup state machine. Tested against a TR Electronic CE58/CE65 absolute encoder at 9600 baud.
- Baud rates other than 9600 and 19200 may not be supported by all serial adapters. `pyprofibus` will warn if an unsupported rate is configured.

## Project Structure

```
main.py                       # CLI entry point (Click + Rich)
profibus_debug/
    bus.py                    # PHY open, device discovery
    diagnostics.py            # SlaveDiag request and response decoding
    exchange.py               # DP startup state machine and DataExchange
    gsd.py                    # GSD/GSE file parser (wraps pyprofibus.gsd)
    session.py                # Last-used port persistence
GSE/                          # Device GSD files
pyproject.toml
```

## Development

```bash
# Install / update dependencies
uv sync

# Run directly
uv run python main.py discover -p COM3
uv run python main.py diagnose 3 -p COM1
uv run python main.py exchange 11 GSE/TR09AAAB.GSE -p COM1

# Add a dependency
uv add <package>
```
