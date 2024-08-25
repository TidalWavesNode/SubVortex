import json
import bittensor as bt
from typing import List

from subnet import protocol
from subnet.validator.models import Miner


def send_miners(self, miners: List[Miner]):
    try:
        # Broadcast the synapse
        data = json.dumps([x.to_dict() for x in miners])
        self.p2p.broadcast("MINER", data)
    except Exception as err:
        bt.logging.warning(f"send_miners() Sending miners failed: {err}")
