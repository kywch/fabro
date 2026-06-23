




from textwrap import dedent
from types import ModuleType
from typing import Any
from typing import Generator

import pytest
from _pytest.monkeypatch import MonkeyPatch












































































































































































































































































class TestImportPath:
            import_path(tmp_path / "invalid.py", root=tmp_path)

    @pytest.fixture
    def simple_module(self, tmp_path: Path) -> Path:
        fn = tmp_path / "_src/tests/mymod.py"
        fn.parent.mkdir(parents=True)
        fn.write_text("def foo(x): return 40 + x", encoding="utf-8")
        return fn

    def test_importmode_importlib(self, simple_module: Path, tmp_path: Path) -> None:
        """`importlib` mode does not change sys.path."""
        module = import_path(simple_module, mode="importlib", root=tmp_path)
        assert module.foo(2) == 42  # type: ignore[attr-defined]
        assert str(simple_module.parent) not in sys.path
        assert module.__name__ in sys.modules
        assert module.__name__ == "_src.tests.mymod"
        assert "_src" in sys.modules
        assert "_src.tests" in sys.modules

    def test_importmode_twice_is_different_module(
        self, simple_module: Path, tmp_path: Path
    ) -> None:
        """`importlib` mode always returns a new module."""
        module1 = import_path(simple_module, mode="importlib", root=tmp_path)
        module2 = import_path(simple_module, mode="importlib", root=tmp_path)
        assert module1 is not module2

    def test_no_meta_path_found(
        self, simple_module: Path, monkeypatch: MonkeyPatch, tmp_path: Path





class TestImportPath:
        # mode='importlib' fails if no spec is found to load the module
        import importlib.util

        monkeypatch.setattr(
            importlib.util, "spec_from_file_location", lambda *args: None
        )
