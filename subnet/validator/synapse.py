import bittensor as bt
from typing import List

from subnet import protocol
from subnet.validator.models import Miner


def send_miners(self, miners: List[Miner], moving_scores):
    try:
        uids = []
        countries = []
        versions = []
        network_status = []
        last_challenge = []
        process_times = []
        scores = []
        availability_scores = []
        latency_scores = []
        reliability_scores = []
        distribution_scores = []
        moving_average_scores = []

        for miner in miners:
            uids.append(miner.uid)
            countries.append(miner.country)
            versions.append(miner.version)
            network_status.append(1 if miner.network_status == "healthy" else 0)
            last_challenge.append(miner.last_challenge)
            process_times.append(miner.process_time)
            scores.append(miner.score)
            availability_scores.append(miner.availability_score)
            latency_scores.append(miner.latency_score)
            reliability_scores.append(miner.reliability_score)
            distribution_scores.append(miner.distribution_score)
            moving_average_scores.append(moving_scores[miner.uid].item())

        # Create the Miners synapse
        synapse = protocol.Miners(
            uids=uids,
            countries=countries,
            versions=versions,
            network_status=network_status,
            last_challenges=last_challenge,
            process_times=process_times,
            final_scores=scores,
            availability_scores=availability_scores,
            latency_scores=latency_scores,
            reliability_scores=reliability_scores,
            distribution_scores=distribution_scores,
            moving_average_scores=moving_average_scores,
        )

        # Broadcast the synapse
        self.p2p.broadcast("MINER", synapse.model_dump_json())
    except Exception as err:
        bt.logging.warning(f"send_miners() Sending miners failed: {err}")
