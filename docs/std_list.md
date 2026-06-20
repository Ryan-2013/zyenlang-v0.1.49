# std/list

`std/list` contains helper functions for the built-in `List` type.

Import:

```zy
import <std/list>;
```

Functions:

- `list.len(xs) -> int`
- `list.is_empty(xs) -> bool`
- `list.join_str(xs, sep) -> str`
- `list.contains_str(xs, value) -> bool`
- `list.index_of_str(xs, value) -> int`
- `list.contains_int(xs, value) -> bool`
- `list.index_of_int(xs, value) -> int`
- `list.copy(xs) -> List`
- `list.reverse(xs) -> List`
- `list.slice(xs, start, count) -> List`
- `list.without_at(xs, index) -> List`
- `list.append_all(target, source) -> void`

`Any` remains internal to the List runtime. User-facing helper names are typed where values must leave the List.
