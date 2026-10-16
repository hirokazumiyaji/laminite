from dataclasses import dataclass

from laminite import JSONResponse, Laminite, Request
from laminite.testing import TestClient


@dataclass
class DomainError(Exception):
    code: str


@dataclass
class NotFoundError(DomainError):
    pass


def test_mro_exception_handler() -> None:
    app = Laminite()

    @app.exception_handler(DomainError)
    def handle_domain(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"detail": exc.code}, status_code=400)

    @app.get("/x")
    def x() -> None:
        raise NotFoundError("missing")

    client = TestClient(app)
    res = client.get("/x")
    assert res.status_code == 400
    assert res.json() == {"detail": "missing"}
    client.close()


def test_provider_exception_handled() -> None:
    app = Laminite()

    @dataclass
    class Svc:
        pass

    @app.provider
    def svc() -> Svc:
        raise NotFoundError("from-provider")

    @app.exception_handler(DomainError)
    def handle_domain(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse({"detail": exc.code}, status_code=404)

    @app.get("/")
    def root(s: Svc) -> dict[str, str]:
        return {"ok": "1"}

    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 404
    assert res.json() == {"detail": "from-provider"}
    client.close()
