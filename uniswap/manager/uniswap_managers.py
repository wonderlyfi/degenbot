from threading import Lock
from typing import Optional, Tuple, Union

from brownie import Contract
from web3 import Web3

from degenbot.constants import ZERO_ADDRESS
from degenbot.exceptions import (
    Erc20TokenError,
    LiquidityPoolError,
    ManagerError,
)
from degenbot.manager import Erc20TokenHelperManager, Manager
from degenbot.uniswap.functions import generate_v3_pool_address
from degenbot.uniswap.v2 import LiquidityPool
from degenbot.uniswap.v2.abi import UNISWAPV2_FACTORY_ABI
from degenbot.uniswap.v3 import TickLens, V3LiquidityPool
from degenbot.uniswap.v3.abi import UNISWAP_V3_FACTORY_ABI


class UniswapLiquidityPoolManager(Manager):
    """
    Single-concern class to allow V2 and V3 managers to share a token manager
    """

    _token_manager = Erc20TokenHelperManager()


class UniswapV2LiquidityPoolManager(UniswapLiquidityPoolManager):
    """
    A class that generates and tracks Uniswap V2 liquidity pool helpers

    The dictionaries of pool helpers are held as a class attribute, so all manager
    objects reference the same state data
    """

    _state = {}
    lock = Lock()

    def __init__(self, factory_address):

        if self._state.get(factory_address):
            self.__dict__ = self._state[factory_address]
        else:
            self._state[factory_address] = {}
            self.__dict__ = self._state[factory_address]

        # if erc20token_manager is not None:
        #     self.erc20token_manager = erc20token_manager
        # else:
        #     self.erc20token_manager = Erc20TokenHelperManager()

        self.factory_contract = Contract.from_abi(
            name="Uniswap V2: Factory",
            address=factory_address,
            abi=UNISWAPV2_FACTORY_ABI,
        )

        # initialize the pool helper dicts if not found
        if not hasattr(self, "pools_by_address"):
            self.pools_by_address = {}
        if not hasattr(self, "pools_by_tokens"):
            self.pools_by_tokens = {}

    def get_pool(
        self,
        pool_address: Optional[str] = None,
        token_addresses: Optional[Tuple[str]] = None,
        silent: bool = False,
    ) -> LiquidityPool:
        """
        Get the pool object from its address, or a tuple of token addresses
        """

        if pool_address is not None:

            pool_address = Web3.toChecksumAddress(pool_address)

            if pool_helper := self.pools_by_address.get(pool_address):
                return pool_helper

            try:
                pool_helper = LiquidityPool(
                    address=pool_address,
                    silent=silent,
                )
            except:
                raise ManagerError(f"Could not build V2 pool: {pool_address=}")

            with self.lock:
                self.pools_by_address[pool_address] = pool_helper
                self.pools_by_tokens[
                    (
                        pool_helper.token0.address,
                        pool_helper.token1.address,
                    )
                ] = pool_helper
                return pool_helper

        elif token_addresses is not None:

            if len(token_addresses) != 2:
                raise ValueError(
                    f"Expected two tokens, found {len(token_addresses)}"
                )

            try:
                erc20token_helpers = tuple(
                    [
                        self._token_manager.get_erc20token(
                            address=token_address,
                            min_abi=True,
                            silent=silent,
                            unload_brownie_contract_after_init=True,
                        )
                        for token_address in token_addresses
                    ]
                )
            except Erc20TokenError:
                raise ManagerError(
                    f"Could not build Erc20Token helpers for pool {pool_address}"
                )

            # dictionary key pair is sorted by address
            erc20token_helpers = (
                min(erc20token_helpers),
                max(erc20token_helpers),
            )
            tokens_key = tuple([token.address for token in erc20token_helpers])

            if pool_helper := self.pools_by_tokens.get(tokens_key):
                return pool_helper

            if (
                pool_address := self.factory_contract.getPair(*tokens_key)
            ) == ZERO_ADDRESS:
                raise ManagerError("No V2 LP available")

            try:
                pool_helper = LiquidityPool(
                    address=pool_address,
                    tokens=erc20token_helpers,
                    silent=silent,
                )
            except:
                raise ManagerError(f"Could not build V2 pool: {pool_address=}")

            with self.lock:
                self.pools_by_address[pool_address] = pool_helper
                self.pools_by_tokens[tokens_key] = pool_helper
                return pool_helper


class UniswapV3LiquidityPoolManager(UniswapLiquidityPoolManager):
    """
    A class that generates and tracks Uniswap V3 liquidity pool helpers

    The dictionaries of pool helpers are held as a class attribute, so all manager
    objects reference the same state data
    """

    _state = {}
    lock = Lock()

    def __init__(
        self,
        factory_address,
    ):

        if self._state.get(factory_address):
            self.__dict__ = self._state[factory_address]
        else:
            self._state[factory_address] = {}
            self.__dict__ = self._state[factory_address]

        self.factory_contract = Contract.from_abi(
            name="Uniswap V3: Factory",
            address=factory_address,
            abi=UNISWAP_V3_FACTORY_ABI,
        )

        # initialize the pool helper dicts if not found
        if not hasattr(self, "pools_by_address"):
            self.pools_by_address = {}
        if not hasattr(self, "pools_by_tokens_and_fee"):
            self.pools_by_tokens_and_fee = {}
        if not hasattr(self, "lens"):
            self.lens = TickLens()

    def get_pool(
        self,
        pool_address: Optional[str] = None,
        token_addresses: Optional[Tuple[str]] = None,
        pool_fee: Optional[int] = None,
        silent: bool = False,
    ) -> V3LiquidityPool:
        """
        Get the pool object from its address, or a tuple of token addresses and fee
        """

        if not (pool_address is None) ^ (
            token_addresses is None and pool_fee is None
        ):
            raise ValueError(
                f"Insufficient arguments provided. Pass address OR tokens+fee"
            )

        if pool_address is not None:
            if token_addresses is not None or pool_fee is not None:
                raise ValueError(
                    f"Conflicting arguments provided. Pass address OR tokens+fee"
                )

            pool_address = Web3.toChecksumAddress(pool_address)

            if pool_helper := self.pools_by_address.get(pool_address):
                return pool_helper

            try:
                pool_helper = V3LiquidityPool(
                    address=pool_address,
                    lens=self.lens,
                    silent=silent,
                )
            except:
                raise ManagerError(f"Could not build V3 pool: {pool_address=}")

            token_addresses = (
                pool_helper.token0.address,
                pool_helper.token1.address,
            )

            with self.lock:
                dict_key = *token_addresses, pool_fee
                self.pools_by_address[pool_address] = pool_helper
                self.pools_by_tokens_and_fee[dict_key] = pool_helper
                return pool_helper

        elif token_addresses is not None and pool_fee is not None:

            if len(token_addresses) != 2:
                raise ValueError(
                    f"Expected two tokens, found {len(token_addresses)}"
                )

            try:
                erc20token_helpers = tuple(
                    [
                        self._token_manager.get_erc20token(
                            address=token_address,
                            min_abi=True,
                            silent=silent,
                            unload_brownie_contract_after_init=True,
                        )
                        for token_address in token_addresses
                    ]
                )
            except Erc20TokenError:
                raise ManagerError("Could not build Erc20Token helpers")

            # dictionary key pair is sorted by address
            erc20token_helpers = (
                min(erc20token_helpers),
                max(erc20token_helpers),
            )
            tokens_key = tuple([token.address for token in erc20token_helpers])
            dict_key = *tokens_key, pool_fee

            if pool_helper := self.pools_by_tokens_and_fee.get(dict_key):
                return pool_helper

            pool_address = generate_v3_pool_address(
                token_addresses=tokens_key, fee=pool_fee
            )

            if pool_helper := self.pools_by_address.get(pool_address):
                return pool_helper

            try:
                pool_helper = V3LiquidityPool(
                    address=pool_address,
                    lens=self.lens,
                    tokens=erc20token_helpers,
                    silent=silent,
                )
            except:
                raise ManagerError(f"Could not build V3 pool: {pool_address=}")

            with self.lock:
                self.pools_by_address[pool_address] = pool_helper
                self.pools_by_tokens_and_fee[dict_key] = pool_helper
                return pool_helper
