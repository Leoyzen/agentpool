# Python 3.12+ Feature Usage

The project targets Python 3.13+ and **SHOULD** leverage 3.12+ features where they improve clarity or safety:

- **PEP 695 generics**: `def func[T](x: T) -> T:` instead of `TypeVar`
- **`type` statement**: `type JsonResult = dict[str, str]` instead of `TypeAlias`
- **`override` decorator** (PEP 698) for method overrides; **`Self`** for factory return types
- **`asyncio.TaskGroup`** for structured concurrency; **`asyncio.timeout()`** over `wait_for()`
- **`match/case`** over `if/elif` for variant dispatch (events, tool results)
- **Walrus operator `:=`** for assignment in conditions: `if (n := len(data)) > 10:`
- **`itertools.batched()`** (3.12+) for chunking; `functools.cache` / `cached_property` for memoization

!!! warning "Deprecated patterns"
    `typing.TypeVar`, `typing.TypeAlias`, `asyncio.wait_for()` — use the modern alternatives above.
