"""Import every module of the installed distribution and prove where it came from.

The Docker image is built from a wheel, not from the source tree, so a package
missing from `[tool.setuptools] packages` produces an image that is silently
incomplete — and the test suite, which runs from source, never notices.

Importing is not enough on its own: if the source tree is also importable (an
editable install, or the repository on `sys.path`) a missing package resolves
from there and the check passes while the wheel is still broken. So every module
is required to resolve under the *same* root as the top-level `core` package.

    cd /tmp && python -m scripts.verify_package
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

EXPECTED_ROOTS = ("core", "api", "worker", "scripts")

# Modules whose absence would break the running service rather than one feature.
# A package can be shipped but empty, and a subpackage can be dropped without
# its parent noticing, so these are named explicitly.
REQUIRED_MODULES = (
    "api.main",
    "worker.main",
    "core.runner",
    "core.models",
    "core.crypto",
    "core.security",
    "core.billing",
    "core.ratelimit",
    "core.llm.router",
    "core.llm.providers.anthropic_provider",
    "core.llm.providers.openai_compat",
    "core.llm.providers.google_provider",
    "core.orchestration.engine",
    "core.orchestration.presets",
    "core.tools.registry",
    "core.tools.artifacts",
    "core.tools.web",
)


def _module_path(name: str) -> Path | None:
    module = sys.modules.get(name)
    origin = getattr(module, "__file__", None)
    return Path(origin).resolve() if origin else None


def main() -> int:
    failures: list[str] = []
    imported = 0

    try:
        core = importlib.import_module("core")
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: cannot import `core`: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    core_file = getattr(core, "__file__", None)
    if core_file is None:
        print("FAILED: `core` is a namespace package with no __init__.py", file=sys.stderr)
        return 1

    install_root = Path(core_file).resolve().parent.parent
    print(f"checking distribution at {install_root}")

    for root in EXPECTED_ROOTS:
        try:
            package = importlib.import_module(root)
        except Exception as exc:  # noqa: BLE001 - report, do not abort the sweep
            failures.append(f"{root}: {type(exc).__name__}: {exc}")
            continue
        imported += 1

        paths = getattr(package, "__path__", None)
        if paths is None:
            continue
        for module in pkgutil.walk_packages(paths, prefix=f"{root}."):
            try:
                importlib.import_module(module.name)
                imported += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{module.name}: {type(exc).__name__}: {exc}")

    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"REQUIRED {name}: {type(exc).__name__}: {exc}")
            continue

        # The decisive check: a module that resolved from somewhere other than
        # the distribution under test means the wheel is missing it and another
        # copy on sys.path is covering for the gap.
        path = _module_path(name)
        if path is not None and install_root not in path.parents:
            failures.append(
                f"REQUIRED {name}: imported from {path}, which is outside the "
                f"distribution at {install_root} - it is missing from the built package"
            )

    if failures:
        print(f"FAILED: {len(failures)} problem(s):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"OK: imported {imported} modules across {', '.join(EXPECTED_ROOTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
