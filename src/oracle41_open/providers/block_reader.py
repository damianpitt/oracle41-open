"""Define read-only block identity and canonical hash-state capabilities.

Historical collection selects one reader for its entire operation. A source may retry the whole
operation on another reader after an ordinary failure, but changed canonical references must be
reported rather than hidden by failover.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

from oracle41_open.core.models import Chain
from oracle41_open.core.models.block_reference import BlockReference, HashPinnedContractRead

_Result = TypeVar("_Result")


class BlockStateReader(Protocol):
    def get_chain_id(self, chain: Chain) -> int: ...
    def get_block_reference(self, chain: Chain, block: int | str) -> BlockReference: ...
    def read_contract_at_hash(
        self, contract_address: str, call_data: str, reference: BlockReference
    ) -> HashPinnedContractRead: ...


class BlockReaderPool(Protocol):
    def with_block_reader(
        self, chain: Chain, operation: Callable[[BlockStateReader], _Result]
    ) -> _Result: ...
