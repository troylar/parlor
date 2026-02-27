# Parameterized Queries Only

All database queries must use parameterized placeholders.

- Use `?` placeholders for SQLite, `%s` for PostgreSQL
- Never concatenate or f-string user input into SQL
- Validate column names against allowlists
- Use ORM methods when available
