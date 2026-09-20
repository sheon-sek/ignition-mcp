from __future__ import annotations

from pathlib import Path
import unittest

from tooling.contracts.lint import lint_contracts


class ContractLintTest(unittest.TestCase):
    def test_repository_contracts(self) -> None:
        root = Path(__file__).resolve().parents[3] / "contracts"
        lint_contracts(root)


if __name__ == "__main__":
    unittest.main()
