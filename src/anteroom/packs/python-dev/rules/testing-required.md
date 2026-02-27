# Testing Required

All new code must have corresponding tests.

- New modules require a corresponding `test_<module>.py` file
- New public functions need at least one test covering the happy path
- Bug fixes must include a regression test
- Use pytest fixtures, not setUp/tearDown
- Mock external dependencies in unit tests
- Minimum coverage target: 80%
