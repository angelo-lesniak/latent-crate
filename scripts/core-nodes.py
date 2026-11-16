#!/usr/bin/env python3
"""Print config/comfy/core-nodes.txt: the node types a ComfyUI checkout
registers without a custom node, read from its source without importing it.

The types are the keys of NODE_CLASS_MAPPINGS in nodes.py and in each
module ComfyUI loads as built in (the comfy_extras files nodes.py lists in
init_builtin_extra_nodes, every comfy_api_nodes/nodes_*.py); where a
module has no such keys, the ids of the classes its extension's
get_node_list returns (see node_list), or without that method, the ids its
node classes and schemas name (see schema_ids). An id that is a computed
expression, a node list that is no literal list of class names, and a
listed class without exactly one id are refused, since this reader cannot
see their value; a node type it misses is reported by
`workflows check`, never passed. Regenerate after a COMFYUI_COMMIT bump:

    git clone --filter=blob:none https://github.com/comfyanonymous/ComfyUI <dir>
    git -C <dir> checkout <COMFYUI_COMMIT>
    python3 scripts/core-nodes.py <dir> <COMFYUI_COMMIT> > config/comfy/core-nodes.txt
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# The class attributes that name a node's id: V3 schemas read `node_id` or
# `NODE_ID`; nodes_hooks.py fills an empty NODE_CLASS_MAPPINGS from `NodeId`.
CLASS_ID_NAMES = ("node_id", "NODE_ID", "NodeId")


def literal(node: ast.expr, where: str) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        raise SystemExit(f"core-nodes: {where}: a node type that is not a string literal: {ast.unparse(node)}")
    return node.value


def mapping_keys(tree: ast.Module, where: str) -> list[str] | None:
    """The keys of a top-level `NODE_CLASS_MAPPINGS = {...}`, or None without one."""
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "NODE_CLASS_MAPPINGS" for target in statement.targets
        ):
            if not isinstance(statement.value, ast.Dict) or None in statement.value.keys:
                raise SystemExit(f"core-nodes: {where}: NODE_CLASS_MAPPINGS is not a dictionary literal")
            return [literal(key, where) for key in statement.value.keys]
    return None


def schema_ids(tree: ast.Module, scope: ast.AST, where: str) -> list[str]:
    """The ids the schemas in `scope` (a class, or the whole module) name:
    a `node_id=` literal; a literal first argument to a module helper whose
    first parameter is `node_id`; a `schema.node_id = ` literal, as a
    subclass renames its parent's schema; a class's own id literal
    (CLASS_ID_NAMES), which `cls.node_id` reads."""
    helpers = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, ast.FunctionDef) and function.args.args and function.args.args[0].arg == "node_id"
    }
    found = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            values = [keyword.value for keyword in node.keywords if keyword.arg == "node_id"]
            if isinstance(node.func, ast.Name) and node.func.id in helpers and node.args:
                values.append(node.args[0])
            # A name or attribute is the helper's parameter or the class's own id, read below.
            found += [literal(value, where) for value in values if not isinstance(value, (ast.Name, ast.Attribute))]
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Attribute)
            and node.targets[0].attr == "node_id"
        ):
            found.append(literal(node.value, where))
        elif isinstance(node, ast.ClassDef):
            for statement in node.body:
                target = (
                    statement.targets[0] if isinstance(statement, ast.Assign) else getattr(statement, "target", None)
                )
                value = getattr(statement, "value", None)
                if isinstance(target, ast.Name) and target.id in CLASS_ID_NAMES and value is not None:
                    if not (isinstance(value, ast.Constant) and value.value is None):
                        found.append(literal(value, where))
    return found


def class_id(tree: ast.Module, name: str, where: str) -> str:
    """The id of the module's class `name`: its own, else the one it
    inherits from a base class of the same module."""
    classes = {statement.name: statement for statement in tree.body if isinstance(statement, ast.ClassDef)}
    if name not in classes:
        raise SystemExit(f"core-nodes: {where}: get_node_list names {name}, which is no class of the module")
    ids = schema_ids(tree, classes[name], where)
    if not ids:
        bases = [base.id for base in classes[name].bases if isinstance(base, ast.Name) and base.id in classes]
        ids = [class_id(tree, base, where) for base in bases]
    if len(ids) != 1:
        raise SystemExit(f"core-nodes: {where}: class {name} names {len(ids)} node ids, not one: {ids}")
    return ids[0]


def node_list(tree: ast.Module, where: str) -> list[str] | None:
    """The ids of the classes an extension's `get_node_list` returns as a
    list literal, or None without that method; ComfyUI registers only those,
    so a class left out of the list (a test node, a commented-out entry) is
    no core node."""
    methods = [
        function
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and function.name == "get_node_list"
    ]
    if not methods:
        return None
    body = methods[0].body
    if (
        len(methods) != 1
        or len(body) != 1
        or not isinstance(body[0], ast.Return)
        or not isinstance(body[0].value, ast.List)
        or not all(isinstance(item, ast.Name) for item in body[0].value.elts)
    ):
        raise SystemExit(f"core-nodes: {where}: get_node_list is not one method returning a list of class names")
    return [class_id(tree, item.id, where) for item in body[0].value.elts]


def extras_files(tree: ast.Module) -> list[str]:
    for function in tree.body:
        if isinstance(function, ast.AsyncFunctionDef) and function.name == "init_builtin_extra_nodes":
            for statement in ast.walk(function):
                if isinstance(statement, ast.Assign) and getattr(statement.targets[0], "id", "") == "extras_files":
                    return [literal(item, "nodes.py extras_files") for item in statement.value.elts]
    raise SystemExit("core-nodes: nodes.py: no extras_files list in init_builtin_extra_nodes")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: core-nodes.py <comfyui-checkout> <commit>")
    root, commit = Path(argv[0]), argv[1]
    nodes = ast.parse((root / "nodes.py").read_text(encoding="utf-8-sig"))
    types = set(mapping_keys(nodes, "nodes.py") or [])
    modules = [root / "comfy_extras" / name for name in extras_files(nodes)]
    modules += sorted((root / "comfy_api_nodes").glob("nodes_*.py"))
    for path in modules:
        where = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        listed = node_list(tree, where)
        found = mapping_keys(tree, where) or (schema_ids(tree, tree, where) if listed is None else listed)
        # A miss leaves a core node out, which `workflows check` reports, so
        # an empty module (nodes_replacements.py registers none) is only noted.
        if not found:
            print(f"core-nodes: {where}: no node types", file=sys.stderr)
        types.update(found)
    sys.stdout.write(f"# ComfyUI {commit}\n" + "".join(f"{name}\n" for name in sorted(types)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
