from eth_abi.abi import encode as abi_encode

from oracle41_open.core.models import RawTransactionLog
from oracle41_open.core.models.decoding import DecodeStatus, SignatureSourceKind
from oracle41_open.core.services.abi_decoder import StandardABIDecoder

_ALICE = "0x1111111111111111111111111111111111111111"
_BOB = "0x2222222222222222222222222222222222222222"
_OPERATOR = "0x3333333333333333333333333333333333333333"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_TRANSFER_SINGLE_TOPIC = (
    "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
)


def test_decodes_erc20_transfer_call_with_bundled_provenance() -> None:
    payload = abi_encode(("address", "uint256"), (_BOB, 42)).hex()

    decoded = StandardABIDecoder().decode_call("0xa9059cbb" + payload)

    assert decoded.status is DecodeStatus.DECODED
    assert decoded.canonical_signature == "transfer(address,uint256)"
    assert [(item.name, item.value) for item in decoded.arguments] == [
        ("to", _BOB),
        ("value", "42"),
    ]
    assert decoded.provenance is not None
    assert decoded.provenance.source_kind is SignatureSourceKind.BUNDLED_STANDARD
    assert decoded.provenance.is_verified is True


def test_decodes_erc1155_batch_transfer_dynamic_arguments() -> None:
    payload = abi_encode(
        ("address", "address", "uint256[]", "uint256[]", "bytes"),
        (_ALICE, _BOB, (7, 8), (2, 3), b"\x12\x34"),
    ).hex()

    decoded = StandardABIDecoder().decode_call("0x2eb2c2d6" + payload)

    assert decoded.status is DecodeStatus.DECODED
    assert decoded.canonical_signature == (
        "safeBatchTransferFrom(address,address,uint256[],uint256[],bytes)"
    )
    assert [item.value for item in decoded.arguments] == [
        _ALICE,
        _BOB,
        "[7, 8]",
        "[2, 3]",
        "0x1234",
    ]


def test_unknown_and_malformed_calls_are_distinct() -> None:
    decoder = StandardABIDecoder()

    unknown = decoder.decode_call("0x12345678deadbeef")
    malformed = decoder.decode_call("0xa9059cbbdeadbeef")

    assert unknown.status is DecodeStatus.UNKNOWN
    assert unknown.selector == "0x12345678"
    assert unknown.provenance is None
    assert malformed.status is DecodeStatus.MALFORMED
    assert malformed.canonical_signature == "transfer(address,uint256)"
    assert malformed.provenance is not None


def test_transfer_topic_shape_distinguishes_erc20_from_erc721() -> None:
    decoder = StandardABIDecoder()
    erc20 = decoder.decode_event(
        _log(
            topics=(_TRANSFER_TOPIC, _address_topic(_ALICE), _address_topic(_BOB)),
            data="0x" + abi_encode(("uint256",), (99,)).hex(),
        )
    )
    erc721 = decoder.decode_event(
        _log(
            topics=(
                _TRANSFER_TOPIC,
                _address_topic(_ALICE),
                _address_topic(_BOB),
                _uint_topic(1234),
            ),
            data="0x",
        )
    )

    assert erc20.status is DecodeStatus.DECODED
    assert erc20.standard == "ERC-20"
    assert erc20.arguments[-1].value == "99"
    assert erc721.status is DecodeStatus.DECODED
    assert erc721.standard == "ERC-721"
    assert erc721.arguments[-1].value == "1234"


def test_decodes_erc1155_transfer_single_event() -> None:
    decoded = StandardABIDecoder().decode_event(
        _log(
            topics=(
                _TRANSFER_SINGLE_TOPIC,
                _address_topic(_OPERATOR),
                _address_topic(_ALICE),
                _address_topic(_BOB),
            ),
            data="0x" + abi_encode(("uint256", "uint256"), (7, 12)).hex(),
        )
    )

    assert decoded.status is DecodeStatus.DECODED
    assert decoded.standard == "ERC-1155"
    assert [(item.name, item.value) for item in decoded.arguments] == [
        ("operator", _OPERATOR),
        ("from", _ALICE),
        ("to", _BOB),
        ("id", "7"),
        ("value", "12"),
    ]


def test_unknown_and_malformed_events_are_distinct_and_deterministic() -> None:
    decoder = StandardABIDecoder()
    unknown_log = _log(topics=("0x" + "12" * 32,), data="0x", log_index=5)
    malformed_log = _log(topics=(_TRANSFER_TOPIC,), data="0x", log_index=6)

    unknown = decoder.decode_event(unknown_log)
    malformed = decoder.decode_event(malformed_log)

    assert unknown.status is DecodeStatus.UNKNOWN
    assert unknown.log_index == 5
    assert malformed.status is DecodeStatus.MALFORMED
    assert malformed.log_index == 6
    assert decoder.decode_event(malformed_log) == malformed


def _log(
    *,
    topics: tuple[str, ...],
    data: str,
    log_index: int = 0,
) -> RawTransactionLog:
    return RawTransactionLog(
        log_index=log_index,
        address="0x4444444444444444444444444444444444444444",
        topics=topics,
        data=data,
        removed=False,
    )


def _address_topic(address: str) -> str:
    return "0x" + address.removeprefix("0x").rjust(64, "0")


def _uint_topic(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()
