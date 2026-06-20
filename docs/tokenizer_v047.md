# ZyenLang v0.1.47 Logical Line Assembler

v0.1.47 replaces the old list-literal-only line joiner with a tokenizer-aware logical line assembler.

The compiler still parses one logical statement at a time, but a logical statement may now span multiple physical lines.

## Supported multiline forms

### Multiline function signature

```zy
fn add(
    a: int,
    b: int
) -> int {
    return a + b;
}
```

### Multiline function call

```zy
let n: int = add(
    10,
    20
);
```

### Multiline method call

```zy
box.add_many(
    "x",
    "y"
);
```

### Multiline if condition

```zy
if (
    value > 10
) {
    print("large");
}
```

### Multiline for header

```zy
for (
    let i: int = 0;
    i < 10;
    i += 1
) {
    print(i);
}
```

### Multiline list literal

```zy
let xs: List = [
    "a",
    "b",
    "c"
];
```

## Implementation notes

`clean_lines()` now tracks:

- string state
- escape state
- parenthesis depth
- bracket depth
- expression-brace depth
- block header completion
- standalone block closing braces

It removes `//` comments only when outside strings, then joins physical lines until a logical boundary appears:

- `;` at zero delimiter depth
- block header ending in `{`
- standalone `}`
- `} else {` / `} else if (...) {`

The compiler still intentionally does not support two statements on one physical line. ZyenLang style remains one statement per logical statement.
