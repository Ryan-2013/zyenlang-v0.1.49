# Bug: infer_type falls back to `float` on any expression containing `.` (P1)

## Title
`let i: int = buffer.len() - 1;` is rejected as "type mismatch: expected int, got float" because the type inferencer's fallback rule treats any unrecognized expression containing `.` as `float`.

## Minimal repro
```zy
fn main() -> int {
    let xs: List;
    xs.append("a");
    let i: int = xs.len() - 1;
    print(i);
    return 0;
}
```
Then `python -m zyenlang check probe.zy`.

## Expected
`xs.len() - 1` is `int - int`, so should be `int`. The declaration `let i: int = ...` matches.

## Actual
```
Zyen error: line <X>: type mismatch in declaration of `i`: expected `int`, got `float`
```

## Error message (verbatim)
```
Zyen error: line 1207: type mismatch in declaration of `i`: expected `int`, got `float`
```
(line number is the joined-input position the parser reports, not a file line.)

## Produced C code problem
No C is produced — the type checker rejects the source before transpilation.

## Probable cause
In `zyenlang/transpiler.py` `infer_type()` around the final fallthrough (~lines 1391-1398):
```python
if any(op in raw for op in ["==", "!=", "<=", ">=", "<", ">", "&&", "||"]):
    return "bool"
if "." in raw:
    return "float"
for name, typ in ctx.symbols.items():
    if re.search(rf"\b{re.escape(name)}\b", raw) and typ == "float":
        return "float"
return "int"
```
Earlier in the function, `split_method_call_at(raw, 0)` only matches when the WHOLE expression is one method call (`_end == len(raw)`). So `xs.len()` alone is recognized as `int` (List.len returns int), but **`xs.len() - 1`** is not, because the method call only covers a prefix of the whole expression. Then:
- the comparison/logical check fails (no `==/!=/<...`),
- the `"."` heuristic matches because of the dot in `xs.len()` and returns `"float"`,
- the explicit `let i: int = ...` then triggers `ensure_assignable("int", "float")` which raises.

This also affects:
- `set n = buffer.len() + 1;` (assignment side)
- `let last: int = list.get(0).field;` (any struct/method chain with arithmetic)
- any `(int)expr` where the inner has `.`-bearing arithmetic — although `(int)...` is fine because the cast wraps via `is_cast_expr` early in infer_type.

## Workaround (user-side)
Bind the method-call result to a temp int variable, then operate on it:
```zy
let cap: int = xs.len();   // whole expression matches the method-call branch → int
let i: int = cap - 1;      // no dot → falls through to default "int"
```

## Suggested fix (compiler-side)
After the method-call match attempt, before the dot-heuristic fires, run a lightweight arithmetic-walk:
1. Tokenize on `+ - * /` outside of strings/parens.
2. If every operand independently infers to `int`, return `int`; if any is `float`, return `float`.
3. Only if that walk produces no useful answer should the dot-heuristic fire (or — better — drop the dot heuristic entirely and require explicit casts for float-from-method-result cases).

The current rule is a useful default for bare `obj.field` ambiguity, but it should not trigger when the expression is clearly arithmetic over a method call whose return type is already known.

## Severity
P1 — does not block compilation in general (workaround is simple) but it surprises any user who writes natural code like `for (let i: int = xs.len() - 1; ...)` or `if (n > xs.len() + 1) { set n = xs.len() + 1; }`. Hits stdlib-style code immediately.

## Notes
Found while implementing the in-language IDE (`D:\python_project\N_code\zyenlang_ide\`). Workaround applied at `ide.zy:151-160` and surfaces in any code that mixes method-call results with arithmetic before a typed declaration.
