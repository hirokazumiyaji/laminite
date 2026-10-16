def test_import_laminite() -> None:
    import laminite

    assert laminite.Laminite is not None
    assert laminite.__version__ == "0.1.0"
