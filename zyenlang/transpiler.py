from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set


class ZyenError(Exception):
    pass


@dataclass
class StructDef:
    name: str
    fields: Dict[str, str] = field(default_factory=dict)
    defaults: Dict[str, str] = field(default_factory=dict)
    methods: Dict[str, "FunctionDef"] = field(default_factory=dict)


@dataclass
class FunctionDef:
    name: str
    ret_type: str
    params: Dict[str, str] = field(default_factory=dict)
    defaults: Dict[str, str] = field(default_factory=dict)
    defined: bool = False
    declared_line: int = 0
    defined_line: int = 0


@dataclass
class TranspileContext:
    structs: Dict[str, StructDef] = field(default_factory=dict)
    functions: Dict[str, FunctionDef] = field(default_factory=dict)
    symbols: Dict[str, str] = field(default_factory=dict)
    consts: set[str] = field(default_factory=set)
    ptr_targets: Dict[str, str] = field(default_factory=dict)
    list_refs: Set[str] = field(default_factory=set)
    scope_stack: List[Set[str]] = field(default_factory=list)
    current_function: Optional[str] = None
    loop_depth: int = 0
    auto_ptr_counter: int = 0


BUILTIN_TYPES = {"int", "float", "bool", "str", "ptr", "void", "List"}
INTERNAL_TYPES = {"Any"}
INT_MIN = -2147483648
INT_MAX = 2147483647


def is_int_literal(expr: str) -> bool:
    return re.match(r"^-?\d+$", expr.strip()) is not None


def parse_int_literal(expr: str) -> int:
    return int(expr.strip())


def check_int_literal_range(expr: str, line_no: int, target_type: str = "int") -> None:
    """Reject integer literals that cannot fit ZyenLang's current int."""
    if target_type != "int":
        return
    if not is_int_literal(expr):
        return
    value = parse_int_literal(expr)
    if value < INT_MIN or value > INT_MAX:
        raise ZyenError(
            f"line {line_no}: integer literal out of range for int: {expr.strip()} "
            f"(allowed {INT_MIN}..{INT_MAX}); use str for huge numbers for now"
        )


def assert_expr_literals_fit(expr: str, ctx: "TranspileContext", line_no: int, expected_type: str = "") -> None:
    """Small static guard against obvious int overflow in declarations/returns/sets."""
    raw = expr.strip()
    if is_int_literal(raw):
        typ = expected_type or infer_type(raw, ctx)
        if ztype_base(typ) == "int":
            check_int_literal_range(raw, line_no, "int")
    if is_array_literal(raw):
        body = raw[1:-1].strip()
        if body:
            for item in split_args(body):
                if is_int_literal(item):
                    check_int_literal_range(item, line_no, "int")


def strip_comment(line: str) -> str:
    in_str = False
    escaped = False
    for i in range(len(line) - 1):
        ch = line[i]
        if ch == '"' and not escaped:
            in_str = not in_str
        if not in_str and line[i : i + 2] == "//":
            return line[:i]
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return line


def clean_lines(source: str) -> List[Tuple[int, str]]:
    """Convert physical source lines into parser-ready logical lines.

    Older ZyenLang versions were almost fully line-oriented and only joined
    multi-line list literals.  v0.1.47 upgrades this stage into a small
    tokenizer-aware statement assembler.  It keeps strings/comments safe and
    allows common formatted code such as:

        fn add(
            a: int,
            b: int
        ) -> int {
            return add(
                a,
                b
            );
        }

    It is still intentionally simple: it does not split two statements written
    on one physical line.  Zyen style remains one statement per logical line.
    """

    def scan_state(text: str, state: Tuple[int, int, int, bool, bool]) -> Tuple[int, int, int, bool, bool]:
        paren, bracket, brace, in_str, escaped = state
        for ch in text:
            if ch == '"' and not escaped:
                in_str = not in_str
            elif not in_str:
                if ch == '(':
                    paren += 1
                elif ch == ')':
                    paren -= 1
                elif ch == '[':
                    bracket += 1
                elif ch == ']':
                    bracket -= 1
                elif ch == '{':
                    brace += 1
                elif ch == '}':
                    brace -= 1
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
        return paren, bracket, brace, in_str, escaped

    def normalize_space(text: str) -> str:
        # Do not collapse whitespace globally: string literals may intentionally
        # contain repeated spaces.  Physical lines are already stripped and
        # joined with a single separator, which is enough for parser regexes.
        return text.strip()

    def is_block_header(text: str) -> bool:
        t = text.strip()
        if not t.endswith("{"):
            return False
        if re.match(r"^(fn|struct|if|for)\b", t):
            return True
        if re.match(r"^}\s*else(\s+if\b.*)?\s*\{\s*$", t):
            return True
        if re.match(r"^else(\s+if\b.*)?\s*\{\s*$", t):
            return True
        return False

    def logical_complete(text: str, state: Tuple[int, int, int, bool, bool]) -> bool:
        paren, bracket, brace, in_str, _escaped = state
        if in_str:
            return False
        t = text.strip()
        if not t:
            return True
        if t == "}":
            return True
        if is_block_header(t):
            return True
        if t.endswith(";") and paren == 0 and bracket == 0 and brace == 0:
            return True
        return False

    result: List[Tuple[int, str]] = []
    pending: List[str] = []
    pending_start = 0
    state = (0, 0, 0, False, False)  # paren, bracket, brace, in_str, escaped

    for no, raw in enumerate(source.splitlines(), start=1):
        line = strip_comment(raw).strip()
        if not line:
            continue

        if not pending:
            # Closing block lines are structural tokens, not expression braces.
            if line == "}" or re.match(r"^}\s*else", line):
                result.append((no, normalize_space(line)))
                continue
            # Accept `else {` / `else if (...) {` even when users format the
            # closing brace on the previous line.  The emitter expects
            # `} else {` as one logical line, so normalize it here.
            if result and line.startswith("else") and result[-1][1] == "}":
                prev_no, _prev = result.pop()
                combined = normalize_space("} " + line)
                result.append((prev_no, combined))
                continue
            pending_start = no

        pending.append(line)
        joined = normalize_space(" ".join(pending))
        state = scan_state(line, state)

        if min(state[0], state[1], state[2]) < 0:
            raise ZyenError(f"line {no}: unmatched closing delimiter")

        if logical_complete(joined, state):
            # Block headers intentionally leave one `{` unmatched because it is
            # consumed later by the structured emitter.  Reset state at logical
            # boundaries so the following body statements are parsed normally.
            result.append((pending_start, joined))
            pending = []
            pending_start = 0
            state = (0, 0, 0, False, False)

    if pending:
        joined = normalize_space(" ".join(pending))
        if state[3]:
            raise ZyenError(f"line {pending_start}: unterminated string literal")
        raise ZyenError(f"line {pending_start}: incomplete statement or header: {joined}")
    return result

def split_args(text: str) -> List[str]:
    args: List[str] = []
    cur: List[str] = []
    depth = 0
    in_str = False
    escaped = False
    for ch in text:
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch in "({[<":
                depth += 1
            elif ch in ")}]>":
                depth -= 1
            elif ch == "," and depth == 0:
                item = "".join(cur).strip()
                if item:
                    args.append(item)
                cur = []
                continue
        cur.append(ch)
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    item = "".join(cur).strip()
    if item:
        args.append(item)
    return args


def _struct_default_init(struct_name: str, ctx: "TranspileContext") -> str:
    """Emit a C99 designated initializer that applies declared field defaults.

    For a struct with no field defaults, returns `(StructName){0}` — same as
    the pre-ZEP-0006 behaviour. For a struct with one or more defaults, emits
    `(StructName){ .a = 1, .b = "x" }`; C99 zero-fills the rest.
    """
    struct = ctx.structs.get(struct_name)
    if struct is None or not struct.defaults:
        return f"({struct_name}){{0}}"
    parts = []
    for field_name, default_expr in struct.defaults.items():
        value = transform_expr(default_expr, ctx)
        parts.append(f".{field_name} = {value}")
    return f"({struct_name}){{ " + ", ".join(parts) + " }"


def _strip_module_prefix_type(ztype: str) -> str:
    """Strip `mod.` qualifiers from a type expression.

    Imported user structs live in the global namespace, so `test.Car` is the
    same type as `Car`. We strip the prefix here so the rest of the pipeline
    only ever sees the unqualified name.
    """
    if not ztype:
        return ztype
    ztype = ztype.replace(" ", "")
    m = re.match(r"^([A-Za-z_]\w*)<(.+)>$", ztype)
    if m:
        inner = _strip_module_prefix_type(m.group(2))
        return f"{m.group(1)}<{inner}>"
    if "." in ztype:
        return ztype.split(".", 1)[1]
    return ztype


def ztype_base(ztype: str) -> str:
    ztype = ztype.strip()
    m = re.match(r"ptr\s*<\s*([A-Za-z_]\w*)\s*>", ztype)
    if m:
        return "ptr"
    return ztype


def ptr_inner_type(ztype: str) -> Optional[str]:
    m = re.match(r"ptr\s*<\s*([A-Za-z_]\w*)\s*>", ztype.strip())
    if m:
        return m.group(1)
    return None


def validate_user_type(ztype: str, line_no: int, context: str = "type") -> None:
    """Reject user-facing use of Any and bare ptr where a concrete target is required."""
    t = ztype.strip().replace(" ", "")
    if t == "Any" or ptr_inner_type(t) == "Any":
        raise ZyenError(f"line {line_no}: `Any` is internal to List; use a concrete type such as int/float/bool/str/ptr<T>/List")


def ensure_assignable(expected_type: str, actual_type: str, line_no: int, what: str = "assignment") -> None:
    expected = expected_type.replace(" ", "")
    actual = actual_type.replace(" ", "")
    eb = ztype_base(expected)
    ab = ztype_base(actual)
    if eb == "Any":
        return  # internal List cell storage only
    if ab == "Any":
        raise ZyenError(f"line {line_no}: List values are dynamic; cast explicitly before {what}, e.g. `(int)value` or `(str)value`")
    if eb == ab:
        return
    if eb == "float" and ab == "int":
        return
    if eb == "ptr" and ab in {"ptr", "none"}:
        return
    raise ZyenError(f"line {line_no}: type mismatch in {what}: expected `{expected_type}`, got `{actual_type}`")


def c_type(ztype: str) -> str:
    ztype = ztype.strip()
    pm = re.match(r"ptrstruct\s*<\s*([A-Za-z_]\w*)\s*>", ztype)
    if pm:
        return f"{pm.group(1)}*"
    base = ztype_base(ztype)
    mapping = {
        "int": "int",
        "float": "double",
        "bool": "bool",
        "str": "const char*",
        "ptr": "ptr",
        "void": "void",
        "Any": "Any",
        "List": "ZL_List",
    }
    return mapping.get(base, base)


def type_name_for_runtime(ztype: str) -> str:
    return ztype_base(ztype)


def is_none_literal(expr: str) -> bool:
    return expr.strip() == "None"


def parse_params(text: str, line_no: int) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Parse function parameters.

    v0.1.49 supports default parameter values:
        fn add(a: int, b: int = 1) -> int { ... }

    Return (params, defaults).  `params` preserves declaration order.
    Defaulted parameters must be trailing so call completion remains simple and
    C codegen can fill missing arguments from left to right.
    """
    params: Dict[str, str] = {}
    defaults: Dict[str, str] = {}
    text = text.strip()
    if not text:
        return params, defaults
    seen_default = False
    for part in split_args(text):
        part = part.strip()
        if not part:
            continue
        default_expr = ""
        # Split on a top-level '=' only.  Default expressions may contain nested
        # calls or strings, but currently should not contain assignment syntax.
        depth = 0
        in_str = False
        escaped = False
        eq_i = -1
        for idx, ch in enumerate(part):
            if ch == '"' and not escaped:
                in_str = not in_str
            elif not in_str:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "=" and depth == 0:
                    eq_i = idx
                    break
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
        if eq_i >= 0:
            default_expr = part[eq_i + 1 :].strip()
            part = part[:eq_i].strip()
            if not default_expr:
                raise ZyenError(f"line {line_no}: default parameter value is empty")
            seen_default = True
        elif seen_default:
            raise ZyenError(f"line {line_no}: non-default parameter cannot follow a default parameter")

        if ":" in part:
            name, typ = part.split(":", 1)
            name = name.strip()
            typ = typ.strip().replace(" ", "")
        else:
            # v0.1 default for untyped parameters in the C backend.
            name = part.strip()
            typ = "int"
        if not re.match(r"^[A-Za-z_]\w*$", name):
            raise ZyenError(f"line {line_no}: invalid parameter name: {name}")
        if name in params:
            raise ZyenError(f"line {line_no}: duplicate parameter name: {name}")
        validate_user_type(typ, line_no, "parameter")
        params[name] = typ
        if default_expr:
            defaults[name] = default_expr
    return params, defaults


def params_compatible(a: FunctionDef, b: FunctionDef) -> bool:
    return a.ret_type == b.ret_type and list(a.params.items()) == list(b.params.items())


def merge_function_signature(ctx: TranspileContext, fn: FunctionDef, line_no: int, is_definition: bool) -> None:
    """Register a function declaration or definition.

    A declaration looks like:
        fn add(a: int, b: int = 1) -> int;

    A definition looks like:
        fn add(a: int, b: int = 1) -> int { ... }

    Declarations may appear before definitions.  The definition may omit default
    values; defaults from the declaration are then reused.  If both sides provide
    defaults for the same parameter, they must match exactly.
    """
    old = ctx.functions.get(fn.name)
    if old is None:
        ctx.functions[fn.name] = fn
        return
    if not params_compatible(old, fn):
        raise ZyenError(f"line {line_no}: function `{fn.name}` declaration/definition signature does not match earlier declaration")
    if is_definition and old.defined:
        raise ZyenError(f"line {line_no}: function `{fn.name}` is already defined at line {old.defined_line}")
    merged_defaults = dict(old.defaults)
    for pname, default in fn.defaults.items():
        if pname in merged_defaults and merged_defaults[pname] != default:
            raise ZyenError(f"line {line_no}: default value for parameter `{pname}` of `{fn.name}` does not match earlier declaration")
        merged_defaults[pname] = default
    old.defaults = merged_defaults
    if is_definition:
        old.defined = True
        old.defined_line = line_no
    else:
        old.declared_line = old.declared_line or line_no

def skip_block(lines: List[Tuple[int, str]], start_i: int) -> int:
    """Return the index just after the block that starts at start_i."""
    depth = 0
    i = start_i
    while i < len(lines):
        line = lines[i][1]
        depth += line.count("{")
        depth -= line.count("}")
        i += 1
        if depth <= 0:
            return i
    return i


def collect_signatures(lines: List[Tuple[int, str]]) -> TranspileContext:
    ctx = TranspileContext()
    i = 0
    while i < len(lines):
        line_no, line = lines[i]
        sm = re.match(r"struct\s+([A-Za-z_]\w*)\s*\{\s*$", line)
        if sm:
            struct_name = sm.group(1)
            if struct_name in BUILTIN_TYPES:
                raise ZyenError(f"line {line_no}: `{struct_name}` is a built-in type name and cannot be used as a struct name")
            fields: Dict[str, str] = {}
            struct_defaults: Dict[str, str] = {}
            methods: Dict[str, FunctionDef] = {}
            i += 1
            while i < len(lines):
                f_no, f_line = lines[i]
                if f_line == "}":
                    break
                # Struct fields are part of the current object layout. v0.1.22
                # required `let this.name: type;`; v0.1.49 also accepts an
                # optional trailing default expression:
                #     let this.name: type;
                #     let this.name: type = expr;   (ZEP-0006)
                fm_field = re.match(r"let\s+this\.([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?)\s*(?:=\s*(.+?))?\s*;\s*$", f_line)
                if fm_field:
                    field_name = fm_field.group(1)
                    field_type = fm_field.group(2).replace(" ", "")
                    default_expr = fm_field.group(3)
                    validate_user_type(field_type, f_no, "struct field")
                    fields[field_name] = field_type
                    if default_expr is not None:
                        struct_defaults[field_name] = default_expr.strip()
                    i += 1
                    continue
                if re.match(r"(?:let\s+)?[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?\s*(?:=\s*.+?)?\s*;\s*$", f_line):
                    raise ZyenError(f"line {f_no}: struct field must use `let this.name: type;` or `let this.name: type = default;`, for example `let this.list_len: int;`")
                fm_method = re.match(r"fn\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?))?\s*\{\s*$", f_line)
                if fm_method:
                    method_name = fm_method.group(1)
                    params, defaults = parse_params(fm_method.group(2), f_no)
                    ret_type = (fm_method.group(3) or "void").replace(" ", "")
                    validate_user_type(ret_type, f_no, "return type")
                    methods[method_name] = FunctionDef(name=method_name, ret_type=ret_type, params=params, defaults=defaults, defined=True, defined_line=f_no)
                    c_params = {"this": f"ptrstruct<{struct_name}>"}
                    c_params.update(params)
                    c_defaults = dict(defaults)
                    c_name = f"{struct_name}_{method_name}"
                    merge_function_signature(ctx, FunctionDef(name=c_name, ret_type=ret_type, params=c_params, defaults=c_defaults, defined=True, defined_line=f_no), f_no, True)
                    i = skip_block(lines, i)
                    continue
                raise ZyenError(f"line {f_no}: invalid struct member; expected `let this.name: type;` or `fn method(...) -> type {{`")
            else:
                raise ZyenError(f"line {line_no}: struct `{struct_name}` is missing closing `}}`")
            ctx.structs[struct_name] = StructDef(name=struct_name, fields=fields, defaults=struct_defaults, methods=methods)
            i += 1
            continue

        # Explicit top-level function declaration / prototype.
        #     fn add(a: int, b: int = 1) -> int;
        fm_decl = re.match(r"fn\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?))?\s*;\s*$", line)
        if fm_decl:
            name = fm_decl.group(1)
            params, defaults = parse_params(fm_decl.group(2), line_no)
            ret_type = (fm_decl.group(3) or "void").replace(" ", "")
            validate_user_type(ret_type, line_no, "return type")
            merge_function_signature(ctx, FunctionDef(name=name, ret_type=ret_type, params=params, defaults=defaults, defined=False, declared_line=line_no), line_no, False)
            i += 1
            continue

        fm = re.match(r"fn\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?))?\s*\{\s*$", line)
        if fm:
            name = fm.group(1)
            params, defaults = parse_params(fm.group(2), line_no)
            ret_type = (fm.group(3) or "void").replace(" ", "")
            validate_user_type(ret_type, line_no, "return type")
            merge_function_signature(ctx, FunctionDef(name=name, ret_type=ret_type, params=params, defaults=defaults, defined=True, defined_line=line_no), line_no, True)
        i += 1

    for name, fn in ctx.functions.items():
        if not fn.defined:
            raise ZyenError(f"line {fn.declared_line}: function `{name}` was declared but not defined")
    return ctx

def convert_struct_literal(expr: str, ctx: TranspileContext) -> str:
    # v0.1 supports compact one-line struct literals:
    # Point { x: 3.0, y: 4.0 } -> (Point){ .x = 3.0, .y = 4.0 }
    # Also accepts qualified `mod.Point { ... }` — imported structs live in the
    # global namespace, so we strip the alias before lookup.
    pattern = re.compile(r"\b(?:[A-Za-z_]\w*\.)?([A-Z][A-Za-z_]\w*)\s*\{([^{}]*)\}")

    def repl(m: re.Match[str]) -> str:
        typ = m.group(1)
        if typ not in ctx.structs:
            return m.group(0)
        body = m.group(2).strip()
        struct = ctx.structs[typ]
        parts: List[str] = []
        provided: set[str] = set()
        if body:
            for item in split_args(body):
                if ":" not in item:
                    raise ZyenError(f"invalid struct literal item `{item}`, expected `field: value`")
                field_name, value = item.split(":", 1)
                field_name = field_name.strip()
                provided.add(field_name)
                value = transform_expr(value.strip(), ctx)
                parts.append(f".{field_name} = {value}")
        # ZEP-0006: fill in declared defaults for any field the literal didn't
        # provide. C99 still zero-fills the rest, so fields with no default
        # keep their previous behaviour.
        for field_name, default_expr in struct.defaults.items():
            if field_name not in provided:
                value = transform_expr(default_expr, ctx)
                parts.append(f".{field_name} = {value}")
        if not parts:
            return f"({typ}){{0}}"
        return f"({typ}){{ " + ", ".join(parts) + " }"

    return pattern.sub(repl, expr)


def transform_deref(expr: str, ctx: TranspileContext) -> str:
    # Replace unary pointer dereference `*p`, but do not touch multiplication
    # such as `a * b` or `p.x * p.y`.
    pattern = re.compile(r"\*\s*([A-Za-z_]\w*)")
    out: List[str] = []
    last = 0
    for m in pattern.finditer(expr):
        start = m.start()
        # Find previous non-space char. Unary deref is allowed at expression start
        # or after an operator/open delimiter.
        j = start - 1
        while j >= 0 and expr[j].isspace():
            j -= 1
        prev = expr[j] if j >= 0 else ""
        if prev and (prev.isalnum() or prev in "_.)]"):
            continue
        name = m.group(1)
        target = ctx.ptr_targets.get(name)
        repl = f"(*({c_type(target)}*)zl_ptr_checked_addr({name}, \"{name}\"))" if target else f"(*{name})"
        out.append(expr[last:start])
        out.append(repl)
        last = m.end()
    if last == 0:
        return expr
    out.append(expr[last:])
    return "".join(out)


def ptrstruct_inner_type(ztype: str) -> Optional[str]:
    m = re.match(r"ptrstruct\s*<\s*([A-Za-z_]\w*)\s*>", ztype.strip())
    if m:
        return m.group(1)
    return None


def list_receiver_c(receiver: str, ctx: TranspileContext) -> str:
    recv = receiver.strip()
    if re.match(r"^[A-Za-z_]\w*$", recv) and recv in ctx.list_refs:
        return recv
    return "&" + transform_field_access(recv, ctx)


def wrap_arg_for_expected(arg: str, expected_type: str, ctx: TranspileContext) -> str:
    expected = expected_type.replace(" ", "")
    expected_base = ztype_base(expected)
    actual = infer_type(arg, ctx)
    base = ztype_base(actual)

    if expected_base == "List":
        # Internal transformed calls may already pass a List by address, e.g.
        # `zlmod_text_join_lines(&xs)` can be transformed again while nested
        # inside another expression. Treat `&list_value` as already wrapped.
        if arg.strip().startswith("&"):
            inner = arg.strip()[1:].strip()
            if ztype_base(infer_type(inner, ctx)) == "List":
                return arg.strip()
        if base != "List":
            raise ZyenError(f"expected List argument, got `{actual}`")
        return list_receiver_c(arg, ctx)

    if expected != "Any":
        return transform_expr(arg, ctx)

    transformed = transform_expr(arg, ctx)
    if base == "Any":
        return transformed
    if base == "float":
        return f"zl_any_float({transformed})"
    if base == "bool":
        return f"zl_any_bool({transformed})"
    if base == "str":
        return f"zl_any_str({transformed})"
    if base == "ptr":
        return f"zl_any_ptr({transformed})"
    if base == "List":
        return f"zl_any_list({list_receiver_c(arg, ctx)})"
    return f"zl_any_int({transformed})"



def complete_args_with_defaults(args: List[str], fn: FunctionDef, call_name: str) -> List[str]:
    param_items = list(fn.params.items())
    required = len([p for p, _t in param_items if p not in fn.defaults])
    total = len(param_items)
    if len(args) < required or len(args) > total:
        if required == total:
            raise ZyenError(f"function `{call_name}` expects {total} args, got {len(args)}")
        raise ZyenError(f"function `{call_name}` expects {required}..{total} args, got {len(args)}")
    completed = list(args)
    for pname, _ptype in param_items[len(args):]:
        if pname not in fn.defaults:
            raise ZyenError(f"function `{call_name}` missing required argument `{pname}`")
        completed.append(fn.defaults[pname])
    return completed

def find_matching_paren(text: str, open_i: int) -> int:
    depth = 0
    in_str = False
    escaped = False
    for i in range(open_i, len(text)):
        ch = text[i]
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return -1


def receiver_start(text: str, dot_i: int) -> int:
    j = dot_i - 1
    while j >= 0 and (text[j].isalnum() or text[j] in "_."):
        j -= 1
    return j + 1


def split_method_call_at(text: str, i: int) -> Optional[Tuple[int, int, str, str, str]]:
    """Return (start,end,receiver,method,args_body) for method call at/after i."""
    m = re.search(r"\.([A-Za-z_]\w*)\s*\(", text[i:])
    if not m:
        return None
    dot = i + m.start()
    method = m.group(1)
    open_i = i + m.end() - 1
    start = receiver_start(text, dot)
    if start == dot:
        return None
    receiver = text[start:dot].strip()
    # skip module function calls already rewritten to zlmod_x; handle only real receiver expressions
    if not receiver:
        return None
    close_i = find_matching_paren(text, open_i)
    if close_i < 0:
        return None
    return start, close_i + 1, receiver, method, text[open_i + 1:close_i]

def transform_method_calls(expr: str, ctx: TranspileContext) -> str:
    """Convert method calls with balanced nested arguments.

    The old regex used `[^()]*`, so calls like
        obj.method((str)xs.get(0))
    were not transformed. This scanner handles nested parentheses and receivers
    such as `this.lines.append(x)`.
    """
    i = 0
    out: List[str] = []
    changed = False
    while i < len(expr):
        found = split_method_call_at(expr, i)
        if not found:
            out.append(expr[i:])
            break
        start, end, obj, method, body = found
        out.append(expr[i:start])
        obj_type = infer_type(obj, ctx)
        args = split_args(body)
        repl: Optional[str] = None

        if obj_type == "List":
            recv = list_receiver_c(obj, ctx)
            if method == "append":
                if len(args) != 1:
                    raise ZyenError(f"List.append expects 1 arg, got {len(args)}")
                repl = f"zl_list_append({recv}, {wrap_arg_for_expected(args[0], 'Any', ctx)})"
            elif method == "len":
                if len(args) != 0:
                    raise ZyenError(f"List.len expects 0 args, got {len(args)}")
                repl = f"zl_list_len({recv})"
            elif method == "is_empty":
                if len(args) != 0:
                    raise ZyenError(f"List.is_empty expects 0 args, got {len(args)}")
                repl = f"zl_list_is_empty({recv})"
            elif method == "get":
                if len(args) != 1:
                    raise ZyenError(f"List.get expects 1 arg, got {len(args)}")
                repl = f"zl_list_get({recv}, {transform_expr(args[0], ctx)})"
            elif method == "ptr":
                if len(args) != 1:
                    raise ZyenError(f"List.ptr expects 1 arg, got {len(args)}")
                repl = f"zl_list_ptr({recv}, {transform_expr(args[0], ctx)})"
            elif method == "append_ptr":
                if len(args) != 1:
                    raise ZyenError(f"List.append_ptr expects 1 arg, got {len(args)}")
                repl = f"zl_list_append_ptr({recv}, {wrap_arg_for_expected(args[0], 'Any', ctx)})"
            elif method == "set":
                if len(args) != 2:
                    raise ZyenError(f"List.set expects 2 args, got {len(args)}")
                repl = f"zl_list_set({recv}, {transform_expr(args[0], ctx)}, {wrap_arg_for_expected(args[1], 'Any', ctx)})"
            elif method == "pop":
                if len(args) != 0:
                    raise ZyenError(f"List.pop expects 0 args, got {len(args)}")
                repl = f"zl_list_pop({recv})"
            elif method == "clear":
                if len(args) != 0:
                    raise ZyenError(f"List.clear expects 0 args, got {len(args)}")
                repl = f"zl_list_clear({recv})"
            else:
                raise ZyenError(f"unknown method `{method}` for List")

        elif obj_type in ctx.structs:
            struct = ctx.structs[obj_type]
            if method not in struct.methods:
                raise ZyenError(f"unknown method `{method}` for struct `{obj_type}`")
            fn = struct.methods[method]
            args = complete_args_with_defaults(args, fn, f"{obj_type}.{method}")
            converted = [wrap_arg_for_expected(arg, param_type, ctx) for arg, (_param_name, param_type) in zip(args, fn.params.items())]
            rest = ", " + ", ".join(converted) if converted else ""
            repl = f"{obj_type}_{method}(&{transform_field_access(obj, ctx)}{rest})"

        else:
            owner = ptrstruct_inner_type(obj_type) if obj_type else None
            if owner in ctx.structs:
                struct = ctx.structs[owner]
                if method not in struct.methods:
                    raise ZyenError(f"unknown method `{method}` for struct `{owner}`")
                fn = struct.methods[method]
                args = complete_args_with_defaults(args, fn, f"{owner}.{method}")
                converted = [wrap_arg_for_expected(arg, param_type, ctx) for arg, (_param_name, param_type) in zip(args, fn.params.items())]
                rest = ", " + ", ".join(converted) if converted else ""
                repl = f"{owner}_{method}({transform_field_access(obj, ctx)}{rest})"

        if repl is None:
            # Not a Zyen object method; preserve original text and continue after it.
            out.append(expr[start:end])
        else:
            out.append(repl)
            changed = True
        i = end
    new_expr = "".join(out)
    # A replacement can expose another method call inside an argument; iterate a
    # few times but avoid infinite loops.
    if changed and new_expr != expr and "." in new_expr:
        for _ in range(3):
            again = transform_method_calls(new_expr, ctx)
            if again == new_expr:
                break
            new_expr = again
    return new_expr


def transform_function_calls(expr: str, ctx: TranspileContext) -> str:
    """Convert direct Zyen function calls so List params pass by reference.

    Most calls compile unchanged, but `fn add(xs: List, v: str)` must receive a
    `ZL_List*`, otherwise mutation happens on a local copy.
    """
    i = 0
    out: List[str] = []
    changed = False
    mask = string_mask(expr)
    while i < len(expr):
        m = re.search(r"\b([A-Za-z_]\w*)\s*\(", expr[i:])
        if not m:
            out.append(expr[i:])
            break
        name_start = i + m.start()
        name = m.group(1)
        open_i = i + m.end() - 1
        if name_start < len(mask) and mask[name_start]:
            out.append(expr[i:open_i + 1])
            i = open_i + 1
            continue
        prev = expr[name_start - 1] if name_start > 0 else ""
        if prev == "." or name not in ctx.functions:
            out.append(expr[i:open_i + 1])
            i = open_i + 1
            continue
        close_i = find_matching_paren(expr, open_i)
        if close_i < 0:
            out.append(expr[i:])
            break
        args = split_args(expr[open_i + 1:close_i])
        fn = ctx.functions[name]
        args = complete_args_with_defaults(args, fn, name)
        converted = [wrap_arg_for_expected(arg, ptype, ctx) for arg, (_pname, ptype) in zip(args, fn.params.items())]
        out.append(expr[i:name_start])
        out.append(f"{name}(" + ", ".join(converted) + ")")
        changed = True
        i = close_i + 1
    return "".join(out) if changed else expr


def is_cast_expr(expr: str) -> Optional[Tuple[str, str]]:
    """Return (target_type, inner_expr) for a whole-expression C-like cast.

    Zyen cast syntax intentionally follows C style:
        (str)i
        (int)text
        (float)x
        (bool)value

    We only treat it as a cast when the entire expression starts with a known
    type cast. Normal parentheses such as `(a + b)` are handled elsewhere.
    """
    m = re.match(r"^\(\s*(int|float|bool|str|ptr|char)\s*\)\s*(.+)$", expr.strip())
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def c_cast_expr(target_type: str, inner: str, ctx: TranspileContext) -> str:
    source_type = infer_type(inner, ctx)
    source_base = ztype_base(source_type)
    inner_c = transform_expr(inner, ctx)

    if target_type == "Any":
        return wrap_arg_for_expected(inner, "Any", ctx)

    if target_type == source_base:
        return inner_c

    if target_type == "str":
        if source_base == "int":
            return f"zl_cast_str_int({inner_c})"
        if source_base == "float":
            return f"zl_cast_str_float({inner_c})"
        if source_base == "bool":
            return f"zl_cast_str_bool({inner_c})"
        if source_base == "Any":
            return f"zl_cast_str_any({inner_c})"
        if source_base == "ptr":
            return f"zl_cast_str_ptr({inner_c})"
        if source_base == "List":
            return f"zl_cast_str_list(&{inner_c})"
        return inner_c

    if target_type == "int":
        if source_base == "str":
            return f"zl_cast_int_str({inner_c})"
        if source_base == "float":
            return f"((int){inner_c})"
        if source_base == "bool":
            return f"(({inner_c}) ? 1 : 0)"
        if source_base == "Any":
            return f"zl_cast_int_any({inner_c})"
        return f"((int){inner_c})"

    if target_type == "float":
        if source_base == "str":
            return f"zl_cast_float_str({inner_c})"
        if source_base == "bool":
            return f"(({inner_c}) ? 1.0 : 0.0)"
        if source_base == "Any":
            return f"zl_cast_float_any({inner_c})"
        return f"((double){inner_c})"

    if target_type == "bool":
        if source_base == "str":
            return f"zl_cast_bool_str({inner_c})"
        if source_base == "Any":
            return f"zl_cast_bool_any({inner_c})"
        if source_base == "ptr":
            return f"({inner_c}.addr != NULL)"
        return f"(({inner_c}) != 0)"

    if target_type == "char":
        return f"zl_char_to_str({transform_expr(inner, ctx)})"

    if target_type == "ptr":
        if source_base == "Any":
            return f"zl_cast_ptr_any({inner_c})"
        if source_base == "ptr":
            return inner_c
        return 'zl_none_ptr("void")'

    return inner_c

def transform_field_access(expr: str, ctx: TranspileContext) -> str:
    # Convert method-body field access on `this`: this.x -> this->x
    pattern = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b(?!\s*\()")

    def repl(m: re.Match[str]) -> str:
        name, field_name = m.group(1), m.group(2)
        stype = ctx.symbols.get(name)
        owner = ptrstruct_inner_type(stype) if stype else None
        if owner and owner in ctx.structs and field_name in ctx.structs[owner].fields:
            return f"{name}->{field_name}"
        return m.group(0)

    return pattern.sub(repl, expr)

def transform_ptr_index(expr: str, ctx: TranspileContext) -> str:
    """Convert Zyen pointer indexing into C pointer indexing.

    Zyen user code should stay clean:
        p[index]
        set p[index] = value;

    The generated C may still use casts, but those casts should stay inside the
    backend, not inside `.zy` standard-library code.
    """
    pattern = re.compile(r"\b([A-Za-z_]\w*)\s*\[\s*([^\[\]]+)\s*\]")

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        index = m.group(2).strip()
        target = ctx.ptr_targets.get(name)
        if not target:
            return m.group(0)
        return f"(({c_type(target)}*)zl_ptr_checked_addr({name}, \"{name}\"))[{transform_expr(index, ctx)}]"

    return pattern.sub(repl, expr)





def has_top_level_comparison(expr: str) -> bool:
    depth = 0
    in_str = False
    escaped = False
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == '"' and not escaped:
            in_str = not in_str
        elif not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0:
                two = expr[i:i + 2]
                if two in {"==", "!=", "<=", ">=", "&&", "||"}:
                    return True
                if ch in {"<", ">"}:
                    return True
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
        i += 1
    return False


def find_top_level_int_op(expr: str) -> Optional[Tuple[str, str, str]]:
    # Use forward-scan string_mask so escape sequences like `\"` are detected
    # correctly. The earlier reverse-tracking of in_str/escaped misread
    # literals such as `"start \"x\" /K /D \""` as having out-of-string `/`s.
    in_s = string_mask(expr)
    def scan(ops: Set[str]) -> Optional[Tuple[int, str]]:
        depth = 0
        for i in range(len(expr) - 1, -1, -1):
            if in_s[i]:
                continue
            ch = expr[i]
            if ch in ")]}":
                depth += 1
            elif ch in "([{}":
                depth -= 1
            elif depth == 0 and ch in ops:
                prev = expr[i - 1] if i > 0 else ""
                nxt = expr[i + 1] if i + 1 < len(expr) else ""
                if ch == "+" and (prev == "+" or nxt == "+" or nxt == "=" or prev == "="):
                    continue
                if ch == "-" and (prev == "-" or nxt == "-" or nxt == "=" or prev == "" or prev in "+-*/(<>=!,&|"):
                    continue
                if ch == "*" and (prev == "*" or nxt == "=" or prev == "="):
                    continue
                if ch == "/" and (nxt == "=" or prev == "="):
                    continue
                return i, ch
        return None
    found = scan({"+", "-"}) or scan({"*", "/"})
    if not found:
        return None
    i, op = found
    left = expr[:i].strip()
    right = expr[i + 1:].strip()
    if not left or not right:
        return None
    return left, op, right


def transform_checked_int_arithmetic(expr: str, ctx: TranspileContext) -> str:
    raw = expr.strip()
    if has_top_level_comparison(raw):
        return raw
    found = find_top_level_int_op(raw)
    if not found:
        return raw
    left, op, right = found
    if ztype_base(infer_type(left, ctx)) != "int" or ztype_base(infer_type(right, ctx)) != "int":
        return raw
    left_c = transform_expr(left, ctx)
    right_c = transform_expr(right, ctx)
    fn = {"+": "zl_int_add", "-": "zl_int_sub", "*": "zl_int_mul", "/": "zl_int_div"}[op]
    return f"{fn}({left_c}, {right_c})"


def string_mask(text: str) -> List[bool]:
    """Return True for indexes that are inside a normal string literal.

    This left-to-right scan avoids the reverse-scan escape bug that misread
    expressions such as `out + "\\n"`.
    """
    mask = [False] * len(text)
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        mask[i] = in_str
        if ch == '"' and not escaped:
            in_str = not in_str
            mask[i] = True
        escaped = ch == "\\" and not escaped
        if ch != "\\":
            escaped = False
    return mask


def _split_top_level_arith(expr: str) -> Optional[List[str]]:
    """Split `expr` on top-level `+ - * /` outside strings/parens.
    Returns None if no top-level operator is found. Skips signs like a
    leading `-x` or `+x`. Does not split on `+=`, `-=`, `*=`, `/=`, `+ +`, etc.
    """
    in_s = string_mask(expr)
    depth = 0
    pieces: List[str] = []
    start = 0
    i = 0
    n = len(expr)
    while i < n:
        if in_s[i]:
            i += 1
            continue
        ch = expr[i]
        if ch in "([{":
            depth += 1
            i += 1
            continue
        if ch in ")]}":
            depth -= 1
            i += 1
            continue
        if depth == 0 and ch in "+-*/":
            prev = expr[i - 1] if i > 0 else ""
            nxt = expr[i + 1] if i + 1 < n else ""
            # Skip compound operators (+=, -=, *=, /=, ++ , -- , /*, */, //).
            if nxt == "=" or nxt == ch or prev == ch or prev == "/" or nxt == "/":
                i += 1
                continue
            # Skip unary sign at the start of the expression or right after another operator.
            if ch in "+-":
                # find previous non-space char excluding our own pos
                j = i - 1
                while j >= 0 and expr[j] == " ":
                    j -= 1
                if j < 0 or expr[j] in "+-*/(,":
                    i += 1
                    continue
            piece = expr[start:i].strip()
            if piece:
                pieces.append(piece)
            start = i + 1
            i += 1
            continue
        i += 1
    if not pieces:
        return None
    tail = expr[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def find_top_level_plus(expr: str) -> Optional[Tuple[str, str]]:
    """Find the last top-level `+` suitable for string concatenation."""
    in_s = string_mask(expr)
    depth = 0
    positions: List[int] = []
    for i, ch in enumerate(expr):
        if in_s[i]:
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == "+":
            prev = expr[i - 1] if i > 0 else ""
            nxt = expr[i + 1] if i + 1 < len(expr) else ""
            if prev == "+" or nxt == "+" or prev == "=" or nxt == "=":
                continue
            positions.append(i)
    if not positions:
        return None
    i = positions[-1]
    left = expr[:i].strip()
    right = expr[i + 1:].strip()
    if left and right:
        return left, right
    return None


def transform_string_concat(expr: str, ctx: TranspileContext) -> str:
    """Convert Zyen string concatenation into runtime string join.

    Supported syntax:
        "my age is " + (str)this.age
        "x=" + (str)x + ", y=" + (str)y

    Only expressions where at least one top-level `+` side is `str` are treated
    as string concatenation. Plain int/float addition remains numeric.
    """
    raw = expr.strip()
    if has_top_level_comparison(raw):
        return raw
    found = find_top_level_plus(raw)
    if not found:
        return raw
    left, right = found
    left_type = ztype_base(infer_type(left, ctx))
    right_type = ztype_base(infer_type(right, ctx))
    if left_type != "str" and right_type != "str":
        return raw
    left_c = c_cast_expr("str", left, ctx)
    right_c = c_cast_expr("str", right, ctx)
    return f"zl_str_join(2, {left_c}, {right_c})"

def is_fstring_expr(expr: str) -> bool:
    return re.match(r'^f".*"$', expr.strip()) is not None


def parse_fstring_parts(expr: str) -> List[Tuple[str, str]]:
    """Return parts as (kind, text), where kind is 'lit' or 'expr'.

    Supported syntax is intentionally small and readable:
        f"rpm={rpm} ok={ok}"

    Escapes:
        {{ -> literal {
        }} -> literal }
    """
    text = expr.strip()
    if not is_fstring_expr(text):
        raise ZyenError("internal error: expected f-string")
    body = text[2:-1]
    parts: List[Tuple[str, str]] = []
    lit: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "{" and i + 1 < len(body) and body[i + 1] == "{":
            lit.append("{")
            i += 2
            continue
        if ch == "}" and i + 1 < len(body) and body[i + 1] == "}":
            lit.append("}")
            i += 2
            continue
        if ch == "{":
            if lit:
                parts.append(("lit", "".join(lit)))
                lit = []
            depth = 1
            j = i + 1
            in_str = False
            escaped = False
            expr_buf: List[str] = []
            while j < len(body):
                cj = body[j]
                if cj == '"' and not escaped:
                    in_str = not in_str
                if not in_str:
                    if cj == "{":
                        depth += 1
                    elif cj == "}":
                        depth -= 1
                        if depth == 0:
                            break
                expr_buf.append(cj)
                escaped = cj == "\\" and not escaped
                if cj != "\\":
                    escaped = False
                j += 1
            if depth != 0:
                raise ZyenError("unterminated f-string expression")
            inner = "".join(expr_buf).strip()
            if not inner:
                raise ZyenError("empty f-string expression")
            parts.append(("expr", inner))
            i = j + 1
            continue
        if ch == "}":
            raise ZyenError("single `}` in f-string; use `}}` for a literal brace")
        lit.append(ch)
        i += 1
    if lit:
        parts.append(("lit", "".join(lit)))
    return parts


def c_string_literal(text: str) -> str:
    return json.dumps(text)


def transform_fstring(expr: str, ctx: TranspileContext) -> str:
    parts = parse_fstring_parts(expr)
    if not parts:
        return '""'
    c_parts: List[str] = []
    for kind, text in parts:
        if kind == "lit":
            if text:
                c_parts.append(c_string_literal(text))
        else:
            c_parts.append(c_cast_expr("str", text, ctx))
    if not c_parts:
        return '""'
    if len(c_parts) == 1:
        return c_parts[0]
    return f"zl_str_join({len(c_parts)}, " + ", ".join(c_parts) + ")"


def replace_fstrings_in_expr(expr: str, ctx: TranspileContext) -> str:
    """Replace f"..." tokens outside ordinary strings.

    v0.1.41 treated any `f"` substring as an f-string starter, even inside
    normal strings, so `string.eq(x, "f")` failed. This scanner tracks normal
    string state first.
    """
    out: List[str] = []
    i = 0
    changed = False
    in_str = False
    escaped = False
    while i < len(expr):
        ch = expr[i]
        if in_str:
            out.append(ch)
            if ch == '"' and not escaped:
                in_str = False
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            escaped = False
            out.append(ch)
            i += 1
            continue
        if ch == "f" and i + 1 < len(expr) and expr[i + 1] == '"':
            if i > 0 and (expr[i - 1].isalnum() or expr[i - 1] == "_"):
                out.append(ch)
                i += 1
                continue
            j = i + 2
            esc = False
            while j < len(expr):
                cj = expr[j]
                if cj == '"' and not esc:
                    break
                esc = cj == "\\" and not esc
                if cj != "\\":
                    esc = False
                j += 1
            if j >= len(expr):
                raise ZyenError("unterminated f-string")
            out.append(transform_fstring(expr[i:j + 1], ctx))
            changed = True
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out) if changed else expr

def transform_expr(expr: str, ctx: TranspileContext) -> str:
    expr = expr.strip()
    if is_fstring_expr(expr):
        return transform_fstring(expr, ctx)
    cast = is_cast_expr(expr)
    if cast:
        return c_cast_expr(cast[0], cast[1], ctx)
    expr = convert_struct_literal(expr, ctx)
    expr = replace_fstrings_in_expr(expr, ctx)
    # String concat must run before object/method rewriting, so expressions like
    # `"x=" + (str)list.get(0)` still infer the right side as str.
    expr = transform_string_concat(expr, ctx)
    expr = transform_function_calls(expr, ctx)
    expr = transform_method_calls(expr, ctx)
    expr = transform_field_access(expr, ctx)
    expr = transform_ptr_index(expr, ctx)
    expr = transform_deref(expr, ctx)
    expr = transform_checked_int_arithmetic(expr, ctx)
    return expr


def is_array_literal(expr: str) -> bool:
    return expr.strip().startswith("[") and expr.strip().endswith("]")


def parse_array_literal(expr: str, ctx: TranspileContext, line_no: int) -> Tuple[List[str], str]:
    """Parse `[1, 2, "hi"]` as a List literal.

    List is heterogeneous: each element is stored in an internal dynamic cell.
    `Any` is not a user-facing type; it is only the runtime representation.
    """
    text = expr.strip()
    if not is_array_literal(text):
        raise ZyenError(f"line {line_no}: internal error: expected list literal")
    body = text[1:-1].strip()
    values = split_args(body) if body else []
    return values, "Any"


def infer_type(expr: str, ctx: TranspileContext) -> str:
    expr = expr.strip()
    raw = expr
    if is_fstring_expr(raw):
        return "str"
    # Cast expression: (str)i, (int)s, (float)x, (bool)v.
    cast = is_cast_expr(raw)
    if cast:
        return "str" if cast[0] == "char" else cast[0]
    # Remove outer parentheses for simple cases.
    while raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    if is_array_literal(raw):
        return "List"
    sm_lit = re.match(r"^(?:[A-Za-z_]\w*\.)?([A-Z][A-Za-z_]\w*)\s*\{", raw)
    if sm_lit and sm_lit.group(1) in ctx.structs:
        return sm_lit.group(1)
    if ptr_constructor_expr(raw) is not None:
        return "ptr"
    if raw == "List":
        return "List"
    if is_none_literal(raw):
        return "none"
    if re.match(r'^".*"$', raw):
        return "str"
    plus = find_top_level_plus(raw)
    if plus:
        left, right = plus
        if ztype_base(infer_type(left, ctx)) == "str" or ztype_base(infer_type(right, ctx)) == "str":
            return "str"
    if raw in {"true", "false"}:
        return "bool"
    if re.match(r"^-?\d+$", raw):
        return "int"
    if re.match(r"^-?\d+\.\d*([eE][+-]?\d+)?$", raw) or re.match(r"^-?\d+[eE][+-]?\d+$", raw):
        return "float"
    if raw.startswith("&"):
        return "ptr"
    dm = re.match(r"^\*\s*([A-Za-z_]\w*)$", raw)
    if dm:
        return ctx.ptr_targets.get(dm.group(1), "int")
    im = re.match(r"^([A-Za-z_]\w*)\s*\[.*\]$", raw)
    if im and im.group(1) in ctx.ptr_targets:
        return ctx.ptr_targets.get(im.group(1), "int")
    if raw.endswith(".addr"):
        return "ptraddr"
    found_mcall = split_method_call_at(raw, 0)
    if found_mcall and found_mcall[0] == 0 and found_mcall[1] == len(raw):
        _start, _end, obj, method, _body = found_mcall
        obj_type = infer_type(obj, ctx)
        if obj_type == "List":
            return {
                "append": "void",
                "set": "void",
                "clear": "void",
                "len": "int",
                "is_empty": "bool",
                "get": "Any",
                "ptr": "ptr",
                "append_ptr": "ptr",
                "pop": "Any",
            }.get(method, "int")
        if obj_type in ctx.structs and method in ctx.structs[obj_type].methods:
            return ctx.structs[obj_type].methods[method].ret_type
        owner = ptrstruct_inner_type(obj_type) if obj_type else None
        if owner in ctx.structs and method in ctx.structs[owner].methods:
            return ctx.structs[owner].methods[method].ret_type
    fm = re.match(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$", raw)
    if fm:
        var, field = fm.group(1), fm.group(2)
        stype = ctx.symbols.get(var)
        owner = ptrstruct_inner_type(stype) if stype else None
        if stype in ctx.structs and field in ctx.structs[stype].fields:
            return ctx.structs[stype].fields[field]
        if owner in ctx.structs and field in ctx.structs[owner].fields:
            return ctx.structs[owner].fields[field]
    call = re.match(r"^([A-Za-z_]\w*)\s*\(.*\)$", raw)
    if call and call.group(1) in ctx.functions:
        return ctx.functions[call.group(1)].ret_type
    if raw in ctx.symbols:
        return ctx.symbols[raw]
    if any(op in raw for op in ["==", "!=", "<=", ">=", "<", ">", "&&", "||"]):
        return "bool"
    # Try top-level arithmetic before falling back to the dot heuristic:
    # if every operand of `+ - * /` independently infers to int, the result
    # is int. This avoids mis-flagging `xs.len() - 1` as float.
    arith_pieces = _split_top_level_arith(raw)
    if arith_pieces is not None and len(arith_pieces) >= 2:
        operand_types = [infer_type(piece, ctx) for piece in arith_pieces]
        if all(t == "int" for t in operand_types):
            return "int"
        if any(t == "float" for t in operand_types) and all(t in {"int", "float"} for t in operand_types):
            return "float"
    if "." in raw:
        return "float"
    for name, typ in ctx.symbols.items():
        if re.search(rf"\b{re.escape(name)}\b", raw) and typ == "float":
            return "float"
    return "int"


def c_print(expr: str, ctx: TranspileContext) -> str:
    raw_expr = expr.strip()
    typ = infer_type(raw_expr, ctx)
    if typ == "void":
        raise ZyenError("cannot print a void value")
    if is_none_literal(raw_expr):
        return 'printf("None\\n");'
    dm = re.match(r"^\*\s*([A-Za-z_]\w*)$", raw_expr)
    if dm:
        name = dm.group(1)
        if name in ctx.ptr_targets:
            cexpr = transform_expr(raw_expr, ctx)
            base = ztype_base(ctx.ptr_targets[name])
            if base == "Any":
                return f'if (!zl_ptr_is_valid({name})) {{ zl_ptr_checked_addr({name}, "{name}"); }} else zl_print_any({cexpr});'
            if base == "List":
                list_arg = cexpr if re.match(r"^[A-Za-z_]\w*$", raw_expr) and raw_expr in ctx.list_refs else "&" + cexpr
                return f'zl_print_list({list_arg});'
            fmt = "%g" if base == "float" else "%s" if base == "str" else "%s" if base == "bool" else "%d"
            if base == "str":
                value_expr = cexpr
            elif base == "bool":
                value_expr = f'({cexpr}) ? "true" : "false"'
            else:
                value_expr = cexpr
            return f'if (!zl_ptr_is_valid({name})) {{ zl_ptr_checked_addr({name}, "{name}"); }} else printf("{fmt}\\n", {value_expr});'
    cexpr = transform_expr(raw_expr, ctx)
    base = ztype_base(typ)
    if base == "Any":
        return f"zl_print_any({cexpr});"
    if base == "str":
        return f'printf("%s\\n", {cexpr});'
    if base == "float":
        return f'printf("%g\\n", {cexpr});'
    if base == "bool":
        return f'printf("%s\\n", ({cexpr}) ? "true" : "false");'
    if base == "ptr":
        return f'if ({cexpr}.addr == NULL) printf("None\\n"); else if (!zl_ptr_is_valid({cexpr})) printf("Freed\\n"); else printf("%p\\n", (void*){cexpr}.addr);'
    if base == "List":
        list_arg = cexpr if re.match(r"^[A-Za-z_]\w*$", raw_expr) and raw_expr in ctx.list_refs else "&" + cexpr
        return f'zl_print_list({list_arg});'
    if base == "ptraddr":
        return f'printf("%p\\n", (void*){cexpr});'
    return f'printf("%d\\n", {cexpr});'



def declare_symbol(ctx: TranspileContext, name: str, typ: str, line_no: int, ptr_target: Optional[str] = None, is_const: bool = False, is_list_ref: bool = False) -> None:
    if not ctx.scope_stack:
        ctx.scope_stack = [set()]
    current = ctx.scope_stack[-1]
    if name in current:
        raise ZyenError(f"line {line_no}: variable `{name}` is already declared in this scope; use a new name or `set {name} = ...;`")
    current.add(name)
    ctx.symbols[name] = typ
    if ptr_target:
        ctx.ptr_targets[name] = ptr_target
    if is_const:
        ctx.consts.add(name)
    if is_list_ref:
        ctx.list_refs.add(name)


def push_scope(ctx: TranspileContext) -> None:
    ctx.scope_stack.append(set())


def pop_scope(ctx: TranspileContext) -> None:
    if not ctx.scope_stack:
        return
    names = ctx.scope_stack.pop()
    for name in names:
        ctx.symbols.pop(name, None)
        ctx.ptr_targets.pop(name, None)
        ctx.consts.discard(name)
        ctx.list_refs.discard(name)

def parse_owned_ptr_decl(line: str, ctx: TranspileContext, line_no: int, trailing_semicolon: bool = True) -> Optional[str]:
    """Parse pointer declarations with dereference initialization.

    Zyen syntax:
        let *a: ptr<int> = 1;
        set *a = 2;

    Meaning: create pointer `a`, allocate hidden local storage for one int,
    point `a` at that location, then write 1 into `*a`. Conceptually the
    pointer is still a location; the hidden storage only exists so `*a` has a
    valid place to write.

    If declared without an initializer:
        let *a: ptr<int>;
    then `a` is None, identical to `let a: ptr<int>;`.
    """
    ending = ";" if trailing_semicolon else ""

    none_pattern = r"(let|const)\s+\*\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?)"
    if trailing_semicolon:
        none_pattern += r";\s*$"
    else:
        none_pattern += r"\s*$"
    nm = re.match(none_pattern, line)
    if nm:
        kind, name, explicit_type = nm.group(1), nm.group(2), nm.group(3).replace(" ", "")
        if explicit_type.startswith("prt"):
            raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")
        validate_user_type(explicit_type, line_no, "ptr declaration")
        if ztype_base(explicit_type) != "ptr":
            raise ZyenError(f"line {line_no}: pointer declaration must use `ptr<T>`, for example `let *a: ptr<int>;`")
        target_type = ptr_inner_type(explicit_type)
        if not target_type:
            raise ZyenError(f"line {line_no}: bare `ptr` declarations are not allowed; use `ptr<int>`, `ptr<str>`, `ptr<bool>`, etc.")
        declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type, is_const=(kind == "const"))
        prefix = "const " if kind == "const" else ""
        return f'{prefix}ptr {name} = zl_none_ptr("{type_name_for_runtime(target_type)}"){ending}'

    pattern = r"(let|const)\s+\*\s*([A-Za-z_]\w*)\s*(?::\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?))?\s*=\s*(.*)"
    if trailing_semicolon:
        pattern += r";\s*$"
    else:
        pattern += r"\s*$"
    m = re.match(pattern, line)
    if not m:
        return None

    kind, name, explicit_type, expr = m.group(1), m.group(2), m.group(3), m.group(4).strip()
    explicit_type = explicit_type.replace(" ", "") if explicit_type else None
    if is_none_literal(expr):
        raise ZyenError(f"line {line_no}: `let *{name} ... = None;` has no location to write; use `let {name}: ptr<T>;` or `let {name}: ptr<T> = None;`")

    if explicit_type and explicit_type.startswith("prt"):
        raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")

    if explicit_type:
        if ztype_base(explicit_type) != "ptr":
            raise ZyenError(f"line {line_no}: owned pointer declaration must use `ptr` or `ptr<T>`, for example `let *a: ptr = 1;` or `let *a: ptr<int> = 1;`")
        validate_user_type(explicit_type, line_no, "owned ptr")
        target_type = ptr_inner_type(explicit_type)
        # Bare `ptr` with an initializer is allowed, but it is no longer dynamic.
        # The target is inferred from the initializer: `let *a: ptr = 1;` -> ptr<int>.
        if not target_type:
            target_type = infer_type(expr, ctx)
            if ztype_base(target_type) == "Any":
                raise ZyenError(f"line {line_no}: cannot infer a concrete ptr target from dynamic List value; cast first")
    else:
        target_type = infer_type(expr, ctx)

    ensure_assignable(target_type, infer_type(expr, ctx), line_no, f"initializing `*{name}`")
    assert_expr_literals_fit(expr, ctx, line_no, target_type)
    hidden_name = f"__zy_ptr_{name}"
    rhs_c = wrap_arg_for_expected(expr, "Any", ctx) if ztype_base(target_type) == "Any" else transform_expr(expr, ctx)
    storage_c = (
        f"ptr {hidden_name} = zl_mem_alloc_cell(sizeof({c_type(target_type)}), \"{type_name_for_runtime(target_type)}\");\n"
        f"(*({c_type(target_type)}*){hidden_name}.addr) = {rhs_c};"
    )
    expr_c = hidden_name

    declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type, is_const=(kind == "const"))
    prefix = "const " if kind == "const" else ""
    return f"{storage_c}\n{prefix}ptr {name} = {expr_c}{ending}"


def parse_var_decl(line: str, ctx: TranspileContext, line_no: int, trailing_semicolon: bool = True) -> str:
    owned = parse_owned_ptr_decl(line, ctx, line_no, trailing_semicolon=trailing_semicolon)
    if owned is not None:
        return owned
    ending = ";" if trailing_semicolon else ""

    # Pointer variables may be declared first. They default to None.
    # Example: `let a: ptr<int>;` -> ptr a = None
    decl_only = r"(let|const)\s+([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?(?:\s*<\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?\s*>)?)"
    if trailing_semicolon:
        decl_only += r";\s*$"
    else:
        decl_only += r"\s*$"
    dm = re.match(decl_only, line)
    if dm:
        kind, name, explicit_type = dm.group(1), dm.group(2), dm.group(3).replace(" ", "")
        explicit_type = _strip_module_prefix_type(explicit_type)
        if ctx.scope_stack and name in ctx.scope_stack[-1]:
            raise ZyenError(f"line {line_no}: variable `{name}` is already declared in this scope; use a new name or `set {name} = ...;`")
        if kind == "const":
            raise ZyenError(f"line {line_no}: const `{name}` needs an initializer; use `const {name}: {explicit_type} = value;`")
        if explicit_type.startswith("prt"):
            raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")
        validate_user_type(explicit_type, line_no, "declaration")
        base_type = ztype_base(explicit_type)

        if base_type == "ptr":
            target_type = ptr_inner_type(explicit_type)
            if not target_type:
                raise ZyenError(f"line {line_no}: bare `ptr` declarations are not allowed; use `ptr<int>`, `ptr<str>`, `ptr<bool>`, etc.")
            declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type)
            return f'ptr {name} = zl_none_ptr("{type_name_for_runtime(target_type)}"){ending}'

        defaults = {
            "int": "0",
            "float": "0.0",
            "bool": "false",
            "str": '""',
            "List": "zl_list_new()",
        }
        if base_type in defaults:
            declare_symbol(ctx, name, base_type, line_no)
            return f"{c_type(base_type)} {name} = {defaults[base_type]}{ending}"

        if explicit_type in ctx.structs:
            declare_symbol(ctx, name, explicit_type, line_no)
            init = _struct_default_init(explicit_type, ctx)
            return f"{explicit_type} {name} = {init}{ending}"

        raise ZyenError(f"line {line_no}: declaration without value is not supported for `{explicit_type}`")

    pattern = r"(let|const)\s+([A-Za-z_]\w*)\s*(?::\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?(?:\s*<\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?\s*>)?))?\s*=\s*(.*)"
    if trailing_semicolon:
        pattern += r";\s*$"
    else:
        pattern += r"\s*$"
    m = re.match(pattern, line)
    if not m:
        raise ZyenError(f"line {line_no}: invalid declaration, expected `let name = value;` or `let name: type = value;`")
    kind, name, explicit_type, expr = m.group(1), m.group(2), m.group(3), m.group(4).strip()
    explicit_type = explicit_type.replace(" ", "") if explicit_type else None
    if explicit_type:
        explicit_type = _strip_module_prefix_type(explicit_type)
    if ctx.scope_stack and name in ctx.scope_stack[-1]:
        raise ZyenError(f"line {line_no}: variable `{name}` is already declared in this scope; use a new name or `set {name} = ...;`")

    # Pointer constructor value: `let b = ptr;` or `let b = ptr<int>;`
    # Creates a None pointer object. The target type may be inferred later by
    # the first `set *b = value;`.
    ptr_ctor_target = ptr_constructor_expr(expr)
    if ptr_ctor_target is not None:
        if ptr_ctor_target == "__bare_ptr__" and not explicit_type:
            raise ZyenError(f"line {line_no}: bare `ptr` constructor needs a target type; use `ptr<int>` or `let name: ptr<int> = None;`")
        if explicit_type and ztype_base(explicit_type) != "ptr":
            raise ZyenError(f"line {line_no}: `ptr` constructor can only initialize a ptr variable")
        if explicit_type:
            validate_user_type(explicit_type, line_no, "ptr constructor")
        target_type = ptr_inner_type(explicit_type) if explicit_type else ptr_ctor_target
        if target_type == "__bare_ptr__":
            target_type = ptr_inner_type(explicit_type)
        if not target_type:
            raise ZyenError(f"line {line_no}: cannot infer ptr target type; use `ptr<int>` or another concrete ptr<T>`")
        declare_symbol(ctx, name, "ptr", line_no, ptr_target=target_type, is_const=(kind == "const"))
        prefix = "const " if kind == "const" else ""
        return f'{prefix}ptr {name} = zl_none_ptr("{type_name_for_runtime(target_type)}"){ending}'

    if explicit_type and explicit_type.startswith("prt"):
        raise ZyenError(f"line {line_no}: unknown type `{explicit_type}`; did you mean `ptr`?")
    if explicit_type:
        validate_user_type(explicit_type, line_no, "declaration")
    ptr_target = ptr_inner_type(explicit_type) if explicit_type else None
    if explicit_type:
        typ = ztype_base(explicit_type)
    else:
        typ = infer_type(expr, ctx)

    if is_array_literal(expr):
        if explicit_type and ztype_base(explicit_type) != "List":
            raise ZyenError(f"line {line_no}: list literal `[ ... ]` can only initialize List, not `{explicit_type}`")
        values, _elem_type = parse_array_literal(expr, ctx, line_no)
        typ = "List"
        declare_symbol(ctx, name, typ, line_no, is_const=(kind == "const"))
        lines = [f"ZL_List {name} = zl_list_new();"]
        for value in values:
            assert_expr_literals_fit(value, ctx, line_no)
            lines.append(f"zl_list_append(&{name}, {wrap_arg_for_expected(value, 'Any', ctx)});")
        return "\n".join(lines)
    if is_none_literal(expr):
        if typ not in {"ptr", "none"}:
            raise ZyenError(f"line {line_no}: None can only be assigned to ptr variables in v0.1")
        typ = "ptr"
        if not ptr_target:
            raise ZyenError(f"line {line_no}: None ptr needs a concrete type, e.g. `let p: ptr<int> = None;`")
        target_type = ptr_target
        ptr_target = target_type
        expr_c = f'zl_none_ptr("{type_name_for_runtime(target_type)}")'
    elif typ == "ptr" and expr.startswith("&"):
        target_var = expr[1:].strip()
        target_type = ptr_target or ctx.symbols.get(target_var, "void")
        ptr_target = target_type
        expr_c = f'zl_ptr(&{target_var}, "{type_name_for_runtime(target_type)}")'
    elif typ == "List" and expr == "List":
        typ = "List"
        expr_c = "zl_list_new()"
    elif explicit_type and explicit_type in ctx.structs and expr == explicit_type:
        typ = explicit_type
        expr_c = _struct_default_init(explicit_type, ctx)
    else:
        if typ == "void":
            raise ZyenError(f"line {line_no}: cannot assign a void result to variable `{name}`")
        if typ == "Any" and not explicit_type:
            raise ZyenError(f"line {line_no}: dynamic List value cannot be stored without a concrete cast; use `(int)`, `(str)`, `(bool)`, `(float)`, or print it directly")
        if explicit_type:
            ensure_assignable(explicit_type, infer_type(expr, ctx), line_no, f"declaration of `{name}`")
        assert_expr_literals_fit(expr, ctx, line_no, typ)
        expr_c = transform_expr(expr, ctx)

    # If a ptr variable is initialized from an address, another ptr, or List.ptr(),
    # track the target type for later `*p` operations.
    if ztype_base(typ) == "ptr" and not ptr_target:
        if expr.startswith("&"):
            ptr_target = ctx.symbols.get(expr[1:].strip(), "void")
        elif expr in ctx.ptr_targets:
            ptr_target = ctx.ptr_targets.get(expr, "void")
        elif re.match(r"^[A-Za-z_]\w*\.(ptr|append_ptr)\s*\(", expr):
            ptr_target = "Any"  # internal List cell
    declare_symbol(ctx, name, typ, line_no, ptr_target=ptr_target, is_const=(kind == "const"))
    decl_type = c_type(typ)
    # Avoid invalid C like `const const char* z = ...` when Zyen `const`
    # is used with `str`, because `str` already maps to `const char*`.
    # For now `const z` means a read-only Zyen variable tracked by the
    # compiler, while the emitted C keeps the canonical storage type.
    if kind == "const" and decl_type.startswith("const "):
        return f"{decl_type} {name} = {expr_c}{ending}"
    prefix = "const " if kind == "const" else ""
    return f"{prefix}{decl_type} {name} = {expr_c}{ending}"



def ptr_constructor_expr(expr: str) -> Optional[str]:
    """Return inner type for `ptr` constructor expressions.

    Zyen pointer-object syntax:
        let b = ptr;       // b is a None ptr with unknown target type
        let b = ptr<int>;  // b is a None ptr<int>

    `ptr` is allowed as a value constructor here, not only as a type name.
    """
    expr = expr.strip()
    if expr == "ptr":
        return "__bare_ptr__"
    m = re.match(r"^ptr\s*<\s*([A-Za-z_]\w*)\s*>$", expr)
    if m:
        return m.group(1)
    return None


def auto_storage_for_deref(name: str, target_type: str, rhs_c: str, ctx: TranspileContext) -> str:
    """Emit lazy storage for assigning through an empty pointer.

    This implements the intentionally simple Zyen rule:
        let b = ptr;
        set *b = 5;

    If `b` is None at that assignment, Zyen creates one hidden cell for b and
    then writes into it. This keeps the user-facing pointer syntax intuitive
    while the backend still generates ordinary C storage.
    """
    ctx.auto_ptr_counter += 1
    hidden = f"__zy_auto_ptr_{name}_{ctx.auto_ptr_counter}"
    ctyp = c_type(target_type)
    runtime_type = type_name_for_runtime(target_type)
    return (
        f"if (!zl_ptr_is_valid({name})) {{ {name} = zl_mem_alloc_cell(sizeof({ctyp}), \"{runtime_type}\"); }}\n"
        f"(*({ctyp}*)zl_ptr_checked_addr({name}, \"{name}\")) = {rhs_c};"
    )



def c_compound_assignment(lhs: str, op: str, rhs: str, ctx: TranspileContext, line_no: int) -> str:
    """Compile explicit compound assignments such as `set x += 1;`.

    ZyenLang keeps mutation visible. In normal statement position, compound
    assignment must use `set`: `set x += 1;`, `set x -= 1;`, etc. Bare forms
    like `x += 1;` are rejected by transform_statement(). The `for (...)` step
    field remains loop-control syntax and may still use C-like `i++` / `i += 1`.
    """
    lhs = lhs.strip()
    rhs = rhs.strip()
    const_name = re.split(r"[.\[]", lhs, 1)[0].replace("*", "").strip()
    if const_name in ctx.consts:
        raise ZyenError(f"line {line_no}: cannot modify const `{const_name}`")

    lhs_type = infer_type(lhs, ctx)
    rhs_type = infer_type(rhs, ctx)
    base = ztype_base(lhs_type)
    if base == "void":
        raise ZyenError(f"line {line_no}: cannot use compound assignment on void value `{lhs}`")
    if base == "List":
        raise ZyenError(f"line {line_no}: compound assignment is not supported for List")
    if base == "ptr":
        raise ZyenError(f"line {line_no}: compound assignment is not supported for ptr itself; use `set *p = ...` or `set *p += ...`")

    lhs_c = transform_expr(lhs, ctx)

    if base == "str":
        if op != "+=":
            raise ZyenError(f"line {line_no}: only `+=` is supported for str")
        rhs_c = c_cast_expr("str", rhs, ctx)
        return f"{lhs_c} = zl_str_join(2, {lhs_c}, {rhs_c});"

    if base == "int":
        ensure_assignable("int", rhs_type, line_no, f"compound assignment to `{lhs}`")
        assert_expr_literals_fit(rhs, ctx, line_no, "int")
        rhs_c = transform_expr(rhs, ctx)
        fn = {"+=": "zl_int_add", "-=": "zl_int_sub", "*=": "zl_int_mul", "/=": "zl_int_div"}[op]
        return f"{lhs_c} = {fn}({lhs_c}, {rhs_c});"

    if base == "float":
        if ztype_base(rhs_type) not in {"int", "float"}:
            raise ZyenError(f"line {line_no}: float compound assignment needs int or float right side")
        rhs_c = transform_expr(rhs, ctx)
        return f"{lhs_c} {op} {rhs_c};"

    raise ZyenError(f"line {line_no}: compound assignment is not supported for `{lhs_type}`")

def parse_set(line: str, ctx: TranspileContext, line_no: int) -> str:
    cm = re.match(r"set\s+(.+?)\s*(\+=|-=|\*=|/=)\s*(.*);\s*$", line)
    if cm:
        return c_compound_assignment(cm.group(1), cm.group(2), cm.group(3), ctx, line_no)
    m = re.match(r"set\s+(.+?)\s*=\s*(.*);\s*$", line)
    if not m:
        raise ZyenError(f"line {line_no}: invalid assignment, expected `set name = value;` or `set name += value;`")
    lhs, rhs = m.group(1).strip(), m.group(2).strip()
    const_name = re.split(r"[.\[]", lhs, 1)[0].replace("*", "").strip()
    if const_name in ctx.consts:
        raise ZyenError(f"line {line_no}: cannot modify const `{const_name}`")

    # Pointer assignment: None or address assignment.
    if re.match(r"^[A-Za-z_]\w*$", lhs) and ctx.symbols.get(lhs) == "ptr":
        target_type = ctx.ptr_targets.get(lhs, "void")
        if is_none_literal(rhs):
            return f'{lhs} = zl_none_ptr("{type_name_for_runtime(target_type)}");'
        if rhs.startswith("&"):
            target_var = rhs[1:].strip()
            target_type = ctx.symbols.get(target_var, target_type)
            ctx.ptr_targets[lhs] = target_type
            return f'{lhs} = zl_ptr(&{target_var}, "{type_name_for_runtime(target_type)}");'

    # Dereference assignment. If the pointer is None, Zyen lazily creates one
    # hidden cell for it. This makes this intuitive pattern valid:
    #     let b = ptr;
    #     set *b = 5;
    dm = re.match(r"^\*\s*([A-Za-z_]\w*)$", lhs)
    if dm and dm.group(1) in ctx.ptr_targets:
        name = dm.group(1)
        rhs_type = infer_type(rhs, ctx)
        target_type = ctx.ptr_targets.get(name, "void")
        if target_type == "void":
            raise ZyenError(f"line {line_no}: pointer `{name}` has no target type; declare it as `ptr<int>`, `ptr<str>`, etc.")
        ensure_assignable(target_type, rhs_type, line_no, f"assignment to `*{name}`")
        assert_expr_literals_fit(rhs, ctx, line_no, target_type)
        rhs_c = wrap_arg_for_expected(rhs, "Any", ctx) if ztype_base(target_type) == "Any" else transform_expr(rhs, ctx)
        return auto_storage_for_deref(name, target_type, rhs_c, ctx)

    if lhs.startswith("*"):
        name = lhs[1:].strip()
        raise ZyenError(f"line {line_no}: `{name}` is not a ptr; declare it with `let {name} = ptr;` or `let {name}: ptr<T>;`")

    expected = ctx.symbols.get(const_name, "")
    assert_expr_literals_fit(rhs, ctx, line_no, expected)
    return f"{transform_expr(lhs, ctx)} = {transform_expr(rhs, ctx)};"


def parse_for(line: str, ctx: TranspileContext, line_no: int) -> str:
    m = re.match(r"for\s*\((.*)\)\s*\{\s*$", line)
    if not m:
        raise ZyenError(f"line {line_no}: invalid for loop")
    inside = m.group(1).strip()
    if inside == ";;":
        return "for (;;) {"
    parts = inside.split(";")
    if len(parts) != 3:
        raise ZyenError(f"line {line_no}: for loop must look like `for (init; condition; step) {{`")
    init, cond, step = [p.strip() for p in parts]
    if init.startswith("let ") or init.startswith("const "):
        init_c = parse_var_decl(init, ctx, line_no, trailing_semicolon=False)
    else:
        init_c = transform_expr(init, ctx)
    if step.startswith("set "):
        step_c = parse_set(step + ";", ctx, line_no).rstrip(";")
    else:
        step_c = transform_expr(step, ctx)
    return f"for ({init_c}; {transform_expr(cond, ctx)}; {step_c}) {{"


def transform_statement(line: str, ctx: TranspileContext, line_no: int) -> str:
    if line == "pass;":
        return ";"
    if line == "break;":
        if ctx.loop_depth <= 0:
            raise ZyenError(f"line {line_no}: `break;` can only be used inside `for` loops")
        return "break;"
    if line == "continue;":
        if ctx.loop_depth <= 0:
            raise ZyenError(f"line {line_no}: `continue;` can only be used inside `for` loops")
        return "continue;"
    if line.startswith("while"):
        raise ZyenError(f"line {line_no}: `while` is intentionally not part of ZyenLang v0.1; use `for` instead")
    if line.startswith("let ") or line.startswith("const "):
        return parse_var_decl(line, ctx, line_no)
    if line.startswith("set "):
        return parse_set(line, ctx, line_no)
    if line.startswith("return"):
        m = re.match(r"return(?:\s+(.*))?;\s*$", line)
        if not m:
            raise ZyenError(f"line {line_no}: return statement must end with `;`")
        expr = (m.group(1) or "").strip()
        return "return;" if not expr else f"return {transform_expr(expr, ctx)};"
    pm = re.match(r"print\s*\((.*)\)\s*;\s*$", line)
    if pm:
        return c_print(pm.group(1), ctx)
    sm = re.match(r"stop\s+(.+);\s*$", line)
    if sm:
        msg = transform_expr(sm.group(1), ctx)
        return f'fprintf(stderr, "%s\\n", {msg}); exit(1);'
    cm = re.match(r"^\s*(\*?\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[[^\]]+\])?)\s*(\+=|-=|\*=|/=)\s*(.*);\s*$", line)
    if cm:
        raise ZyenError(
            f"line {line_no}: bare compound assignment is not allowed; use `set {cm.group(1).strip()} {cm.group(2)} {cm.group(3).strip()};`"
        )
    if re.match(r"^[A-Za-z_]\w*\s*=", line):
        raise ZyenError(f"line {line_no}: bare assignment is not allowed; use `let`, `const`, or `set`")
    if not line.endswith(";"):
        raise ZyenError(f"line {line_no}: statement must end with `;`")
    return transform_expr(line, ctx)


def c_param_type(ztype: str) -> str:
    if ztype_base(ztype) == "List":
        return "ZL_List*"
    return c_type(ztype)


def c_function_signature(name: str, ret_type: str, params: Dict[str, str]) -> str:
    param_c = ", ".join(f"{c_param_type(t)} {n}" for n, t in params.items())
    if not param_c:
        param_c = "void"
    return f"{c_type(ret_type)} {name}({param_c})"


def emit_struct_defs(ctx: TranspileContext) -> List[str]:
    out: List[str] = []
    for struct in ctx.structs.values():
        out.append(f"typedef struct {struct.name} {{")
        for field_name, field_type in struct.fields.items():
            out.append(f"    {c_type(field_type)} {field_name};")
        out.append(f"}} {struct.name};")
        out.append("")
    return out


def emit_function_prototypes(ctx: TranspileContext) -> List[str]:
    out: List[str] = []
    for fn in ctx.functions.values():
        out.append(c_function_signature(fn.name, fn.ret_type, fn.params) + ";")
    if out:
        out.append("")
    return out


def emit_function_body(lines: List[Tuple[int, str]], start_i: int, out: List[str], ctx: TranspileContext) -> int:
    block_stack: List[str] = ["fn"]
    i = start_i
    while i < len(lines):
        line_no, line = lines[i]
        if line.startswith("if"):
            m = re.match(r"if\s*\((.*)\)\s*\{\s*$", line)
            if not m:
                raise ZyenError(f"line {line_no}: invalid if statement; use `if (condition) {{`")
            cond = m.group(1).strip()
            out.append(f"if ({transform_expr(cond, ctx)}) {{")
            push_scope(ctx)
            block_stack.append("if")
        elif re.match(r"}\s*else\s+if\s*\(.*\)\s*{\s*$", line):
            if not block_stack:
                raise ZyenError(f"line {line_no}: unexpected `else if`")
            closed = block_stack.pop()
            if closed == "for":
                ctx.loop_depth -= 1
            pop_scope(ctx)
            m = re.match(r"}\s*else\s+if\s*\((.*)\)\s*{\s*$", line)
            cond = m.group(1).strip()
            out.append(f"}} else if ({transform_expr(cond, ctx)}) {{")
            push_scope(ctx)
            block_stack.append("if")
        elif line == "} else {":
            if not block_stack:
                raise ZyenError(f"line {line_no}: unexpected `else`")
            closed = block_stack.pop()
            if closed == "for":
                ctx.loop_depth -= 1
            pop_scope(ctx)
            out.append("} else {")
            push_scope(ctx)
            block_stack.append("else")
        elif line == "}":
            if not block_stack:
                raise ZyenError(f"line {line_no}: unexpected `}}`")
            closed = block_stack.pop()
            if closed == "for":
                ctx.loop_depth -= 1
            pop_scope(ctx)
            out.append("}")
            out.append("")
            i += 1
            if not block_stack:
                return i
            continue
        elif line.startswith("for"):
            push_scope(ctx)
            out.append(parse_for(line, ctx, line_no))
            block_stack.append("for")
            ctx.loop_depth += 1
        elif line.startswith("class "):
            raise ZyenError(f"line {line_no}: `class` is planned for v0.2; v0.1 supports `struct`")
        else:
            out.append("    " + transform_statement(line, ctx, line_no))
        i += 1
    raise ZyenError("function is missing closing `}`")


def reset_function_context(ctx: TranspileContext, name: str, params: Dict[str, str]) -> None:
    ctx.current_function = name
    ctx.symbols = {param_name: ztype_base(param_type) for param_name, param_type in params.items()}
    # Preserve special pointer-to-struct self type for methods.
    for param_name, param_type in params.items():
        if ptrstruct_inner_type(param_type):
            ctx.symbols[param_name] = param_type.replace(" ", "")
    ctx.consts = set()
    ctx.ptr_targets = {}
    ctx.list_refs = {param_name for param_name, param_type in params.items() if ztype_base(param_type) == "List"}
    ctx.scope_stack = [set(params.keys())]
    ctx.loop_depth = 0
    for param_name, param_type in params.items():
        inner = ptr_inner_type(param_type)
        if inner:
            ctx.ptr_targets[param_name] = inner

def transpile(source: str) -> str:
    lines = clean_lines(source)
    ctx = collect_signatures(lines)
    out: List[str] = []
    out.append("// Generated by ZyenLang v0.1.49")
    out.append("#include <stdio.h>")
    out.append("#include <stdbool.h>")
    out.append("#include <stdlib.h>")
    out.append("#include <string.h>")
    out.append("#include <stdarg.h>")
    out.append("#include <time.h>")
    out.append("#ifdef _WIN32")
    out.append("#include <windows.h>")
    out.append("#else")
    out.append("#include <unistd.h>")
    out.append("#endif")
    out.append("")
    out.append("typedef struct { void* addr; const char* type_name; int mem_id; bool owned; } ptr;")
    out.append("#define ZL_MEM_MAX 4096")
    out.append("static void* zl_mem_addr[ZL_MEM_MAX];")
    out.append("static bool zl_mem_valid[ZL_MEM_MAX];")
    out.append("static int zl_mem_next_id = 1;")
    out.append("static inline ptr zl_ptr(void* addr, const char* type_name) { return (ptr){ addr, type_name, 0, false }; }")
    out.append("static inline ptr zl_none_ptr(const char* type_name) { return (ptr){ NULL, type_name, 0, false }; }")
    out.append("static inline bool zl_ptr_is_none(ptr p) { return p.addr == NULL; }")
    out.append("static inline bool zl_ptr_is_owned(ptr p) { return p.mem_id > 0; }")
    out.append("static inline bool zl_ptr_is_valid(ptr p) { if (p.addr == NULL) return false; if (p.mem_id == 0) return true; if (p.mem_id <= 0 || p.mem_id >= ZL_MEM_MAX) return false; return zl_mem_valid[p.mem_id] && zl_mem_addr[p.mem_id] == p.addr; }")
    out.append("static inline void* zl_ptr_checked_addr(ptr p, const char* name) { if (p.addr == NULL) { fprintf(stderr, \"None pointer dereference: %s\\n\", name); exit(1); } if (!zl_ptr_is_valid(p)) { fprintf(stderr, \"Freed pointer dereference: %s\\n\", name); exit(1); } return p.addr; }")
    out.append("static inline ptr zl_mem_alloc_cell(size_t size, const char* type_name) { void* raw = calloc(1, size); if (!raw) { fprintf(stderr, \"memory allocation failed\\n\"); exit(1); } int id = zl_mem_next_id++; if (id >= ZL_MEM_MAX) { fprintf(stderr, \"memory registry full\\n\"); exit(1); } zl_mem_addr[id] = raw; zl_mem_valid[id] = true; return (ptr){ raw, type_name, id, true }; }")
    out.append("static inline int zl_mem_free(ptr p) { if (p.addr == NULL) return -1; if (p.mem_id <= 0) return -2; if (p.mem_id >= ZL_MEM_MAX || !zl_mem_valid[p.mem_id]) return -3; if (p.type_name && strcmp(p.type_name, \"str\") == 0 && p.addr) { char* inner = *((char**)p.addr); if (inner) free(inner); } free(p.addr); zl_mem_addr[p.mem_id] = NULL; zl_mem_valid[p.mem_id] = false; return 0; }")
    out.append("static inline bool zl_mem_is_valid(ptr p) { return zl_ptr_is_valid(p); }")
    out.append("static inline bool zl_mem_is_none(ptr p) { return p.addr == NULL; }")
    out.append("static inline bool zl_mem_is_owned(ptr p) { return p.mem_id > 0 && p.owned; }")
    out.append("static inline const char* zl_mem_type(ptr p) { return p.type_name ? p.type_name : \"ptr\"; }")
    out.append("static inline ptr zl_mem_alloc_int(int v) { ptr p = zl_mem_alloc_cell(sizeof(int), \"int\"); *((int*)p.addr) = v; return p; }")
    out.append("static inline ptr zl_mem_alloc_float(double v) { ptr p = zl_mem_alloc_cell(sizeof(double), \"float\"); *((double*)p.addr) = v; return p; }")
    out.append("static inline ptr zl_mem_alloc_bool(bool v) { ptr p = zl_mem_alloc_cell(sizeof(bool), \"bool\"); *((bool*)p.addr) = v; return p; }")
    out.append("static inline char* zl_mem_strdup(const char* s) { size_t n = strlen(s ? s : \"\") + 1; char* out = (char*)malloc(n); if (!out) { fprintf(stderr, \"string allocation failed\\n\"); exit(1); } memcpy(out, s ? s : \"\", n); return out; }")
    out.append("static inline ptr zl_mem_alloc_str(const char* v) { ptr p = zl_mem_alloc_cell(sizeof(char*), \"str\"); *((char**)p.addr) = zl_mem_strdup(v); return p; }")
    out.append("typedef struct ZL_List ZL_List;")
    out.append("typedef struct { int kind; long long i; double f; const char* s; bool b; ptr p; ZL_List* l; } Any;")
    out.append("typedef struct ZL_List { Any* items; int len; int cap; } ZL_List;")
    out.append("static inline Any zl_any_int(int v) { return (Any){ .kind = 1, .i = v }; }")
    out.append("static inline Any zl_any_float(double v) { return (Any){ .kind = 2, .f = v }; }")
    out.append("static inline Any zl_any_str(const char* v) { return (Any){ .kind = 3, .s = zl_mem_strdup(v ? v : \"\") }; }")
    out.append("static inline Any zl_any_bool(bool v) { return (Any){ .kind = 4, .b = v }; }")
    out.append("static inline Any zl_any_ptr(ptr v) { return (Any){ .kind = 5, .p = v }; }")
    out.append("static inline Any zl_any_list(ZL_List* v) { return (Any){ .kind = 6, .l = v }; }")
    out.append("static inline ptr zl_mem_alloc_any(Any v) { ptr p = zl_mem_alloc_cell(sizeof(Any), \"Any\"); *((Any*)p.addr) = v; return p; }")
    out.append("static inline int zl_int_checked(long long v, const char* op) { if (v < -2147483648LL || v > 2147483647LL) { fprintf(stderr, \"int overflow in %s: %lld\\n\", op, v); exit(1); } return (int)v; }")
    out.append("static inline int zl_int_add(int a, int b) { return zl_int_checked((long long)a + (long long)b, \"+\"); }")
    out.append("static inline int zl_int_sub(int a, int b) { return zl_int_checked((long long)a - (long long)b, \"-\"); }")
    out.append("static inline int zl_int_mul(int a, int b) { return zl_int_checked((long long)a * (long long)b, \"*\"); }")
    out.append("static inline int zl_int_div(int a, int b) { if (b == 0) { fprintf(stderr, \"int divide by zero\\n\"); exit(1); } if (a == -2147483648 && b == -1) { fprintf(stderr, \"int overflow in /: 2147483648\\n\"); exit(1); } return a / b; }")
    out.append("static inline const char* zl_cast_str_int(int v) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; snprintf(out, 64, \"%d\", v); return out; }")
    out.append("static inline const char* zl_cast_str_float(double v) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; snprintf(out, 64, \"%g\", v); return out; }")
    out.append("static inline const char* zl_cast_str_bool(bool v) { return v ? \"true\" : \"false\"; }")
    out.append("static inline const char* zl_cast_str_ptr(ptr v) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; if (v.addr == NULL) return \"None\"; if (!zl_ptr_is_valid(v)) return \"Freed\"; snprintf(out, 64, \"%p\", (void*)v.addr); return out; }")
    out.append("static inline int zl_cast_int_str(const char* s) { return atoi(s); }")
    out.append("static inline double zl_cast_float_str(const char* s) { return atof(s); }")
    out.append("static inline bool zl_cast_bool_str(const char* s) { return strcmp(s, \"true\") == 0 || strcmp(s, \"1\") == 0; }")
    out.append("static inline int zl_cast_int_any(Any v) { switch (v.kind) { case 1: return (int)v.i; case 2: return (int)v.f; case 3: return atoi(v.s); case 4: return v.b ? 1 : 0; default: return 0; } }")
    out.append("static inline double zl_cast_float_any(Any v) { switch (v.kind) { case 1: return (double)v.i; case 2: return v.f; case 3: return atof(v.s); case 4: return v.b ? 1.0 : 0.0; default: return 0.0; } }")
    out.append("static inline bool zl_cast_bool_any(Any v) { switch (v.kind) { case 1: return v.i != 0; case 2: return v.f != 0.0; case 3: return strcmp(v.s, \"true\") == 0 || strcmp(v.s, \"1\") == 0; case 4: return v.b; case 5: return zl_ptr_is_valid(v.p); case 6: return v.l != NULL && v.l->len > 0; default: return false; } }")
    out.append("static inline ptr zl_cast_ptr_any(Any v) { if (v.kind == 5) return v.p; return zl_none_ptr(\"void\"); }")
    out.append("static inline const char* zl_cast_str_any(Any v) { static char buf[16][128]; static int idx = 0; char* out = buf[idx++ & 15]; switch (v.kind) { case 1: snprintf(out, 128, \"%lld\", v.i); return out; case 2: snprintf(out, 128, \"%g\", v.f); return out; case 3: return v.s; case 4: return v.b ? \"true\" : \"false\"; case 5: return zl_cast_str_ptr(v.p); case 6: if (v.l == NULL) return \"List(None)\"; snprintf(out, 128, \"List(len=%d)\", v.l->len); return out; default: return \"Any(?)\"; } }")
    out.append("static inline const char* zl_cast_str_list(ZL_List* l) { static char buf[16][64]; static int idx = 0; char* out = buf[idx++ & 15]; if (l == NULL) return \"List(None)\"; snprintf(out, 64, \"List(len=%d)\", l->len); return out; }")
    out.append("static inline const char* zl_str_join(int count, ...) { size_t total = 1; va_list ap; va_start(ap, count); for (int i = 0; i < count; i++) { const char* s = va_arg(ap, const char*); if (!s) s = \"\"; total += strlen(s); } va_end(ap); char* out = (char*)malloc(total); if (!out) { fprintf(stderr, \"string join allocation failed\\n\"); exit(1); } out[0] = 0; va_start(ap, count); for (int i = 0; i < count; i++) { const char* s = va_arg(ap, const char*); if (!s) s = \"\"; strcat(out, s); } va_end(ap); return out; }")
    out.append("static inline const char* zl_char_to_str(int c) { char* out = (char*)malloc(2); if (!out) { fprintf(stderr, \"char allocation failed\\n\"); exit(1); } out[0] = (char)c; out[1] = 0; return out; }")
    out.append("static inline const char* zl_str_substring(const char* s, int start, int length) { if (!s) s = \"\"; int n = (int)strlen(s); if (start < 0) start = 0; if (start > n) start = n; if (length < 0 || start + length > n) length = n - start; char* out = (char*)malloc((size_t)length + 1); if (!out) { fprintf(stderr, \"substring allocation failed\\n\"); exit(1); } memcpy(out, s + start, (size_t)length); out[length] = 0; return out; }")
    out.append("static inline int zl_time_clock_ms(void) { return (int)((clock() * 1000) / CLOCKS_PER_SEC); }")
    out.append("static inline int zl_thread_sleep_ms(int ms) { if (ms < 0) ms = 0; clock_t start = clock(); double target = ((double)ms) / 1000.0; while (((double)(clock() - start) / CLOCKS_PER_SEC) < target) { } return 0; }")
    out.append("static inline int zl_thread_yield_now(void) { return zl_thread_sleep_ms(0); }")
    out.append("static inline int zl_thread_cpu_count(void) {")
    out.append("#ifdef _WIN32")
    out.append("    SYSTEM_INFO info; GetSystemInfo(&info); return (int)info.dwNumberOfProcessors;")
    out.append("#else")
    out.append("    long n = sysconf(_SC_NPROCESSORS_ONLN); return n > 0 ? (int)n : 1;")
    out.append("#endif")
    out.append("}")
    out.append('static inline int zl_thread_run_cmd(const char* command) { fflush(stdout); return system(command ? command : ""); }')
    out.append("static inline int zl_thread_spawn_cmd(const char* command) {")
    out.append("    if (!command || !command[0]) return -1;")
    out.append("    char cmd[1400];")
    out.append("#ifdef _WIN32")
    out.append('    snprintf(cmd, sizeof(cmd), "start \\"\\" /B cmd /C \\"%s\\"", command);')
    out.append("#else")
    out.append('    snprintf(cmd, sizeof(cmd), "%s &", command);')
    out.append("#endif")
    out.append("    fflush(stdout); return system(cmd);")
    out.append("}")
    out.append('static inline int zl_cmd_run(const char* command) { fflush(stdout); return system(command); }')
    out.append('static inline int zl_term_clear(void) { fflush(stdout); return system("cls || clear"); }')
    out.append('static inline void zl_term_line(void) { printf("------------------------------------------------------------\\n"); }')
    out.append('static inline const char* zl_term_input(const char* prompt) { char buf[1024]; if (prompt) { printf("%s", prompt); fflush(stdout); } if (!fgets(buf, sizeof(buf), stdin)) { buf[0] = 0; return zl_mem_strdup(""); } size_t n = strlen(buf); while (n > 0 && (buf[n-1] == \"\\n\"[0] || buf[n-1] == \"\\r\"[0])) { buf[--n] = 0; } return zl_mem_strdup(buf); }')
    out.append('static inline int zl_term_pause(void) { printf("Press Enter to continue..."); fflush(stdout); char tmp[8]; fgets(tmp, sizeof(tmp), stdin); return 0; }')
    out.append('static inline bool zl_fs_exists(const char* path) { FILE* f = fopen(path, "rb"); if (!f) return false; fclose(f); return true; }')
    out.append('static inline const char* zl_fs_read(const char* path) { FILE* f = fopen(path, "rb"); if (!f) return ""; if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return ""; } long n = ftell(f); if (n < 0) { fclose(f); return ""; } rewind(f); char* buf = (char*)malloc((size_t)n + 1); if (!buf) { fclose(f); fprintf(stderr, "fs.read allocation failed\\n"); exit(1); } size_t got = fread(buf, 1, (size_t)n, f); buf[got] = 0; fclose(f); return buf; }')
    out.append('static inline int zl_fs_write(const char* path, const char* text) { FILE* f = fopen(path, "wb"); if (!f) return -1; fputs(text ? text : "", f); fclose(f); return 0; }')
    out.append('static inline int zl_fs_append(const char* path, const char* text) { FILE* f = fopen(path, "ab"); if (!f) return -1; fputs(text ? text : "", f); fclose(f); return 0; }')
    out.append("static inline const char* zl_path_basename(const char* path) { if (!path) path = \"\"; const char* a = strrchr(path, '/'); const char* b = strrchr(path, '\\\\'); const char* p = a > b ? a : b; return zl_mem_strdup(p ? p + 1 : path); }")
    out.append("static inline const char* zl_path_dirname(const char* path) { if (!path) path = \"\"; const char* a = strrchr(path, '/'); const char* b = strrchr(path, '\\\\'); const char* p = a > b ? a : b; if (!p) return \".\"; return zl_str_substring(path, 0, (int)(p - path)); }")
    out.append("static inline const char* zl_path_ext(const char* path) { const char* base = zl_path_basename(path); const char* dot = strrchr(base, '.'); if (!dot || dot == base) return \"\"; return zl_mem_strdup(dot); }")
    out.append("static inline const char* zl_path_join(const char* a, const char* b) { if (!a || !a[0]) return zl_mem_strdup(b ? b : \"\"); if (!b || !b[0]) return zl_mem_strdup(a); const char* sep = (a[strlen(a)-1] == '/' || a[strlen(a)-1] == '\\\\') ? \"\" : \"/\"; return zl_str_join(3, a, sep, b); }")
    out.append("static inline ZL_List zl_list_new(void) { return (ZL_List){ NULL, 0, 0 }; }")
    out.append("static inline void zl_list_ensure(ZL_List* l, int need) { if (l->cap >= need) return; int cap = l->cap ? l->cap * 2 : 4; while (cap < need) cap *= 2; Any* next = (Any*)realloc(l->items, sizeof(Any) * cap); if (!next) { fprintf(stderr, \"List allocation failed\\n\"); exit(1); } l->items = next; l->cap = cap; }")
    out.append("static inline void zl_list_append(ZL_List* l, Any v) { zl_list_ensure(l, l->len + 1); l->items[l->len++] = v; }")
    out.append("static inline int zl_list_len(ZL_List* l) { return l->len; }")
    out.append("static inline bool zl_list_is_empty(ZL_List* l) { return l->len == 0; }")
    out.append("static inline Any zl_list_get(ZL_List* l, int index) { if (index < 0 || index >= l->len) { fprintf(stderr, \"List index out of range: %d\\n\", index); exit(1); } return l->items[index]; }")
    out.append("static inline ptr zl_list_ptr(ZL_List* l, int index) { if (index < 0 || index >= l->len) { fprintf(stderr, \"List index out of range: %d\\n\", index); exit(1); } return zl_ptr(&l->items[index], \"Any\"); }")
    out.append("static inline ptr zl_list_append_ptr(ZL_List* l, Any v) { zl_list_append(l, v); return zl_ptr(&l->items[l->len - 1], \"Any\"); }")
    out.append("static inline void zl_list_set(ZL_List* l, int index, Any v) { if (index < 0 || index >= l->len) { fprintf(stderr, \"List index out of range: %d\\n\", index); exit(1); } l->items[index] = v; }")
    out.append("static inline Any zl_list_pop(ZL_List* l) { if (l->len <= 0) { fprintf(stderr, \"List pop from empty list\\n\"); exit(1); } return l->items[--l->len]; }")
    out.append("static inline void zl_list_clear(ZL_List* l) { l->len = 0; }")
    out.append("static inline ZL_List zl_str_split(const char* s, const char* sep) { ZL_List list = zl_list_new(); if (!s) s = \"\"; if (!sep || sep[0] == 0) { for (int i = 0; s[i]; i++) zl_list_append(&list, zl_any_str(zl_char_to_str((unsigned char)s[i]))); return list; } size_t sepn = strlen(sep); const char* cur = s; const char* hit = NULL; while ((hit = strstr(cur, sep)) != NULL) { int len = (int)(hit - cur); zl_list_append(&list, zl_any_str(zl_str_substring(cur, 0, len))); cur = hit + sepn; } zl_list_append(&list, zl_any_str(cur)); return list; }")
    out.append("#ifdef _WIN32")
    out.append('static inline ZL_List zl_fs_list_dir(const char* path) { ZL_List l = zl_list_new(); if (!path || !path[0]) return l; char pattern[1024]; snprintf(pattern, sizeof(pattern), "%s/*", path); WIN32_FIND_DATAA fd; HANDLE h = FindFirstFileA(pattern, &fd); if (h == INVALID_HANDLE_VALUE) return l; do { if (strcmp(fd.cFileName, ".") == 0) continue; char entry[600]; if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) snprintf(entry, sizeof(entry), "%s/", fd.cFileName); else snprintf(entry, sizeof(entry), "%s", fd.cFileName); zl_list_append(&l, zl_any_str(zl_mem_strdup(entry))); } while (FindNextFileA(h, &fd)); FindClose(h); return l; }')
    out.append('static inline int zl_fs_mkdir(const char* path) { if (!path || !path[0]) return -1; if (CreateDirectoryA(path, NULL)) return 0; DWORD err = GetLastError(); if (err == ERROR_ALREADY_EXISTS) return 0; return -2; }')
    out.append('static inline bool zl_fs_is_dir(const char* path) { if (!path) return false; DWORD a = GetFileAttributesA(path); if (a == INVALID_FILE_ATTRIBUTES) return false; return (a & FILE_ATTRIBUTE_DIRECTORY) != 0; }')
    out.append("#else")
    out.append("#include <dirent.h>")
    out.append("#include <sys/stat.h>")
    out.append("#include <errno.h>")
    out.append('static inline ZL_List zl_fs_list_dir(const char* path) { ZL_List l = zl_list_new(); if (!path || !path[0]) return l; DIR* d = opendir(path); if (!d) return l; struct dirent* e; while ((e = readdir(d)) != NULL) { if (strcmp(e->d_name, ".") == 0) continue; char entry[600]; char full[1100]; snprintf(full, sizeof(full), "%s/%s", path, e->d_name); struct stat st; if (stat(full, &st) == 0 && S_ISDIR(st.st_mode)) snprintf(entry, sizeof(entry), "%s/", e->d_name); else snprintf(entry, sizeof(entry), "%s", e->d_name); zl_list_append(&l, zl_any_str(zl_mem_strdup(entry))); } closedir(d); return l; }')
    out.append('static inline int zl_fs_mkdir(const char* path) { if (!path || !path[0]) return -1; if (mkdir(path, 0755) == 0) return 0; if (errno == EEXIST) return 0; return -2; }')
    out.append('static inline bool zl_fs_is_dir(const char* path) { if (!path) return false; struct stat st; if (stat(path, &st) != 0) return false; return S_ISDIR(st.st_mode); }')
    out.append("#endif")
    out.append("static inline const char* zl_str_lower(const char* s) { if (!s) s = \"\"; size_t n = strlen(s); char* out = (char*)malloc(n + 1); if (!out) { fprintf(stderr, \"lower allocation failed\\n\"); exit(1); } for (size_t i = 0; i < n; i++) { char ch = s[i]; out[i] = (ch >= 'A' && ch <= 'Z') ? (char)(ch + 32) : ch; } out[n] = 0; return out; }")
    out.append("static inline const char* zl_str_upper(const char* s) { if (!s) s = \"\"; size_t n = strlen(s); char* out = (char*)malloc(n + 1); if (!out) { fprintf(stderr, \"upper allocation failed\\n\"); exit(1); } for (size_t i = 0; i < n; i++) { char ch = s[i]; out[i] = (ch >= 'a' && ch <= 'z') ? (char)(ch - 32) : ch; } out[n] = 0; return out; }")
    out.append("static inline const char* zl_str_trim(const char* s) { if (!s) s = \"\"; const char* start = s; while (*start == ' ' || *start == '\\t' || *start == '\\r' || *start == '\\n') start++; const char* end = s + strlen(s); while (end > start && (end[-1] == ' ' || end[-1] == '\\t' || end[-1] == '\\r' || end[-1] == '\\n')) end--; int len = (int)(end - start); return zl_str_substring(start, 0, len); }")
    out.append("static inline const char* zl_str_repeat(const char* s, int count) { if (!s) s = \"\"; if (count <= 0) return \"\"; size_t n = strlen(s); size_t total = n * (size_t)count; char* out = (char*)malloc(total + 1); if (!out) { fprintf(stderr, \"repeat allocation failed\\n\"); exit(1); } char* p = out; for (int i = 0; i < count; i++) { memcpy(p, s, n); p += n; } out[total] = 0; return out; }")
    out.append("static inline const char* zl_str_replace(const char* s, const char* old, const char* repl) { if (!s) s = \"\"; if (!old || old[0] == 0) return zl_mem_strdup(s); if (!repl) repl = \"\"; size_t oldn = strlen(old), repln = strlen(repl); int hits = 0; const char* cur = s; const char* hit; while ((hit = strstr(cur, old)) != NULL) { hits++; cur = hit + oldn; } size_t outn = strlen(s) + (size_t)hits * (repln > oldn ? repln - oldn : 0) + 1; if (repln < oldn) outn = strlen(s) - (size_t)hits * (oldn - repln) + 1; char* out = (char*)malloc(outn); if (!out) { fprintf(stderr, \"replace allocation failed\\n\"); exit(1); } char* dst = out; cur = s; while ((hit = strstr(cur, old)) != NULL) { size_t keep = (size_t)(hit - cur); memcpy(dst, cur, keep); dst += keep; memcpy(dst, repl, repln); dst += repln; cur = hit + oldn; } strcpy(dst, cur); return out; }")
    out.append("static inline const char* zl_str_reverse(const char* s) { if (!s) s = \"\"; size_t n = strlen(s); char* out = (char*)malloc(n + 1); if (!out) { fprintf(stderr, \"reverse allocation failed\\n\"); exit(1); } for (size_t i = 0; i < n; i++) out[i] = s[n - 1 - i]; out[n] = 0; return out; }")
    out.append('static inline void zl_print_any(Any v) { switch (v.kind) { case 1: printf("%lld\\n", v.i); break; case 2: printf("%g\\n", v.f); break; case 3: printf("%s\\n", v.s); break; case 4: printf("%s\\n", v.b ? "true" : "false"); break; case 5: if (v.p.addr == NULL) printf("None\\n"); else if (!zl_ptr_is_valid(v.p)) printf("Freed\\n"); else printf("%p\\n", (void*)v.p.addr); break; case 6: if (v.l == NULL) printf("List(None)\\n"); else printf("List(len=%d)\\n", v.l->len); break; default: printf("Any(?)\\n"); } }')
    out.append('static inline void zl_print_list(ZL_List* l) { printf("List(len=%d)\\n", l->len); }')
    out.append("")
    out.append("static inline void zl_quote_arg(char* out_arg, size_t cap, const char* in_arg) { size_t j = 0; if (cap == 0) return; out_arg[j++] = \'\"\'; for (size_t i = 0; in_arg && in_arg[i] && j + 3 < cap; i++) { char ch = in_arg[i]; if (ch == \'\"\') ch = \'_\'; out_arg[j++] = ch; } if (j + 1 < cap) out_arg[j++] = \'\"\'; out_arg[j < cap ? j : cap - 1] = 0; }")
    out.append("static inline int zl_py_cmd0(const char* module, const char* op) { char cmd[512]; snprintf(cmd, sizeof(cmd), \"python -m %s %s\", module, op); return system(cmd); }")
    out.append("static inline int zl_py_cmd2(const char* module, const char* op, const char* a, const char* b) { char qa[512], qb[512], cmd[1600]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s\", module, op, qa, qb); return system(cmd); }")
    out.append("static inline int zl_py_cmd3i(const char* module, const char* op, const char* a, const char* b, int x) { char qa[512], qb[512], cmd[1700]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s %d\", module, op, qa, qb, x); return system(cmd); }")
    out.append("static inline int zl_py_cmd4ii(const char* module, const char* op, const char* a, const char* b, int x, int y) { char qa[512], qb[512], cmd[1800]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s %d %d\", module, op, qa, qb, x, y); return system(cmd); }")
    out.append("static inline int zl_py_cmd3s(const char* module, const char* op, const char* a, const char* b, const char* c) { char qa[512], qb[512], qc[512], cmd[2100]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); zl_quote_arg(qc, sizeof(qc), c); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %s %s\", module, op, qa, qb, qc); return system(cmd); }")
    out.append("static inline int zl_py_cmd3sf(const char* module, const char* op, const char* a, double x, const char* b) { char qa[512], qb[512], cmd[2100]; zl_quote_arg(qa, sizeof(qa), a); zl_quote_arg(qb, sizeof(qb), b); snprintf(cmd, sizeof(cmd), \"python -m %s %s %s %.17g %s\", module, op, qa, x, qb); return system(cmd); }")
    out.append("static char zl_tk_script_path[512] = \"zyen_tk_scene.ztk\";")
    out.append("static FILE* zl_tk_scene_fp = NULL;")
    out.append("static inline const char* zl_tk_clean(const char* s) { static char bufs[16][1024]; static int idx = 0; char* out = bufs[idx++ & 15]; int j = 0; if (!s) s = \"\"; for (int i = 0; s[i] && j < 1023; i++) { char ch = s[i]; if (ch == '\\n' || ch == '\\r' || ch == '\\t') ch = ' '; out[j++] = ch; } out[j] = 0; return out; }")
    out.append("static inline int zl_tk_append_raw(const char* line) { if (zl_tk_scene_fp) { fputs(line ? line : \"\", zl_tk_scene_fp); fputc('\\n', zl_tk_scene_fp); return 0; } FILE* f = fopen(zl_tk_script_path, \"ab\"); if (!f) return -1; fputs(line ? line : \"\", f); fputc('\\n', f); fclose(f); return 0; }")
    out.append("static inline int zl_tk_begin(const char* path) { if (path && path[0]) { strncpy(zl_tk_script_path, path, sizeof(zl_tk_script_path)-1); zl_tk_script_path[sizeof(zl_tk_script_path)-1] = 0; } FILE* f = fopen(zl_tk_script_path, \"wb\"); if (!f) return -1; fputs(\"# ZyenLang tk scene\\n\", f); fclose(f); return 0; }")
    out.append("static inline int zl_tk_window(const char* title, int w, int h) { char line[1400]; snprintf(line, sizeof(line), \"window\\t%s\\t%d\\t%d\", zl_tk_clean(title), w, h); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_open(const char* title, int w, int h) { int code = zl_tk_begin(\"zyen_tk_scene.ztk\"); if (code != 0) return code; return zl_tk_window(title, w, h); }")
    out.append("static inline int zl_tk_bg(const char* color) { char line[1400]; snprintf(line, sizeof(line), \"bg\\t%s\", zl_tk_clean(color)); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_clear(const char* color) { char line[1400]; snprintf(line, sizeof(line), \"clear\\t%s\", zl_tk_clean(color)); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_line(int x1, int y1, int x2, int y2, const char* color, int width) { char line[1400]; snprintf(line, sizeof(line), \"line\\t%d\\t%d\\t%d\\t%d\\t%s\\t%d\", x1, y1, x2, y2, zl_tk_clean(color), width); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_rect(int x, int y, int w, int h, const char* color) { char line[1400]; snprintf(line, sizeof(line), \"rect\\t%d\\t%d\\t%d\\t%d\\t%s\", x, y, w, h, zl_tk_clean(color)); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_rect_outline(int x, int y, int w, int h, const char* color, int width) { char line[1400]; snprintf(line, sizeof(line), \"rect_outline\\t%d\\t%d\\t%d\\t%d\\t%s\\t%d\", x, y, w, h, zl_tk_clean(color), width); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_circle(int x, int y, int r, const char* color) { char line[1400]; snprintf(line, sizeof(line), \"circle\\t%d\\t%d\\t%d\\t%s\", x, y, r, zl_tk_clean(color)); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_circle_outline(int x, int y, int r, const char* color, int width) { char line[1400]; snprintf(line, sizeof(line), \"circle_outline\\t%d\\t%d\\t%d\\t%s\\t%d\", x, y, r, zl_tk_clean(color), width); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_text(int x, int y, const char* text, const char* color, int size) { char line[1800]; snprintf(line, sizeof(line), \"text\\t%d\\t%d\\t%s\\t%s\\t%d\", x, y, zl_tk_clean(text), zl_tk_clean(color), size); return zl_tk_append_raw(line); }")
    out.append("static inline int zl_tk_image(const char* path, int x, int y) { char line[1800]; snprintf(line, sizeof(line), \"image\\t%s\\t%d\\t%d\", zl_tk_clean(path), x, y); return zl_tk_append_raw(line); }")
    out.append("static inline const char* zl_tk_script(void) { return zl_tk_script_path; }")
    out.append("static inline int zl_tk_show(void) { char q[512], cmd[1200]; zl_quote_arg(q, sizeof(q), zl_tk_script_path); snprintf(cmd, sizeof(cmd), \"python -m zyenlang.tk_cli show %s\", q); fflush(stdout); return system(cmd); }")
    out.append("static inline int zl_tk_show_for(int ms) { char q[512], cmd[1300]; zl_quote_arg(q, sizeof(q), zl_tk_script_path); snprintf(cmd, sizeof(cmd), \"python -m zyenlang.tk_cli show %s %d\", q, ms); fflush(stdout); return system(cmd); }")
    # --- tk session (file-IPC) helpers --------------------------------------
    out.append("static char zl_tk_session_dir[512] = \"\";")
    out.append("static long zl_tk_session_event_offset = 0;")
    out.append("static int  zl_tk_session_scene_version = 0;")
    out.append("static inline void zl_tk_session_path(char* out_path, size_t cap, const char* name) {")
    out.append("    snprintf(out_path, cap, \"%s/%s\", zl_tk_session_dir, name);")
    out.append("}")
    out.append("static inline int zl_tk_session_state_field(const char* path, const char* key, char* out_val, size_t cap) {")
    out.append("    if (cap == 0) return -1;")
    out.append("    out_val[0] = 0;")
    out.append("    FILE* f = fopen(path, \"rb\");")
    out.append("    if (!f) return -1;")
    out.append("    char line[256];")
    out.append("    int found = -1;")
    out.append("    while (fgets(line, sizeof(line), f)) {")
    out.append("        size_t kl = strlen(key);")
    out.append("        if (strncmp(line, key, kl) == 0 && line[kl] == '=') {")
    out.append("            const char* v = line + kl + 1;")
    out.append("            size_t vn = strlen(v);")
    out.append("            while (vn > 0 && (v[vn - 1] == '\\n' || v[vn - 1] == '\\r')) vn--;")
    out.append("            if (vn >= cap) vn = cap - 1;")
    out.append("            memcpy(out_val, v, vn);")
    out.append("            out_val[vn] = 0;")
    out.append("            found = 0;")
    out.append("            break;")
    out.append("        }")
    out.append("    }")
    out.append("    fclose(f);")
    out.append("    return found;")
    out.append("}")
    out.append("static inline int zl_tk_session_state_set(const char* path, const char* key, const char* value) {")
    # Read all fields, replace key, write back.
    out.append("    char fields_key[8][32];")
    out.append("    char fields_val[8][128];")
    out.append("    int field_count = 0;")
    out.append("    FILE* f = fopen(path, \"rb\");")
    out.append("    if (f) {")
    out.append("        char line[256];")
    out.append("        while (fgets(line, sizeof(line), f) && field_count < 8) {")
    out.append("            char* eq = strchr(line, '=');")
    out.append("            if (!eq) continue;")
    out.append("            *eq = 0;")
    out.append("            const char* k = line;")
    out.append("            const char* v = eq + 1;")
    out.append("            size_t vn = strlen(v);")
    out.append("            while (vn > 0 && (v[vn - 1] == '\\n' || v[vn - 1] == '\\r')) vn--;")
    out.append("            snprintf(fields_key[field_count], sizeof(fields_key[field_count]), \"%s\", k);")
    out.append("            int copy = (int)vn; if (copy >= (int)sizeof(fields_val[field_count])) copy = (int)sizeof(fields_val[field_count]) - 1;")
    out.append("            memcpy(fields_val[field_count], v, (size_t)copy);")
    out.append("            fields_val[field_count][copy] = 0;")
    out.append("            field_count++;")
    out.append("        }")
    out.append("        fclose(f);")
    out.append("    }")
    out.append("    int replaced = 0;")
    out.append("    for (int i = 0; i < field_count; i++) {")
    out.append("        if (strcmp(fields_key[i], key) == 0) {")
    out.append("            snprintf(fields_val[i], sizeof(fields_val[i]), \"%s\", value);")
    out.append("            replaced = 1;")
    out.append("            break;")
    out.append("        }")
    out.append("    }")
    out.append("    if (!replaced && field_count < 8) {")
    out.append("        snprintf(fields_key[field_count], sizeof(fields_key[field_count]), \"%s\", key);")
    out.append("        snprintf(fields_val[field_count], sizeof(fields_val[field_count]), \"%s\", value);")
    out.append("        field_count++;")
    out.append("    }")
    out.append("    char tmp_path[600];")
    out.append("    snprintf(tmp_path, sizeof(tmp_path), \"%s.tmp\", path);")
    out.append("    FILE* o = fopen(tmp_path, \"wb\");")
    out.append("    if (!o) return -1;")
    out.append("    for (int i = 0; i < field_count; i++) fprintf(o, \"%s=%s\\n\", fields_key[i], fields_val[i]);")
    out.append("    fflush(o); fclose(o);")
    out.append("    remove(path);")
    out.append("    rename(tmp_path, path);")
    out.append("    return 0;")
    out.append("}")
    out.append("static inline int zl_tk_session_open(const char* title, int w, int h, const char* dir) {")
    out.append("    if (!dir || !dir[0]) return -1;")
    out.append("    strncpy(zl_tk_session_dir, dir, sizeof(zl_tk_session_dir) - 1);")
    out.append("    zl_tk_session_dir[sizeof(zl_tk_session_dir) - 1] = 0;")
    out.append("    char mkdir_cmd[1024];")
    out.append("#ifdef _WIN32")
    out.append("    snprintf(mkdir_cmd, sizeof(mkdir_cmd), \"if not exist \\\"%s\\\" mkdir \\\"%s\\\" >nul 2>nul\", zl_tk_session_dir, zl_tk_session_dir);")
    out.append("#else")
    out.append("    snprintf(mkdir_cmd, sizeof(mkdir_cmd), \"mkdir -p '%s' >/dev/null 2>&1\", zl_tk_session_dir);")
    out.append("#endif")
    out.append("    system(mkdir_cmd);")
    out.append("    char scene_path[600];")
    out.append("    zl_tk_session_path(scene_path, sizeof(scene_path), \"scene.ztk\");")
    out.append("    char events_path[600];")
    out.append("    zl_tk_session_path(events_path, sizeof(events_path), \"events.txt\");")
    out.append("    char state_path[600];")
    out.append("    zl_tk_session_path(state_path, sizeof(state_path), \"state.txt\");")
    out.append("    FILE* fe = fopen(events_path, \"wb\"); if (fe) fclose(fe);")
    out.append("    FILE* fs = fopen(state_path, \"wb\"); if (fs) { fputs(\"scene_version=-1\\nevent_version=0\\nclose=0\\nclosed=0\\n\", fs); fclose(fs); }")
    out.append("    strncpy(zl_tk_script_path, scene_path, sizeof(zl_tk_script_path) - 1);")
    out.append("    zl_tk_script_path[sizeof(zl_tk_script_path) - 1] = 0;")
    out.append("    FILE* fsc = fopen(scene_path, \"wb\"); if (fsc) { fclose(fsc); }")
    out.append("    zl_tk_window(title, w, h);")
    out.append("    zl_tk_session_event_offset = 0;")
    out.append("    zl_tk_session_scene_version = 0;")
    out.append("    char spawn_cmd[1400];")
    out.append("#ifdef _WIN32")
    out.append("    snprintf(spawn_cmd, sizeof(spawn_cmd), \"start \\\"\\\" /B cmd /C \\\"python -m zyenlang.tk_cli interactive \\\"%s\\\"\\\"\", zl_tk_session_dir);")
    out.append("#else")
    out.append("    snprintf(spawn_cmd, sizeof(spawn_cmd), \"python -m zyenlang.tk_cli interactive '%s' &\", zl_tk_session_dir);")
    out.append("#endif")
    out.append("    fflush(stdout); system(spawn_cmd);")
    out.append("    int start = zl_time_clock_ms();")
    out.append("    char val[32];")
    out.append("    while (1) {")
    out.append("        if (zl_tk_session_state_field(state_path, \"ready\", val, sizeof(val)) == 0 && strcmp(val, \"1\") == 0) return 0;")
    out.append("        if (zl_time_clock_ms() - start >= 5000) return -1;")
    out.append("        zl_thread_sleep_ms(40);")
    out.append("    }")
    out.append("}")
    out.append("static inline int zl_tk_session_begin_frame(void) {")
    out.append("    if (!zl_tk_session_dir[0]) return -1;")
    out.append("    if (zl_tk_scene_fp) { fclose(zl_tk_scene_fp); zl_tk_scene_fp = NULL; }")
    out.append("    zl_tk_scene_fp = fopen(zl_tk_script_path, \"wb\");")
    out.append("    if (!zl_tk_scene_fp) return -1;")
    out.append("    fputs(\"# ZyenLang tk scene\\n\", zl_tk_scene_fp);")
    out.append("    return 0;")
    out.append("}")
    out.append("static inline int zl_tk_session_redraw(void) {")
    out.append("    if (!zl_tk_session_dir[0]) return -1;")
    out.append("    if (zl_tk_scene_fp) { fflush(zl_tk_scene_fp); fclose(zl_tk_scene_fp); zl_tk_scene_fp = NULL; }")
    out.append("    zl_tk_session_scene_version++;")
    out.append("    char state_path[600];")
    out.append("    zl_tk_session_path(state_path, sizeof(state_path), \"state.txt\");")
    out.append("    char val[32]; snprintf(val, sizeof(val), \"%d\", zl_tk_session_scene_version);")
    out.append("    return zl_tk_session_state_set(state_path, \"scene_version\", val);")
    out.append("}")
    out.append("static inline const char* zl_tk_session_next_event(int timeout_ms) {")
    out.append("    static char buf[16][512]; static int idx = 0;")
    out.append("    char* out = buf[idx++ & 15]; out[0] = 0;")
    out.append("    if (!zl_tk_session_dir[0]) return \"\";")
    out.append("    char events_path[600]; zl_tk_session_path(events_path, sizeof(events_path), \"events.txt\");")
    out.append("    int start = zl_time_clock_ms();")
    out.append("    while (1) {")
    out.append("        FILE* f = fopen(events_path, \"rb\");")
    out.append("        if (f) {")
    out.append("            if (fseek(f, zl_tk_session_event_offset, SEEK_SET) == 0) {")
    out.append("                if (fgets(out, 512, f)) {")
    out.append("                    size_t got = strlen(out);")
    out.append("                    if (got > 0 && (out[got - 1] == '\\n' || out[got - 1] == '\\r')) {")
    out.append("                        zl_tk_session_event_offset = (int)ftell(f);")
    out.append("                        fclose(f);")
    out.append("                        while (got > 0 && (out[got - 1] == '\\n' || out[got - 1] == '\\r')) { out[--got] = 0; }")
    out.append("                        return out;")
    out.append("                    }")
    out.append("                }")
    out.append("            }")
    out.append("            fclose(f);")
    out.append("        }")
    out.append("        if (zl_time_clock_ms() - start >= timeout_ms) { out[0] = 0; return \"\"; }")
    out.append("        zl_thread_sleep_ms(10);")
    out.append("    }")
    out.append("}")
    out.append("static inline int zl_tk_session_close(void) {")
    out.append("    if (!zl_tk_session_dir[0]) return -1;")
    out.append("    char state_path[600]; zl_tk_session_path(state_path, sizeof(state_path), \"state.txt\");")
    out.append("    zl_tk_session_state_set(state_path, \"close\", \"1\");")
    out.append("    int start = zl_time_clock_ms();")
    out.append("    char val[32];")
    out.append("    while (1) {")
    out.append("        if (zl_tk_session_state_field(state_path, \"closed\", val, sizeof(val)) == 0 && strcmp(val, \"1\") == 0) break;")
    out.append("        if (zl_time_clock_ms() - start >= 2000) break;")
    out.append("        zl_thread_sleep_ms(40);")
    out.append("    }")
    out.append("    zl_tk_session_dir[0] = 0;")
    out.append("    return 0;")
    out.append("}")
    out.append("static int zl_tk_cached_char_w = 0;")
    out.append("static int zl_tk_cached_line_h = 0;")
    out.append("static inline int zl_tk_session_char_w(void) {")
    out.append("    if (zl_tk_cached_char_w > 0) return zl_tk_cached_char_w;")
    out.append("    if (!zl_tk_session_dir[0]) return 9;")
    out.append("    char state_path[600]; zl_tk_session_path(state_path, sizeof(state_path), \"state.txt\");")
    out.append("    char val[32];")
    out.append("    for (int retry = 0; retry < 20; retry++) {")
    out.append("        if (zl_tk_session_state_field(state_path, \"char_w\", val, sizeof(val)) == 0) {")
    out.append("            int w = atoi(val);")
    out.append("            if (w > 0) { zl_tk_cached_char_w = w; return w; }")
    out.append("        }")
    out.append("        zl_thread_sleep_ms(20);")
    out.append("    }")
    out.append("    return 9;")
    out.append("}")
    out.append("static inline int zl_tk_session_line_h(void) {")
    out.append("    if (zl_tk_cached_line_h > 0) return zl_tk_cached_line_h;")
    out.append("    if (!zl_tk_session_dir[0]) return 20;")
    out.append("    char state_path[600]; zl_tk_session_path(state_path, sizeof(state_path), \"state.txt\");")
    out.append("    char val[32];")
    out.append("    for (int retry = 0; retry < 20; retry++) {")
    out.append("        if (zl_tk_session_state_field(state_path, \"line_h\", val, sizeof(val)) == 0) {")
    out.append("            int h = atoi(val);")
    out.append("            if (h > 0) { zl_tk_cached_line_h = h; return h; }")
    out.append("        }")
    out.append("        zl_thread_sleep_ms(20);")
    out.append("    }")
    out.append("    return 20;")
    out.append("}")
    out.append("static inline int zl_cv_info(void) { return zl_py_cmd0(\"zyenlang.cv_cli\", \"info\"); }")
    out.append("static inline int zl_cv_readable(const char* input) { char q[512], cmd[1200]; zl_quote_arg(q, sizeof(q), input); snprintf(cmd, sizeof(cmd), \"python -m zyenlang.cv_cli readable %s\", q); return system(cmd); }")
    out.append("static inline int zl_cv_gray(const char* input, const char* output) { return zl_py_cmd2(\"zyenlang.cv_cli\", \"gray\", input, output); }")
    out.append("static inline int zl_cv_resize(const char* input, const char* output, int w, int h) { return zl_py_cmd4ii(\"zyenlang.cv_cli\", \"resize\", input, output, w, h); }")
    out.append("static inline int zl_cv_blur(const char* input, const char* output, int k) { return zl_py_cmd3i(\"zyenlang.cv_cli\", \"blur\", input, output, k); }")
    out.append("static inline int zl_cv_canny(const char* input, const char* output, int low, int high) { return zl_py_cmd4ii(\"zyenlang.cv_cli\", \"canny\", input, output, low, high); }")
    out.append("static inline int zl_cv_threshold(const char* input, const char* output, int t) { return zl_py_cmd3i(\"zyenlang.cv_cli\", \"threshold\", input, output, t); }")
    out.append("static inline int zl_gpu_info(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"info\"); }")
    out.append("static inline bool zl_gpu_has_nvidia(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"has-nvidia\") == 0; }")
    out.append("static inline bool zl_gpu_has_torch_cuda(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"has-torch-cuda\") == 0; }")
    out.append("static inline bool zl_gpu_has_opencv_cuda(void) { return zl_py_cmd0(\"zyenlang.gpu_cli\", \"has-opencv-cuda\") == 0; }")
    out.append("static inline int zl_gpu_cv_gray(const char* input, const char* output) { return zl_py_cmd2(\"zyenlang.cv_cli\", \"gpu-gray\", input, output); }")
    out.append("static inline int zl_gpu_vector_add_csv(const char* a, const char* b, const char* output) { return zl_py_cmd3s(\"zyenlang.gpu_cli\", \"vector-add-csv\", a, b, output); }")
    out.append("static inline int zl_gpu_vector_scale_csv(const char* input, double scale, const char* output) { return zl_py_cmd3sf(\"zyenlang.gpu_cli\", \"vector-scale-csv\", input, scale, output); }")
    out.append("static inline int zl_gpu_dot_csv(const char* a, const char* b, const char* output) { return zl_py_cmd3s(\"zyenlang.gpu_cli\", \"dot-csv\", a, b, output); }")
    out.append("")

    # Emit all structs first so function prototypes may use them regardless of
    # source order. This keeps .zy imports convenient and C backend stable.
    out.extend(emit_struct_defs(ctx))
    out.extend(emit_function_prototypes(ctx))

    i = 0
    while i < len(lines):
        line_no, line = lines[i]
        sm = re.match(r"struct\s+([A-Za-z_]\w*)\s*\{\s*$", line)
        if sm:
            struct_name = sm.group(1)
            i += 1
            while i < len(lines):
                member_no, member_line = lines[i]
                if member_line == "}":
                    i += 1
                    break
                # Skip fields. They were already emitted in emit_struct_defs().
                # ZEP-0006: also accept `let this.name: type = default;`.
                if re.match(r"let\s+this\.[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?\s*(?:=\s*.+?)?\s*;\s*$", member_line):
                    i += 1
                    continue
                if re.match(r"(?:let\s+)?[A-Za-z_]\w*\s*:\s*[A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?\s*(?:=\s*.+?)?\s*;\s*$", member_line):
                    raise ZyenError(f"line {member_no}: struct field must use `let this.name: type;` or `let this.name: type = default;`, for example `let this.list_len: int;`")
                fm_method = re.match(r"fn\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?))?\s*\{\s*$", member_line)
                if fm_method:
                    method_name = fm_method.group(1)
                    params, _defaults = parse_params(fm_method.group(2), member_no)
                    ret_type = (fm_method.group(3) or "void").replace(" ", "")
                    c_name = f"{struct_name}_{method_name}"
                    c_params = {"this": f"ptrstruct<{struct_name}>"}
                    c_params.update(params)
                    reset_function_context(ctx, c_name, c_params)
                    out.append(c_function_signature(c_name, ret_type, c_params) + " {")
                    i = emit_function_body(lines, i + 1, out, ctx)
                    continue
                raise ZyenError(f"line {member_no}: invalid struct member")
            continue

        fm_decl = re.match(r"fn\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?))?\s*;\s*$", line)
        if fm_decl:
            # Explicit prototype only.  collect_signatures() already registered it.
            i += 1
            continue

        fm = re.match(r"fn\s+([A-Za-z_]\w*)\s*\((.*)\)\s*(?:->\s*([A-Za-z_]\w*(?:\s*<\s*[A-Za-z_]\w*\s*>)?))?\s*\{\s*$", line)
        if fm:
            name = fm.group(1)
            ret_type = (fm.group(3) or "void").replace(" ", "")
            params, _defaults = parse_params(fm.group(2), line_no)
            reset_function_context(ctx, name, params)
            out.append(c_function_signature(name, ret_type, params) + " {")
            i = emit_function_body(lines, i + 1, out, ctx)
            continue

        if line.startswith("class "):
            raise ZyenError(f"line {line_no}: `class` is planned for v0.2; v0.1 supports `struct`")
        if line.startswith("let ") or line.startswith("const ") or line.startswith("set "):
            raise ZyenError(f"line {line_no}: top-level variables are not supported yet; put variables inside `fn main()` or use a zero-argument function for constants")
        if line and line != "}":
            raise ZyenError(f"line {line_no}: top-level statement is not allowed; only import, struct, and fn are allowed at file scope")
        i += 1

    return "\n".join(out).rstrip() + "\n"


def resolve_import_path(base_dir: Path, import_name: str) -> Path:
    candidate = (base_dir / import_name).resolve()
    if candidate.exists():
        return candidate
    if candidate.suffix == "":
        candidate_zy = candidate.with_suffix(".zy")
        if candidate_zy.exists():
            return candidate_zy
    raise FileNotFoundError(candidate)


def resolve_std_import_path(import_name: str) -> Path:
    """Resolve `import <...>;` from the bundled standard library.

    Supported forms:
      import <std/math>;
      import <std/math.zy>;
      import <math>;            // shorthand for std/math.zy
      import <std/math> as m;   // alias for calls like m.abs_int(...)

    v0.1.17 supports both source-tree execution and installed-module execution:
      - source tree: <project>/std/math.zy
      - installed package: zyenlang/std/math.zy
    """
    name = import_name.strip().replace("\\", "/")
    roots = [
        Path(__file__).resolve().parent,       # installed package: zyenlang/std
        Path(__file__).resolve().parents[1],  # source tree: project/std
    ]

    checked: List[Path] = []
    for root in roots:
        if name.startswith("std/"):
            candidate = root / name
        else:
            candidate = root / "std" / name
        candidate = candidate.resolve()
        checked.append(candidate)
        if candidate.exists():
            return candidate
        if candidate.suffix == "":
            candidate_zy = candidate.with_suffix(".zy")
            checked.append(candidate_zy)
            if candidate_zy.exists():
                return candidate_zy

    raise FileNotFoundError(checked[-1])


def module_name_from_path(path: Path) -> str:
    return path.stem


def module_prefix(module_name: str) -> str:
    safe = re.sub(r"\W+", "_", module_name.strip())
    if not re.match(r"^[A-Za-z_]", safe):
        safe = "m_" + safe
    return f"zlmod_{safe}_"


def _brace_delta_outside_strings(line: str) -> int:
    delta = 0
    in_str = False
    esc = False
    for ch in line:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            delta += 1
        elif ch == "}":
            delta -= 1
    return delta


def collect_function_names_from_source(source: str) -> List[str]:
    """Collect only top-level function names from a source file.

    Struct methods are intentionally not namespaced. This lets a std module
    expose a struct such as `TestStats` whose methods are called as
    `stats.eq_int(...)` even though top-level module functions are still called
    through `test.new_stats(...)`.
    """
    names: List[str] = []
    depth = 0
    for _line_no, line in clean_lines(source):
        if depth == 0:
            m = re.match(r"fn\s+([A-Za-z_]\w*)\s*\(", line)
            if m and m.group(1) not in names:
                names.append(m.group(1))
        depth += _brace_delta_outside_strings(line)
        if depth < 0:
            depth = 0
    return names

def replace_module_calls(line: str, aliases: Dict[str, str]) -> str:
    """Convert namespace calls like `math.abs_int(x)` to valid backend names.

    This only rewrites dotted function calls, not struct fields like `p.x`.
    """
    for alias, prefix in sorted(aliases.items(), key=lambda kv: len(kv[0]), reverse=True):
        line = re.sub(
            rf"\b{re.escape(alias)}\.([A-Za-z_]\w*)\s*\(",
            lambda m, p=prefix: f"{p}{m.group(1)}(",
            line,
        )
    return line


def prefix_module_body(source: str, module_name: str) -> str:
    """Namespace top-level functions in a module body.

    `fn abs_int(...)` becomes `fn zlmod_math_abs_int(...)`. Struct method
    names are not rewritten; method dispatch uses the struct type and stays
    source-like (`stats.eq_int(...)`).
    """
    prefix = module_prefix(module_name)
    names = collect_function_names_from_source(source)
    lines = source.splitlines()
    out_lines: List[str] = []
    depth = 0
    for line in lines:
        if depth == 0:
            for name in names:
                line = re.sub(rf"\bfn\s+{re.escape(name)}\s*\(", f"fn {prefix}{name}(" , line)
        out_lines.append(line)
        depth += _brace_delta_outside_strings(strip_comment(line))
        if depth < 0:
            depth = 0
    out = "\n".join(out_lines) + ("\n" if source.endswith("\n") else "")
    for name in names:
        out = re.sub(rf"(?<![\.\w]){re.escape(name)}\s*\(", f"{prefix}{name}(" , out)
    return out

def parse_std_import(cleaned: str):
    return re.match(r'^import\s+<([^>]+)>\s*(?:as\s+([A-Za-z_]\w*))?\s*;\s*$', cleaned)


def parse_user_import(cleaned: str):
    return re.match(r'^import\s+"([^"]+)"\s*(?:as\s+([A-Za-z_]\w*))?\s*;\s*$', cleaned)



def load_user_module_as_alias(
    input_path: Path,
    alias: str,
    seen: Set[Path],
    stack: List[Path],
    seen_aliases: Set[Tuple[Path, str]],
) -> str:
    """Load a user .zy file as a local namespace.

    Example:
        import "list.zy" as list;
        list.say_hi();

    The backend rewrites this to a stable C symbol such as:
        zlmod_list_say_hi();

    In v0.1.30, namespacing applies to top-level functions in the imported
    file. Struct types remain global for now, which keeps the rule simple and
    avoids renaming data layouts behind the user's back.
    """
    path = input_path.resolve()
    key = (path, alias)
    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if key in seen_aliases:
        return ""
    if not path.exists():
        raise FileNotFoundError(path)

    seen_aliases.add(key)
    stack.append(path)

    local_aliases: Dict[str, str] = {}
    dependency_pieces: List[str] = []
    body_lines: List[str] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        user_import = parse_user_import(cleaned)
        std_import = parse_std_import(cleaned)
        if user_import:
            import_path = resolve_import_path(path.parent, user_import.group(1))
            import_alias = user_import.group(2)
            if import_alias:
                local_aliases[import_alias] = module_prefix(import_alias)
                dependency_pieces.append(load_user_module_as_alias(import_path, import_alias, seen, stack, seen_aliases))
            else:
                # A plain user import inside a namespaced module is still global,
                # preserving the original v0.1 behavior.
                dependency_pieces.append(load_source_with_imports(import_path, seen=seen, stack=stack, seen_aliases=seen_aliases))
            continue
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            std_alias = std_import.group(2) or imported_name
            local_aliases[std_alias] = module_prefix(imported_name)
            if imported_name == "prelude":
                dependency_pieces.append(load_prelude_into_aliases(import_path, local_aliases, seen, stack))
            else:
                dependency_pieces.append(load_std_module(import_path, imported_name, seen, stack))
            continue
        if cleaned.startswith("import "):
            raise ZyenError(f"{path}:{line_no}: import must look like `import \"file.zy\";`, `import \"file.zy\" as name;`, or `import <std/name>;`")
        body_lines.append(replace_module_calls(raw, local_aliases))

    stack.pop()
    body = "\n".join(body_lines) + "\n"
    namespaced_body = prefix_module_body(body, alias)
    return "\n".join(piece for piece in dependency_pieces if piece) + "\n" + namespaced_body

def load_std_module(
    input_path: Path,
    module_name: str,
    seen: Set[Path],
    stack: List[Path],
) -> str:
    """Load and namespace a standard-library module.

    Standard library functions never enter the global namespace. A caller uses
    `math.abs_int(...)`; the backend compiles that as `zlmod_math_abs_int(...)`.
    """
    path = input_path.resolve()
    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if path in seen:
        return ""
    if not path.exists():
        raise FileNotFoundError(path)

    seen.add(path)
    stack.append(path)

    local_aliases: Dict[str, str] = {}
    dependency_pieces: List[str] = []
    body_lines: List[str] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        user_import = parse_user_import(cleaned)
        std_import = parse_std_import(cleaned)
        if user_import:
            import_path = resolve_import_path(path.parent, user_import.group(1))
            # User imports inside std are rare; keep them global for now.
            dependency_pieces.append(load_source_with_imports(import_path, seen=seen, stack=stack))
            continue
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            alias = std_import.group(2) or imported_name
            local_aliases[alias] = module_prefix(imported_name)

            # `prelude` is only an aggregator; importing it inside a module simply
            # loads its member modules and exposes their aliases locally.
            if imported_name == "prelude":
                dependency_pieces.append(load_prelude_into_aliases(import_path, local_aliases, seen, stack))
            else:
                dependency_pieces.append(load_std_module(import_path, imported_name, seen, stack))
            continue
        if cleaned.startswith("import "):
            raise ZyenError(f"{path}:{line_no}: import must look like `import \"file.zy\";` or `import <std/name>;`")
        body_lines.append(replace_module_calls(raw, local_aliases))

    stack.pop()
    body = "\n".join(body_lines) + "\n"
    namespaced_body = prefix_module_body(body, module_name)
    return "\n".join(piece for piece in dependency_pieces if piece) + "\n" + namespaced_body


def load_prelude_into_aliases(
    input_path: Path,
    aliases: Dict[str, str],
    seen: Set[Path],
    stack: List[Path],
) -> str:
    """Load std/prelude as an alias aggregator.

    `import <std/prelude>;` does not create `prelude.foo()`; it makes the
    bundled modules available as `math.foo()`, `check.foo()`, etc.
    """
    path = input_path.resolve()
    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if not path.exists():
        raise FileNotFoundError(path)

    stack.append(path)
    pieces: List[str] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        std_import = parse_std_import(cleaned)
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            if imported_name == "prelude":
                raise ZyenError(f"{path}:{line_no}: prelude cannot import itself")
            alias = std_import.group(2) or imported_name
            aliases[alias] = module_prefix(imported_name)
            pieces.append(load_std_module(import_path, imported_name, seen, stack))
            continue
        if cleaned and not cleaned.startswith("//"):
            raise ZyenError(f"{path}:{line_no}: prelude may only contain std imports")
    stack.pop()
    return "\n".join(piece for piece in pieces if piece) + "\n"


def load_source_with_imports(
    input_path: Path,
    seen: Optional[Set[Path]] = None,
    stack: Optional[List[Path]] = None,
    seen_aliases: Optional[Set[Tuple[Path, str]]] = None,
) -> str:
    """Load a .zy file and expand imports.

    - `import "file.zy";` is a user import and remains global.
    - `import "file.zy" as mod;` is namespaced; call functions as `mod.fn(...)`.
    - `import <std/math>;` is namespaced; call functions as `math.abs_int(...)`.
    - `import <std/math> as m;` gives an alias, e.g. `m.abs_int(...)`.
    - `import <std/prelude>;` loads common std modules and exposes their module
      namespaces (`math.*`, `check.*`, `control.*`, `motor.*`).
    """
    seen = seen if seen is not None else set()
    stack = stack if stack is not None else []
    seen_aliases = seen_aliases if seen_aliases is not None else set()
    path = input_path.resolve()

    if path in stack:
        chain = " -> ".join(p.name for p in stack + [path])
        raise ZyenError(f"circular import detected: {chain}")
    if path in seen:
        return ""
    if not path.exists():
        raise FileNotFoundError(path)

    seen.add(path)
    stack.append(path)
    aliases: Dict[str, str] = {}
    pieces: List[str] = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        cleaned = strip_comment(raw).strip()
        user_import = parse_user_import(cleaned)
        std_import = parse_std_import(cleaned)
        if user_import:
            import_path = resolve_import_path(path.parent, user_import.group(1))
            import_alias = user_import.group(2)
            if import_alias:
                aliases[import_alias] = module_prefix(import_alias)
                pieces.append(f"// begin import {import_path} as {import_alias}")
                pieces.append(load_user_module_as_alias(import_path, import_alias, seen, stack, seen_aliases))
                pieces.append(f"// end import {import_path} as {import_alias}")
            else:
                pieces.append(f"// begin import {import_path}")
                pieces.append(load_source_with_imports(import_path, seen=seen, stack=stack, seen_aliases=seen_aliases))
                pieces.append(f"// end import {import_path}")
            continue
        if std_import:
            import_path = resolve_std_import_path(std_import.group(1))
            imported_name = module_name_from_path(import_path)
            alias = std_import.group(2) or imported_name

            if imported_name == "prelude":
                pieces.append(f"// begin std prelude {import_path}")
                pieces.append(load_prelude_into_aliases(import_path, aliases, seen, stack))
                pieces.append(f"// end std prelude {import_path}")
            else:
                aliases[alias] = module_prefix(imported_name)
                pieces.append(f"// begin std import {import_path} as {alias}")
                pieces.append(load_std_module(import_path, imported_name, seen, stack))
                pieces.append(f"// end std import {import_path}")
            continue
        if cleaned.startswith("import "):
            raise ZyenError(f"{path}:{line_no}: import must look like `import \"file.zy\";` or `import <std/name>;`")
        pieces.append(replace_module_calls(raw, aliases))

    stack.pop()
    return "\n".join(pieces) + "\n"

def build_file(input_path: Path, output_path: Path) -> None:
    source = load_source_with_imports(input_path)
    c_code = transpile(source)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(c_code, encoding="utf-8")


def compile_c(c_path: Path, exe_path: Path) -> None:
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["gcc", "-std=c11", "-Wall", "-Wextra", str(c_path), "-o", str(exe_path)]
    subprocess.run(cmd, check=True)


def run_source(input_path: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        c_path = tmp_path / (input_path.stem + ".c")
        exe_name = input_path.stem + (".exe" if sys.platform.startswith("win") else "")
        exe_path = tmp_path / exe_name
        build_file(input_path, c_path)
        compile_c(c_path, exe_path)
        result = subprocess.run([str(exe_path)], check=False)
        return result.returncode


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="zyen", description="ZyenLang v0.1.48 transpiler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="transpile .zy to .c")
    p_build.add_argument("input")
    p_build.add_argument("-o", "--output", default=None)
    p_build.add_argument("--exe", default=None, help="also compile generated C to executable")

    p_run = sub.add_parser("run", help="transpile, compile, and run .zy")
    p_run.add_argument("input")

    p_check = sub.add_parser("check", help="only validate/transpile in memory")
    p_check.add_argument("input")

    args = parser.parse_args(argv)
    try:
        input_path = Path(args.input)
        if args.cmd == "build":
            if args.output:
                requested_output = Path(args.output)
            else:
                requested_output = input_path.with_suffix(".c")

            # Simple mode:
            #   -o main.c     -> generate C file
            #   -o main.exe   -> generate temporary C, then compile executable
            #   -o main       -> generate C file named main, unless --exe is also used
            if requested_output.suffix.lower() == ".exe":
                with tempfile.TemporaryDirectory() as tmp:
                    temp_c = Path(tmp) / (input_path.stem + ".c")
                    build_file(input_path, temp_c)
                    compile_c(temp_c, requested_output)
                print(f"compiled: {requested_output}")
                return 0

            output_path = requested_output
            build_file(input_path, output_path)
            print(f"generated: {output_path}")
            if args.exe:
                compile_c(output_path, Path(args.exe))
                print(f"compiled: {args.exe}")
            return 0
        if args.cmd == "run":
            return run_source(input_path)
        if args.cmd == "check":
            transpile(load_source_with_imports(input_path))
            print("OK")
            return 0
    except subprocess.CalledProcessError as e:
        print(f"C compiler failed: {e}", file=sys.stderr)
        return 2
    except ZyenError as e:
        print(f"Zyen error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"file not found: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
