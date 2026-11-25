"""Collect hash-pinned token endpoints and recheck their canonical references.

One provider supplies chain ID, numbered headers, EIP-1898 reads, and optional finality heads.
Canonical headers are checked again after collection. Finality remains an endpoint assertion,
especially on L2s: it is not an independent consensus or settlement proof.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from oracle41_open.core.models import Chain, ProviderError, ProviderResponseError
from oracle41_open.core.models.block_reference import BlockReference, BlockReferenceChangedError
from oracle41_open.core.models.historical_price import time_text, utc_time
from oracle41_open.core.services.protocol_collection import call_data, decode_call_result
from oracle41_open.providers.block_reader import BlockReaderPool, BlockStateReader


@dataclass(frozen=True)
class VerifiedHoldingsAnchors:
    opening: dict[str, Any]
    closing: dict[str, Any]
    finality: dict[str, Any]


def reference_document(reference: BlockReference) -> dict[str, Any]:
    return {
        "chain": reference.chain.value,
        "number": reference.number,
        "hash": reference.block_hash,
        "parent_hash": reference.parent_hash,
        "timestamp": time_text(reference.timestamp),
        "source_provider": reference.source_provider,
        "observed_at": time_text(reference.fetched_at),
    }


class HistoricalBlockService:
    def __init__(
        self, source: BlockReaderPool, now_func: Callable[[], datetime] | None = None
    ) -> None:
        self._source = source
        self._now = now_func or (lambda: datetime.now(tz=UTC))

    def collect(
        self,
        chain: Chain,
        wallet: str,
        token: str,
        opening_block: int,
        closing_block: int,
        require_finalized: bool = False,
    ) -> VerifiedHoldingsAnchors:
        return self._source.with_block_reader(
            chain,
            lambda reader: self._collect(
                reader, chain, wallet, token, opening_block, closing_block, require_finalized
            ),
        )

    def _collect(
        self,
        reader: BlockStateReader,
        chain: Chain,
        wallet: str,
        token: str,
        opening_block: int,
        closing_block: int,
        require_finalized: bool,
    ) -> VerifiedHoldingsAnchors:
        self._check_chain(reader, chain)
        opening = self._header(reader, chain, opening_block)
        closing = self._header(reader, chain, closing_block, opening.source_provider)
        if opening.timestamp > closing.timestamp or closing.timestamp > utc_time(self._now()):
            raise ProviderResponseError(
                "Historical block timestamps are inconsistent or in the future."
            )
        if closing.number == opening.number and not _same_reference(opening, closing):
            raise BlockReferenceChangedError("The same block returned different references.")
        if closing.number == opening.number + 1 and closing.parent_hash != opening.block_hash:
            raise BlockReferenceChangedError(
                "Adjacent blocks do not share the expected parent reference."
            )
        first = self._balance(reader, opening, wallet, token)
        last = self._balance(reader, closing, wallet, token)
        finality = self._finality(reader, chain, closing, opening.source_provider)
        self._recheck(reader, opening)
        self._recheck(reader, closing)
        self._check_chain(reader, chain)
        if require_finalized and finality["status"] != "finalized_by_endpoint":
            raise ProviderResponseError(
                "The closing block is not reported finalized by this endpoint."
            )
        return VerifiedHoldingsAnchors(first, last, finality)

    @staticmethod
    def _check_chain(reader: BlockStateReader, chain: Chain) -> None:
        if reader.get_chain_id(chain) != chain.network.chain_id:
            raise ProviderResponseError(
                "The JSON-RPC endpoint chain ID does not match the selected network."
            )

    @staticmethod
    def _header(
        reader: BlockStateReader, chain: Chain, block: int | str, source: str | None = None
    ) -> BlockReference:
        reference = reader.get_block_reference(chain, block)
        if (
            reference.chain is not chain
            or (isinstance(block, int) and reference.number != block)
            or (source is not None and reference.source_provider != source)
        ):
            raise ProviderResponseError(
                "Historical block reference changed source or request context."
            )
        return reference

    def _recheck(self, reader: BlockStateReader, original: BlockReference) -> None:
        current = self._header(reader, original.chain, original.number, original.source_provider)
        if not _same_reference(original, current):
            raise BlockReferenceChangedError(
                "Canonical block identity changed during collection. Retry after the endpoint stabilizes."
            )

    @staticmethod
    def _balance(
        reader: BlockStateReader, reference: BlockReference, wallet: str, token: str
    ) -> dict[str, Any]:
        values = []
        fetched = reference.fetched_at
        for signature, args, types, output in (
            ("balanceOf(address)", (wallet,), ("address",), "uint256"),
            ("decimals()", (), (), "uint8"),
        ):
            result = reader.read_contract_at_hash(
                token, call_data(signature, types, args), reference
            )
            if (
                result.chain is not reference.chain
                or result.contract_address.lower() != token
                or result.block_number != reference.number
                or result.block_hash.lower() != reference.block_hash
                or result.source_provider != reference.source_provider
            ):
                raise ProviderResponseError(
                    "Hash-pinned contract result does not match its block context."
                )
            values.append(
                decode_call_result(result.data, (output,), "Hash-pinned historical token read")[0]
            )
            fetched = max(fetched, utc_time(result.fetched_at))
        return {
            "block_number": reference.number,
            "raw_balance": str(values[0]),
            "decimals": int(str(values[1])),
            "source_provider": reference.source_provider,
            "observed_at": time_text(fetched),
            "method": "balanceOf(address),decimals()",
            "block_reference": "eip1898_hash_require_canonical",
            "header": reference_document(reference),
        }

    def _finality(
        self, reader: BlockStateReader, chain: Chain, closing: BlockReference, source: str
    ) -> dict[str, Any]:
        heads: dict[str, Any] = {}
        references = {}
        for tag in ("finalized", "safe"):
            try:
                reference = reader.get_block_reference(chain, tag)
            except BlockReferenceChangedError:
                raise
            except ProviderError as error:
                heads[tag] = {"availability": "unavailable", "error_type": type(error).__name__}
                continue
            if reference.chain is not chain or reference.source_provider != source:
                raise ProviderResponseError("Finality header belongs to another chain or source.")
            if (
                reference.timestamp > utc_time(self._now())
                or (reference.number >= closing.number and reference.timestamp < closing.timestamp)
                or (reference.number <= closing.number and reference.timestamp > closing.timestamp)
            ):
                raise ProviderResponseError("Finality header has inconsistent timing.")
            self._recheck(reader, reference)
            references[tag] = reference
            heads[tag] = {"availability": "observed", "header": reference_document(reference)}
        if (
            "safe" in references
            and "finalized" in references
            and references["safe"].number < references["finalized"].number
        ):
            raise ProviderResponseError("The endpoint's safe head is behind its finalized head.")
        status = "unknown"
        for tag, label in (("finalized", "finalized_by_endpoint"), ("safe", "safe_by_endpoint")):
            if tag in references and closing.number <= references[tag].number:
                status = label
                break
        if status == "unknown" and references:
            status = "not_reported_safe"
        return {
            "status": status,
            "heads": heads,
            "source_provider": source,
            "meaning": "endpoint_assertion_not_independent_consensus_or_L1_settlement_proof",
        }

    def revalidate(self, chain: Chain, headers: list[dict[str, Any]]) -> dict[str, Any]:
        """Compare saved references with a current endpoint without altering the old report."""
        if len(headers) != 2:
            raise ProviderResponseError(
                "Reference checks require saved opening and closing headers."
            )

        def operation(reader: BlockStateReader) -> dict[str, Any]:
            self._check_chain(reader, chain)
            observations = []
            current_refs: list[BlockReference] = []
            for saved in headers:
                current = self._header(reader, chain, int(saved["number"]))
                if current.timestamp > utc_time(self._now()):
                    raise ProviderResponseError("Current block reference has a future timestamp.")
                if current_refs and current.source_provider != current_refs[0].source_provider:
                    raise ProviderResponseError("Reference recheck changed source.")
                current_refs.append(current)
                observations.append(
                    {
                        "saved": saved,
                        "current": reference_document(current),
                        "matches": current.block_hash == saved["hash"]
                        and time_text(current.timestamp) == saved["timestamp"]
                        and current.parent_hash == saved["parent_hash"],
                    }
                )
            finality = self._finality(
                reader, chain, current_refs[-1], current_refs[0].source_provider
            )
            for current in current_refs:
                self._recheck(reader, current)
            self._check_chain(reader, chain)
            return {
                "status": "references_match_at_check"
                if all(item["matches"] for item in observations)
                else "changed_reference",
                "observations": observations,
                "finality": finality,
                "limitation": "A changed reference can indicate reorganization or endpoint disagreement; no history was rewritten.",
            }

        return self._source.with_block_reader(chain, operation)


def _same_reference(left: BlockReference, right: BlockReference) -> bool:
    return (left.chain, left.number, left.block_hash, left.parent_hash, left.timestamp) == (
        right.chain,
        right.number,
        right.block_hash,
        right.parent_hash,
        right.timestamp,
    )
