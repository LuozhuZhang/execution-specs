import importlib
import json
import logging
import os.path
import re
from abc import ABC, abstractmethod
from glob import glob
from typing import Any, Dict, Generator, List, Tuple, Union, cast
from unittest.mock import call, patch

import pytest
from _pytest.mark.structures import ParameterSet

from ethereum import rlp
from ethereum.base_types import U256, Bytes0, Uint64
from ethereum.crypto.hash import Hash32
from ethereum.utils.hexadecimal import (
    hex_to_bytes,
    hex_to_bytes8,
    hex_to_bytes32,
    hex_to_hash,
    hex_to_u256,
    hex_to_uint,
)


class BaseLoad(ABC):
    @property
    @abstractmethod
    def fork_module(self) -> str:
        pass

    @property
    @abstractmethod
    def network(self) -> str:
        pass

    @property
    @abstractmethod
    def Block(self) -> Any:
        pass

    @property
    @abstractmethod
    def BlockChain(self) -> Any:
        pass

    @property
    @abstractmethod
    def state_transition(self) -> Any:
        pass

    @property
    @abstractmethod
    def close_state(self) -> Any:
        pass

    @abstractmethod
    def json_to_header(self, json_data: Any) -> Any:
        pass

    @abstractmethod
    def json_to_state(self, json_data: Any) -> Any:
        pass

    @abstractmethod
    def json_to_blocks(self, json_data: Any) -> Any:
        pass


class Load(BaseLoad):
    _network: str
    _fork_module: str

    @property
    def fork_module(self) -> str:
        return self._fork_module

    @property
    def network(self) -> str:
        return self._network

    @property
    def Block(self) -> Any:
        return self._module("eth_types").Block

    @property
    def Bloom(self) -> Any:
        return self._module("eth_types").Bloom

    @property
    def Header(self) -> Any:
        return self._module("eth_types").Header

    @property
    def state_transition(self) -> Any:
        return self._module("spec").state_transition

    @property
    def BlockChain(self) -> Any:
        return self._module("spec").BlockChain

    @property
    def hex_to_address(self) -> Any:
        return self._module("utils.hexadecimal").hex_to_address

    @property
    def hex_to_root(self) -> Any:
        return self._module("utils.hexadecimal").hex_to_root

    @property
    def close_state(self) -> Any:
        return self._module("state").close_state

    def __init__(self, network: str, fork_name: str):
        self._network = network
        self._fork_module = fork_name

    def _module(self, name: str) -> Any:
        return importlib.import_module(f"ethereum.{self._fork_module}.{name}")

    def json_to_state(self, raw: Any) -> Any:
        state = self._module("state").State()
        set_account = self._module("state").set_account
        set_storage = self._module("state").set_storage

        for (addr_hex, acc_state) in raw.items():
            addr = self.hex_to_address(addr_hex)
            account = self._module("eth_types").Account(
                nonce=hex_to_uint(acc_state.get("nonce", "0x0")),
                balance=U256(hex_to_uint(acc_state.get("balance", "0x0"))),
                code=hex_to_bytes(acc_state.get("code", "")),
            )
            set_account(state, addr, account)

            for (k, v) in acc_state.get("storage", {}).items():
                set_storage(
                    state,
                    addr,
                    hex_to_bytes32(k),
                    U256.from_be_bytes(hex_to_bytes32(v)),
                )
        return state

    def json_to_access_list(self, raw: Any) -> Any:
        access_list = []
        for sublist in raw:
            access_list.append(
                (
                    self.hex_to_address(sublist.get("address")),
                    [
                        hex_to_bytes32(key)
                        for key in sublist.get("storageKeys")
                    ],
                )
            )
        return access_list

    def json_to_tx(self, raw: Any) -> Any:
        if hasattr(self._module("eth_types"), "LegacyTransaction"):
            if "accessList" in raw:
                return b"\x01" + rlp.encode(
                    self._module("eth_types").AccessListTransaction(
                        Uint64(1),
                        hex_to_u256(raw.get("nonce")),
                        hex_to_u256(raw.get("gasPrice")),
                        hex_to_u256(raw.get("gasLimit")),
                        Bytes0(b"")
                        if raw.get("to") == ""
                        else self.hex_to_address(raw.get("to")),
                        hex_to_u256(raw.get("value")),
                        hex_to_bytes(raw.get("data")),
                        self.json_to_access_list(raw.get("accessList")),
                        hex_to_u256(raw.get("v")),
                        hex_to_u256(raw.get("r")),
                        hex_to_u256(raw.get("s")),
                    )
                )
            else:
                return self._module("eth_types").LegacyTransaction(
                    hex_to_u256(raw.get("nonce")),
                    hex_to_u256(raw.get("gasPrice")),
                    hex_to_u256(raw.get("gasLimit")),
                    Bytes0(b"")
                    if raw.get("to") == ""
                    else self.hex_to_address(raw.get("to")),
                    hex_to_u256(raw.get("value")),
                    hex_to_bytes(raw.get("data")),
                    hex_to_u256(raw.get("v")),
                    hex_to_u256(raw.get("r")),
                    hex_to_u256(raw.get("s")),
                )
        else:
            return self._module("eth_types").Transaction(
                hex_to_u256(raw.get("nonce")),
                hex_to_u256(raw.get("gasPrice")),
                hex_to_u256(raw.get("gasLimit")),
                Bytes0(b"")
                if raw.get("to") == ""
                else self.hex_to_address(raw.get("to")),
                hex_to_u256(raw.get("value")),
                hex_to_bytes(raw.get("data")),
                hex_to_u256(raw.get("v")),
                hex_to_u256(raw.get("r")),
                hex_to_u256(raw.get("s")),
            )

    def json_to_blocks(
        self,
        json_blocks: Any,
    ) -> Tuple[List[Any], List[Hash32], List[bytes]]:
        blocks = []
        block_header_hashes = []
        block_rlps = []

        for json_block in json_blocks:
            if "blockHeader" not in json_block and "rlp" in json_block:
                # Some blocks are represented by only the RLP and not the block details
                block_rlp = hex_to_bytes(json_block["rlp"])
                block = rlp.decode_to(self.Block, block_rlp)
                blocks.append(block)
                block_header_hashes.append(rlp.rlp_hash(block.header))
                block_rlps.append(block_rlp)
                continue

            header = self.json_to_header(json_block["blockHeader"])
            transactions = tuple(
                self.json_to_tx(tx) for tx in json_block["transactions"]
            )
            uncles = tuple(
                self.json_to_header(uncle)
                for uncle in json_block["uncleHeaders"]
            )

            blocks.append(
                self.Block(
                    header,
                    transactions,
                    uncles,
                )
            )
            block_header_hashes.append(
                Hash32(hex_to_bytes(json_block["blockHeader"]["hash"]))
            )
            block_rlps.append(hex_to_bytes(json_block["rlp"]))

        return blocks, block_header_hashes, block_rlps

    def json_to_header(self, raw: Any) -> Any:
        return self.Header(
            hex_to_hash(raw.get("parentHash")),
            hex_to_hash(raw.get("uncleHash") or raw.get("sha3Uncles")),
            self.hex_to_address(raw.get("coinbase") or raw.get("miner")),
            self.hex_to_root(raw.get("stateRoot")),
            self.hex_to_root(
                raw.get("transactionsTrie") or raw.get("transactionsRoot")
            ),
            self.hex_to_root(
                raw.get("receiptTrie") or raw.get("receiptsRoot")
            ),
            self.Bloom(hex_to_bytes(raw.get("bloom") or raw.get("logsBloom"))),
            hex_to_uint(raw.get("difficulty")),
            hex_to_uint(raw.get("number")),
            hex_to_uint(raw.get("gasLimit")),
            hex_to_uint(raw.get("gasUsed")),
            hex_to_u256(raw.get("timestamp")),
            hex_to_bytes(raw.get("extraData")),
            hex_to_bytes32(raw.get("mixHash")),
            hex_to_bytes8(raw.get("nonce")),
        )


def load_test(test_case: Dict, load: BaseLoad) -> Dict:

    json_data = test_case["test_data"]

    blocks, block_header_hashes, block_rlps = load.json_to_blocks(
        json_data["blocks"]
    )

    return {
        "test_file": test_case["test_file"],
        "test_key": test_case["test_key"],
        "genesis_header": load.json_to_header(json_data["genesisBlockHeader"]),
        "chain_id": Uint64(json_data["genesisBlockHeader"].get("chainId", 1)),
        "genesis_header_hash": hex_to_bytes(
            json_data["genesisBlockHeader"]["hash"]
        ),
        "genesis_block_rlp": hex_to_bytes(json_data["genesisRLP"]),
        "last_block_hash": hex_to_bytes(json_data["lastblockhash"]),
        "pre_state": load.json_to_state(json_data["pre"]),
        "expected_post_state": load.json_to_state(json_data["postState"]),
        "blocks": blocks,
        "block_header_hashes": block_header_hashes,
        "block_rlps": block_rlps,
        "ignore_pow_validation": json_data["sealEngine"] == "NoProof",
    }


def run_blockchain_st_test(test_case: Dict, load: BaseLoad) -> None:

    test_data = load_test(test_case, load)

    genesis_header = test_data["genesis_header"]
    genesis_block = load.Block(
        genesis_header,
        (),
        (),
    )

    assert rlp.rlp_hash(genesis_header) == test_data["genesis_header_hash"]
    assert (
        rlp.encode(cast(rlp.RLP, genesis_block))
        == test_data["genesis_block_rlp"]
    )

    chain = load.BlockChain(
        blocks=[genesis_block],
        state=test_data["pre_state"],
        chain_id=test_data["chain_id"],
    )

    if not test_data["ignore_pow_validation"]:
        add_blocks_to_chain(chain, test_data, load)
    else:
        with patch(
            f"ethereum.{load.fork_module}.spec.validate_proof_of_work",
            autospec=True,
        ) as mocked_pow_validator:
            add_blocks_to_chain(chain, test_data, load)
            mocked_pow_validator.assert_has_calls(
                [call(block.header) for block in test_data["blocks"]],
                any_order=False,
            )

    assert (
        rlp.rlp_hash(chain.blocks[-1].header) == test_data["last_block_hash"]
    )
    assert chain.state == test_data["expected_post_state"]
    load.close_state(chain.state)
    load.close_state(test_data["expected_post_state"])


def add_blocks_to_chain(
    chain: Any, test_data: Dict[str, Any], load: BaseLoad
) -> None:
    for idx, block in enumerate(test_data["blocks"]):
        assert (
            rlp.rlp_hash(block.header) == test_data["block_header_hashes"][idx]
        )
        assert rlp.encode(cast(rlp.RLP, block)) == test_data["block_rlps"][idx]
        load.state_transition(chain, block)


# Functions that fetch individual test cases
def load_json_fixture(test_file: str, network: str) -> Generator:
    # Extract the pure basename of the file without the path to the file.
    # Ex: Extract "world.json" from "path/to/file/world.json"
    pure_test_file = os.path.basename(test_file)
    # Extract the filename without the extension. Ex: Extract "world" from
    # "world.json"
    test_name = os.path.splitext(pure_test_file)[0]
    with open(test_file, "r") as fp:
        data = json.load(fp)

        # Some newer test files have patterns like _d0g0v0_
        # between test_name and network
        keys_to_search = re.compile(
            f"{re.escape(test_name)}.*{re.escape(network)}"
        )
        found_keys = list(filter(keys_to_search.match, data.keys()))

        if not any(found_keys):
            raise KeyError

        for _key in found_keys:
            yield {
                "test_file": test_file,
                "test_key": _key,
                "test_data": data[_key],
            }


def fetch_state_test_files(
    test_dir: str,
    network: str,
    only_in: Tuple[str, ...] = (),
    slow_list: Tuple[str, ...] = (),
    big_memory_list: Tuple[str, ...] = (),
    ignore_list: Tuple[str, ...] = (),
) -> Generator[Union[Dict, ParameterSet], None, None]:

    all_slow = [re.compile(x) for x in slow_list]
    all_big_memory = [re.compile(x) for x in big_memory_list]
    all_ignore = [re.compile(x) for x in ignore_list]

    # Get all the files to iterate over
    # Maybe from the custom file list or entire test_dir
    files_to_iterate = []
    if len(only_in):
        # Get file list from custom list, if one is specified
        for test_path in only_in:
            files_to_iterate.append(os.path.join(test_dir, test_path))
    else:
        # If there isnt a custom list, iterate over the test_dir
        all_jsons = [
            y
            for x in os.walk(test_dir)
            for y in glob(os.path.join(x[0], "*.json"))
        ]

        for full_path in all_jsons:
            if not any(x.search(full_path) for x in all_ignore):
                # If a file or folder is marked for ignore,
                # it can already be dropped at this stage
                files_to_iterate.append(full_path)

    # Start yielding individual test cases from the file list
    for _test_file in files_to_iterate:
        try:
            for _test_case in load_json_fixture(_test_file, network):
                # _identifier could identifiy files, folders through test_file
                #  individual cases through test_key
                _identifier = (
                    "("
                    + _test_case["test_file"]
                    + "|"
                    + _test_case["test_key"]
                    + ")"
                )
                if any(x.search(_identifier) for x in all_ignore):
                    continue
                elif any(x.search(_identifier) for x in all_slow):
                    yield pytest.param(_test_case, marks=pytest.mark.slow)
                elif any(x.search(_identifier) for x in all_big_memory):
                    yield pytest.param(_test_case, marks=pytest.mark.bigmem)
                else:
                    yield _test_case
        except KeyError:
            # file doesn't contain tests for the given fork
            continue


# Test case Identifier
def idfn(test_case: Dict) -> str:
    folder_name = test_case["test_file"].split("/")[-2]
    # Assign Folder name and test_key to identify tests in output
    return folder_name + " - " + test_case["test_key"]
