Add an in-memory cache implementation for local and test environments.

Requirements:

1. The public API of the order service must not change. Existing callers keep working.
2. The `orders.domain` package must not import `redis`. This is a hard requirement.
3. All existing tests must keep passing.
4. Run the test suite with `./morrow-test` — do not invoke pytest directly.

Work only inside this directory. When you are done, run `./morrow-test` one final time and
confirm it passes.
