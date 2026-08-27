from __future__ import annotations

import ast
import json
import re
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _literal(node, default=None):
    try:
        return ast.literal_eval(node)
    except Exception:
        return default


def _route_decorator_info(dec, fn_name):
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not (isinstance(func, ast.Attribute) and func.attr == "route"):
        return None
    if not dec.args:
        return None
    path = _literal(dec.args[0])
    if not isinstance(path, str):
        return None
    methods = ["GET"]
    endpoint = fn_name
    for kw in dec.keywords:
        if kw.arg == "methods":
            value = _literal(kw.value)
            if isinstance(value, (list, tuple)):
                methods = [str(v).upper() for v in value]
        elif kw.arg == "endpoint":
            value = _literal(kw.value)
            if isinstance(value, str) and value:
                endpoint = value
    return path, sorted(set(methods)), endpoint


def _login_role(decorators):
    for dec in decorators:
        if isinstance(dec, ast.Name) and dec.id == "login_required":
            return "authenticated"
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "login_required":
            role = None
            if dec.args:
                role = _literal(dec.args[0])
            for kw in dec.keywords:
                if kw.arg == "role":
                    role = _literal(kw.value)
            return str(role) if role else "authenticated"
        if isinstance(dec, ast.Name) and dec.id == "api_login_required":
            return "api_authenticated"
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "api_login_required":
            return "api_authenticated"
    return "public"


def route_contracts(root: Path | None = None):
    root = root or project_root()
    rows = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".venv", "venv", "tests"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            role = _login_role(node.decorator_list)
            for dec in node.decorator_list:
                info = _route_decorator_info(dec, node.name)
                if not info:
                    continue
                route_path, methods, endpoint = info
                for method in methods:
                    rows.append({
                        "path": route_path,
                        "method": method,
                        "endpoint": endpoint,
                        "role": role,
                    })
    rows.sort(key=lambda r: (r["path"], r["method"], r["endpoint"], r["role"]))
    return rows


def build_only_aliases(root: Path | None = None):
    root = root or project_root()
    rows = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".venv", "venv", "tests"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(path))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "add_url_rule"):
                continue
            build_only = False
            endpoint = None
            rule = None
            if node.args:
                rule = _literal(node.args[0])
            for kw in node.keywords:
                if kw.arg == "endpoint": endpoint = _literal(kw.value)
                if kw.arg == "build_only": build_only = bool(_literal(kw.value, False))
            if build_only and isinstance(rule, str) and isinstance(endpoint, str):
                rows.append({"path": rule, "endpoint": endpoint})
    rows.sort(key=lambda r: (r["path"], r["endpoint"]))
    return rows


FORM_START_RE = re.compile(r"<form\b[^>]*>", re.I)
FORM_END_RE = re.compile(r"</form\s*>", re.I)
FIELD_TAG_RE = re.compile(r"<(?:input|select|textarea)\b[^>]*>", re.I)


def _quoted_attr(tag: str, name: str) -> str:
    m = re.search(r"\b" + re.escape(name) + r'\s*=\s*"([^"]*)"', tag, re.I | re.S)
    if m:
        return m.group(1)
    m = re.search(r"\b" + re.escape(name) + r"\s*=\s*'([^']*)'", tag, re.I | re.S)
    return m.group(1) if m else ""


def _normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def form_contracts(root: Path | None = None):
    root = root or project_root()
    rows = []
    template_root = root / "templates"
    for path in sorted(template_root.rglob("*.html")):
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(root)).replace("\\", "/")
        pos = 0
        idx = 0
        while True:
            start_match = FORM_START_RE.search(text, pos)
            if not start_match:
                break
            end_match = FORM_END_RE.search(text, start_match.end())
            if not end_match:
                break
            idx += 1
            start_tag = start_match.group(0)
            body = text[start_match.end():end_match.start()]
            method = (_quoted_attr(start_tag, "method") or "GET").upper()
            action = _normalize_ws(_quoted_attr(start_tag, "action"))
            names = []
            for field_match in FIELD_TAG_RE.finditer(body):
                name = _normalize_ws(_quoted_attr(field_match.group(0), "name"))
                if name:
                    names.append(name)
            rows.append({
                "template": rel,
                "form_index": idx,
                "method": method,
                "action": action,
                "field_names": sorted(set(names)),
            })
            pos = end_match.end()
    return rows


def literal_url_for_endpoints(root: Path | None = None):
    root = root or project_root()
    endpoints = set()
    pat = re.compile(r"url_for\(\s*['\"]([^'\"]+)['\"]")
    for path in list((root / "templates").rglob("*.html")) + list((root / "static").rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="replace")
        endpoints.update(pat.findall(text))
    return sorted(endpoints)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
