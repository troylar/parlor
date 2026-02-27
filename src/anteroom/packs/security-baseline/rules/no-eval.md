# No eval() or exec()

Never use `eval()`, `exec()`, `compile()`, or `ast.literal_eval()` with user-controlled input.

- No dynamic code execution from external sources
- No `pickle.loads()` on untrusted data
- No `yaml.load()` without `Loader=yaml.SafeLoader`
- Use structured parsing instead of dynamic evaluation
