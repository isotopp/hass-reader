import tomllib
from pathlib import Path

from hass_reader.cli import IngestCommandResult, main


def test_ingest_command_uses_documented_defaults(tmp_path, capsys) -> None:
    calls = []

    def runner(args) -> IngestCommandResult:
        calls.append(args)
        return IngestCommandResult(fetched=1, cached=2, normalized=3, aligned=4)

    exit_code = main(["ingest", "--db", str(tmp_path / "cache.sqlite3")], ingest_runner=runner)

    assert exit_code == 0
    assert calls[0].start == "2025-07-01"
    assert calls[0].end == "2026-07-01"
    assert calls[0].timezone == "Europe/Amsterdam"
    assert calls[0].influx_url == "http://192.168.1.10:8086"
    assert calls[0].influx_database == "hass"
    assert calls[0].force_refresh is False
    assert "fetched=1 cached=2 normalized=3 aligned=4" in capsys.readouterr().out


def test_ingest_command_reports_failures_without_traceback(tmp_path, capsys) -> None:
    def runner(_args) -> IngestCommandResult:
        raise RuntimeError("connection failed")

    exit_code = main(["ingest", "--db", str(tmp_path / "cache.sqlite3")], ingest_runner=runner)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "connection failed" in captured.err
    assert "Traceback" not in captured.err


def test_console_scripts_are_wired_to_cli_main() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())

    assert pyproject["project"]["scripts"]["hass-reader"] == "hass_reader:main"
