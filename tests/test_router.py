import pytest

from laminite.router import MethodNotAllowed, NotFound, Router


def test_static_map_only() -> None:
    router: Router[str] = Router()
    router.add("/hello", "GET", "hello")
    match = router.resolve("GET", "/hello")
    assert match.value == "hello"
    assert match.path_params == {}
    assert match.is_head is False


def test_backtrack_to_param_route() -> None:
    router: Router[str] = Router()
    router.add("/a/b/c", "GET", "static")
    router.add("/a/{x}/d", "GET", "dynamic")
    match = router.resolve("GET", "/a/b/d")
    assert match.value == "dynamic"
    assert match.path_params == {"x": "b"}


def test_method_not_allowed_includes_head_when_get() -> None:
    router: Router[str] = Router()
    router.add("/items", "GET", "list")
    with pytest.raises(MethodNotAllowed) as exc_info:
        router.resolve("POST", "/items")
    assert "GET" in exc_info.value.allowed
    assert "HEAD" in exc_info.value.allowed


def test_static_get_and_dynamic_post() -> None:
    router: Router[str] = Router()
    router.add("/a/b", "GET", "static-get")
    router.add("/a/{x}", "POST", "dynamic-post")
    match = router.resolve("POST", "/a/b")
    assert match.value == "dynamic-post"
    assert match.path_params == {"x": "b"}
    match_get = router.resolve("GET", "/a/b")
    assert match_get.value == "static-get"


def test_head_falls_back_to_get_static() -> None:
    router: Router[str] = Router()
    router.add("/x", "GET", "get")
    match = router.resolve("HEAD", "/x")
    assert match.value == "get"
    assert match.is_head is True


def test_head_falls_back_to_get_dynamic() -> None:
    router: Router[str] = Router()
    router.add("/items/{item_id}", "GET", "get")
    match = router.resolve("HEAD", "/items/1")
    assert match.value == "get"
    assert match.path_params == {"item_id": "1"}
    assert match.is_head is True


def test_trailing_slash_distinct() -> None:
    router: Router[str] = Router()
    router.add("/items", "GET", "no-slash")
    router.add("/items/", "GET", "slash")
    assert router.resolve("GET", "/items").value == "no-slash"
    assert router.resolve("GET", "/items/").value == "slash"


def test_duplicate_route_raises() -> None:
    router: Router[str] = Router()
    router.add("/x", "GET", "a")
    with pytest.raises(ValueError):
        router.add("/x", "GET", "b")


def test_conflicting_param_names() -> None:
    router: Router[str] = Router()
    router.add("/items/{id}", "GET", "a")
    with pytest.raises(ValueError):
        router.add("/items/{item_id}/tags", "GET", "b")


def test_duplicate_param_name_in_template() -> None:
    router: Router[str] = Router()
    with pytest.raises(ValueError):
        router.add("/a/{id}/b/{id}", "GET", "x")


def test_invalid_template() -> None:
    router: Router[str] = Router()
    with pytest.raises(ValueError):
        router.add("/files/{name}.json", "GET", "x")
    with pytest.raises(ValueError):
        router.add("items", "GET", "x")


def test_not_found() -> None:
    router: Router[str] = Router()
    router.add("/only", "GET", "x")
    with pytest.raises(NotFound):
        router.resolve("GET", "/missing")


def test_failed_static_registration_rolls_back() -> None:
    router: Router[str] = Router()
    # Simulate a trie entry without a matching static map entry.
    node = router._root.static.setdefault("ghost", router._root.__class__())
    node.methods["GET"] = "stale"
    with pytest.raises(ValueError):
        router.add("/ghost", "GET", "new")
    assert "/ghost" not in router._static
    assert router.resolve("GET", "/ghost").value == "stale"
