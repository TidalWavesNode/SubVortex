import typing
import bittensor as bt

from subnet.shared.substrate import get_weights_min_stake


class SubVortexMetagraph(bt.metagraph):
    def __init__(
        self,
        netuid: int,
        network: str = "finney",
        lite: bool = True,
        sync: bool = True,
        onSync=None,
    ):
        super().__init__(netuid=netuid, network=network, lite=lite, sync=sync)

        self.onSync = onSync
        self.neuron_types: typing.List[str] = []

    def get_validators(self, weights_min_stake: int = 0):
        """
        Return the list of active validator.
        An active validator is a validator staking the minimum required
        """
        validators = []

        for neuron in self.neurons:
            stake = self.S[neuron.uid]
            if stake < weights_min_stake:
                # Skip any validator that has not enough stake to set weight
                continue

            validator = (
                neuron.uid,
                neuron.hotkey,
                stake,
            )
            validators.append(validator)

        return validators

    def sync(
        self,
        block: typing.Optional[int] = None,
        lite: bool = True,
        subtensor: typing.Optional["bt.subtensor"] = None,
    ):
        super().sync(block, lite, subtensor)

        if self.onSync:
            self.onSync()

    def _set_metagraph_attributes(self, block, subtensor):
        super()._set_metagraph_attributes(block, subtensor)

        # Create a list of neuron types, 'M' for Miner, 'V' for Validator, 'N' nothing
        weights_min_stake = get_weights_min_stake(subtensor.substrate)
        self.neuron_types = [
            (
                "V"
                if self._is_validator(uid, weights_min_stake)
                else "M" if self._is_miner(uid) else "N"
            )
            for uid in self.uids
        ]

    def _is_validator(self, uid, weights_min_stake):
        stake = self.S[uid]
        return not self.axons[uid].is_serving and stake >= weights_min_stake

    def _is_miner(self, uid):
        return self.axons[uid].is_serving
