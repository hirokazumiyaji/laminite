def test_import_laminite() -> None:
    from importlib.metadata import version

    import laminite

    assert laminite.Laminite is not None
    assert laminite.__version__ == version("laminite")


def test_public_asgi_types() -> None:
    from laminite.types import ASGIApp, Message, Receive, Scope, Send

    assert ASGIApp is not None
    assert Scope is not None
    assert Message is not None
    assert Receive is not None
    assert Send is not None
