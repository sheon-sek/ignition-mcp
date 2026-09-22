from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tooling.contracts.lint import ContractError, lint_contracts

REPO_CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"


class ContractLintTest(unittest.TestCase):
    def test_repository_contracts(self) -> None:
        lint_contracts(REPO_CONTRACTS)


class ContractLintDriftTest(unittest.TestCase):
    """Each case mutates one frozen expectation and requires the linter to reject it."""

    def _copy(self, temp_root: str) -> Path:
        target = Path(temp_root) / "contracts"
        shutil.copytree(REPO_CONTRACTS, target)
        return target

    def _edit_json(self, path: Path, mutate) -> None:  # type: ignore[no-untyped-def]
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_mutation_class_on_rest_tool_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "tools/rest/project_export.contract.json",
                lambda doc: doc.update(mutationClass="CONFIG_MUTATION"),
            )
            with self.assertRaisesRegex(ContractError, "read-only"):
                lint_contracts(contracts)

    def test_missing_phase3_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            (contracts / "tools/rest/artifact_list.contract.json").unlink()
            with self.assertRaises(ContractError):
                lint_contracts(contracts)

    def test_unlisted_rest_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            shutil.copyfile(
                contracts / "tools/rest/artifact_info.contract.json",
                contracts / "tools/rest/project_delete.contract.json",
            )
            with self.assertRaisesRegex(ContractError, "inventory drift"):
                lint_contracts(contracts)

    def test_sensitive_export_gate_removal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "tools/rest/tag_config_export.contract.json",
                lambda doc: doc.update(deploymentGate="IGNITION_MCP_ANYTHING"),
            )
            with self.assertRaisesRegex(ContractError, "gate/audit drift"):
                lint_contracts(contracts)

    def test_artifact_ref_token_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "shared/artifact-ref.schema.json",
                lambda doc: doc["properties"]["download"]["properties"].update(downloadToken={"type": "string"}),
            )
            with self.assertRaisesRegex(ContractError, "never a token"):
                lint_contracts(contracts)

    def test_artifact_delete_phase3_exposure_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "shared/artifact-classes.json",
                lambda doc: doc.update(publicDeleteToolPhase3=True),
            )
            with self.assertRaisesRegex(ContractError, "Phase 4"):
                lint_contracts(contracts)

    def test_transaction_state_order_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "shared/project-transaction-states.json",
                lambda doc: doc.update(states=list(reversed(doc["states"]))),
            )
            with self.assertRaisesRegex(ContractError, "state order drift"):
                lint_contracts(contracts)

    def test_recovery_lock_release_set_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "shared/project-transaction-states.json",
                lambda doc: doc["recoveryLockReleasedOn"].append("OUTCOME_UNKNOWN"),
            )
            with self.assertRaisesRegex(ContractError, "release set drift"):
                lint_contracts(contracts)

    def test_missing_dispatch_classification_declaration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "tools/rest/project_import.contract.json",
                lambda doc: doc["transaction"].pop("dispatchBoundary"),
            )
            with self.assertRaisesRegex(ContractError, "dispatch classification"):
                lint_contracts(contracts)

    def test_dispatch_classification_value_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "tools/rest/project_import.contract.json",
                lambda doc: doc["transaction"]["dispatchBoundary"]["values"].append("maybe_later"),
            )
            with self.assertRaisesRegex(ContractError, "dispatch classification"):
                lint_contracts(contracts)

    def test_missing_import_dispatched_declaration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "tools/rest/project_import.contract.json",
                lambda doc: doc["transaction"].update(importDispatched=""),
            )
            with self.assertRaisesRegex(ContractError, "importDispatched"):
                lint_contracts(contracts)

    def test_error_taxonomy_growth_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "shared/error-codes.json",
                lambda doc: doc["codes"].append("artifact_expired"),
            )
            with self.assertRaisesRegex(ContractError, "taxonomy drift"):
                lint_contracts(contracts)

    def test_tag_import_target_match_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "tools/rest/tag_config_import.contract.json",
                lambda doc: doc["targetId"].update(match="exact"),
            )
            with self.assertRaisesRegex(ContractError, "Target match rule"):
                lint_contracts(contracts)

    def test_tag_import_reserved_provider_removal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            contracts = self._copy(temp)
            self._edit_json(
                contracts / "tools/rest/tag_config_import.contract.json",
                lambda doc: doc.pop("reservedTagProviders"),
            )
            with self.assertRaisesRegex(ContractError, "reserved Tag providers"):
                lint_contracts(contracts)


if __name__ == "__main__":
    unittest.main()
