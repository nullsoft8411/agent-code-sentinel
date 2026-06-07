#!/usr/bin/env python3
"""Extract a migration inventory from the local Code Sentinel source tree.

The extractor intentionally uses AST parsing instead of importing the source
project. Importing would require the full SaaS runtime dependencies and could
execute module-level code; the migration inventory only needs static structure.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any


MODEL_ROOT = Path("infrastructure/persistence/models")
RUNTIME_ROOTS = (
    Path("application"),
    Path("infrastructure/execution"),
    Path("infrastructure/messaging"),
    Path("infrastructure/persistence"),
    Path("workers"),
)
REQUIRED_TABLES = (
    "projects",
    "health_bot_tasks",
    "health_bot_scan_jobs",
    "health_bot_scan_findings",
    "quality_gate_runs",
    "quality_gate_fix_sessions",
    "quality_gate_states",
    "qg_workflows",
    "health_bot_claude_sessions",
    "tracked_prs",
    "audit_logs",
    "health_bot_state",
)
REQUIRED_RUNTIME_MODULES = (
    "application/task_service.py",
    "application/scanner_service.py",
    "application/git_service.py",
    "application/services/task_creation_service.py",
    "application/services/scan_execution_service.py",
    "application/services/qg_workflow_orchestrator.py",
    "infrastructure/execution/claude_executor.py",
    "infrastructure/execution/qg_test_runner.py",
    "infrastructure/messaging/stream_service.py",
    "workers/task_processing_worker.py",
    "workers/stream_task_worker.py",
    "workers/scan_worker.py",
    "workers/scan_coordinator.py",
    "workers/pr_monitor_worker.py",
)


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return call_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return node.attr
    return None


def literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def assigned_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def module_name(source_root: Path, path: Path) -> str:
    return path.relative_to(source_root).with_suffix("").as_posix().replace("/", ".")


def iter_python_files(source_root: Path, roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        absolute = source_root / root
        if not absolute.exists():
            continue
        files.extend(
            path
            for path in absolute.rglob("*.py")
            if "__pycache__" not in path.parts and path.is_file()
        )
    return sorted(set(files), key=lambda item: item.relative_to(source_root).as_posix())


def extract_models(source_root: Path) -> dict[str, Any]:
    tables: list[dict[str, Any]] = []
    enums: list[dict[str, Any]] = []

    for path in iter_python_files(source_root, (MODEL_ROOT,)):
        tree = parse_python(path)
        relative_path = path.relative_to(source_root).as_posix()

        for class_node in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
            table_name: str | None = None
            columns: list[str] = []
            relationships: list[str] = []
            indexes: list[str] = []
            enum_values: list[str] = []

            for item in class_node.body:
                if isinstance(item, ast.Assign):
                    targets = [assigned_name(target) for target in item.targets]
                    if "__tablename__" in targets:
                        table_name = literal_string(item.value)

                    target_name = next((name for name in targets if name), None)
                    value_call = call_name(item.value)
                    if target_name and value_call in {"Column", "mapped_column"}:
                        columns.append(target_name)
                    if target_name and value_call == "relationship":
                        relationships.append(target_name)
                    if target_name and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                        if target_name.isupper():
                            enum_values.append(item.value.value)

                    if target_name == "__table_args__" and isinstance(item.value, ast.Tuple):
                        for element in item.value.elts:
                            if call_name(element) == "Index" and isinstance(element, ast.Call) and element.args:
                                index_name = literal_string(element.args[0])
                                if index_name:
                                    indexes.append(index_name)

            if table_name:
                tables.append(
                    {
                        "class_name": class_node.name,
                        "columns": sorted(columns),
                        "file": relative_path,
                        "indexes": sorted(indexes),
                        "module": module_name(source_root, path),
                        "relationships": sorted(relationships),
                        "table_name": table_name,
                    }
                )

            if enum_values:
                enums.append(
                    {
                        "class_name": class_node.name,
                        "file": relative_path,
                        "module": module_name(source_root, path),
                        "values": sorted(enum_values),
                    }
                )

    return {
        "enums": sorted(enums, key=lambda item: (item["file"], item["class_name"])),
        "tables": sorted(tables, key=lambda item: item["table_name"]),
    }


def extract_runtime_modules(source_root: Path) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for path in iter_python_files(source_root, RUNTIME_ROOTS):
        tree = parse_python(path)
        relative_path = path.relative_to(source_root).as_posix()
        classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
        functions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        method_count = sum(
            1
            for class_node in tree.body
            if isinstance(class_node, ast.ClassDef)
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        if not classes and not functions and method_count == 0:
            continue
        modules.append(
            {
                "classes": sorted(classes),
                "file": relative_path,
                "functions": sorted(functions),
                "method_count": method_count,
                "module": module_name(source_root, path),
                "root": relative_path.split("/", 1)[0],
            }
        )
    return modules


def build_inventory(source_root: Path) -> dict[str, Any]:
    source_root = source_root.resolve()
    models = extract_models(source_root)
    runtime_modules = extract_runtime_modules(source_root)
    table_names = {table["table_name"] for table in models["tables"]}
    runtime_files = {module["file"] for module in runtime_modules}

    return {
        "coverage": {
            "required_runtime_modules_present": {
                name: name in runtime_files for name in REQUIRED_RUNTIME_MODULES
            },
            "required_tables_present": {
                name: name in table_names for name in REQUIRED_TABLES
            },
            "runtime_module_count": len(runtime_modules),
            "table_count": len(models["tables"]),
        },
        "generated_by": "scripts/extract_local_code_sentinel_inventory.py",
        "models": models,
        "runtime_modules": runtime_modules,
        "source_root": str(source_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="/home/pika/projekte/code-sentinel")
    parser.add_argument("--output", default="-")
    args = parser.parse_args()

    inventory = build_inventory(Path(args.source))
    payload = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    if args.output == "-":
        print(payload, end="")
    else:
        Path(args.output).write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
