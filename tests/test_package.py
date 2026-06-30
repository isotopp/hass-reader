import hass_reader


def test_package_exposes_cli_entry_point() -> None:
    assert callable(hass_reader.main)
