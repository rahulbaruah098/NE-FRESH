from __future__ import annotations
import ast
from pathlib import Path


def load_source_definitions(path, function_names=(), assignment_names=(), namespace=None):
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    function_names = set(function_names)
    assignment_names = set(assignment_names)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in function_names:
            clone = ast.FunctionDef(
                name=node.name,
                args=node.args,
                body=node.body,
                decorator_list=[],
                returns=node.returns,
                type_comment=node.type_comment,
            ) if isinstance(node, ast.FunctionDef) else node
            selected.append(clone)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = []
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name): names.append(target.id)
            if assignment_names.intersection(names):
                selected.append(node)
    mod = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = dict(namespace or {})
    exec(compile(mod, str(path), "exec"), ns, ns)
    return ns
