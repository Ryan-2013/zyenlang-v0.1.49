# std/stack, std/queue, std/map, std/set

ZyenLang v0.1.47 adds small collection helpers implemented in pure ZyenLang.

## std/stack

```zy
import <std/stack>;
let s: Stack = stack.new();
s.push_str("a");
print(s.pop_str());
```

Typed push/pop methods:

- `push_str`, `push_int`, `push_float`, `push_bool`
- `pop_str`, `pop_int`, `pop_float`, `pop_bool`
- `peek_str`, `peek_int`
- `len`, `is_empty`, `clear`

## std/queue

```zy
import <std/queue>;
let q: Queue = queue.new();
q.push_str("a");
print(q.pop_str());
```

Queue is FIFO. Current implementation rebuilds the backing List on each pop.

## std/map

`std/map` provides `StringMap`, a string-key/string-value map backed by two Lists.

```zy
import <std/map>;
let m: StringMap = map.new();
m.put("name", "ZyenLang");
print(m.get("name", "none"));
```

Methods:

- `put(key, value)`
- `get(key, default_value)`
- `has(key)`
- `remove(key)`
- `keys()`
- `values()`
- `len()`
- `is_empty()`
- `clear()`

## std/set

`std/set` provides `StringSet`, a string set backed by List.

```zy
import <std/set>;
let tags: StringSet = set.new();
tags.add("robotics");
print(tags.has("robotics"));
```

Methods:

- `add(value)`
- `has(value)`
- `remove(value)`
- `values()`
- `len()`
- `is_empty()`
- `clear()`
