import bittensor as bt
from prometheus_client import Gauge, Counter
from subnet.protocol import Miners

# TODO: Remove the unused metrics

# Score metrics
gauge_miner_final_score = Gauge(
    "miner_final_score",
    "Final score",
    ["uid"],
)
gauge_miner_availability_score = Gauge(
    "miner_availability_score", "Availability score", ["uid"]
)
gauge_miner_reliability_score = Gauge(
    "miner_reliability_score", "Reliability score", ["uid"]
)
gauge_miner_latency_score = Gauge("miner_latency_score", "Latency score", ["uid"])
gauge_miner_distribution_score = Gauge(
    "miner_distribution_score", "Distribution score", ["uid"]
)
gauge_miner_process_time = Gauge("miner_process_time", "Process time", ["uid"])
gauge_miner_moving_average_score = Gauge(
    "miner_moving_average_score", "Moving average score", ["uid"]
)

# Distribution metrics
gauge_miners_distribution = Gauge(
    "miners_distribution",
    "Distribution of verified miners by country",
    ["country"],
)

# Neuron metrics
gauge_neuron_details = Gauge(
    "neuron_details", "Neuron details", ["netuid", "uid", "country", "ip", "version"]
)

gauge_neuron_miner = Gauge(
    "neuron_miner",
    "Miner",
    [
        "uid",
        "rank",
        "ip",
        "country",
        "version",
        "network_status",
        "incentive",
        "coldkey",
        "hotkey",
    ],
)

gauge_neuron_validator = Gauge(
    "neuron_validator",
    "Validator",
    [
        "uid",
        "rank",
        "name",
        "ip",
        "country",
        "version",
        "network_status",
        "vtrust",
        "dividend",
        "consensus",
        "coldkey",
        "hotkey",
    ],
)


# Miner metrics
gauge_miner = Gauge(
    "miner",
    "Miner",
    [
        "uid",
        "country",
        "version",
        "network_status",
        "last_challenge",
    ],
)

# Rank metrics
gauge_miners_rank = Gauge("miners_rank", "Miners rank", ["uid"])

gauge_miners_rank_details = Gauge(
    "miners_rank_details", "Miners Rank details", ["top", "worst", "rank"]
)


def send_details_to_prometheus(
    netuid: int, uid: int, country: str, ip: str, version: str
):
    gauge_neuron_details.labels(
        netuid=netuid, uid=uid, country=country, ip=ip, version=version
    ).set(1)


def send_miners_to_prometheus(synapse: Miners, uid: int):
    # Clear the old serie
    gauge_miner.clear()

    for index, uid in enumerate(synapse.uids):
        # Miner metric
        gauge_miner.labels(
            uid=uid,
            country=synapse.countries[index],
            version=synapse.versions[index],
            network_status=synapse.network_status[index],
            last_challenge=synapse.last_challenges[index],
        ).set(0)

        # Process Time metric
        process_time = synapse.process_times[index]
        gauge_miner_process_time.labels(uid=uid).set(process_time)

        # Final Score metric
        final_score = synapse.final_scores[index]
        gauge_miner_final_score.labels(uid=uid).set(final_score)

        # Final Score metric
        availability_score = synapse.availability_scores[index]
        gauge_miner_availability_score.labels(uid=uid).set(availability_score)

        # Latency Score metric
        latency_score = synapse.latency_scores[index]
        gauge_miner_latency_score.labels(uid=uid).set(latency_score)

        # Reliability Score metric
        reliability_score = synapse.reliability_scores[index]
        gauge_miner_reliability_score.labels(uid=uid).set(reliability_score)

        # Distribution Score metric
        distribution_score = synapse.distribution_scores[index]
        gauge_miner_distribution_score.labels(uid=uid).set(distribution_score)

        moving_average_score = synapse.moving_average_scores[index]
        gauge_miner_moving_average_score.labels(uid=uid).set(moving_average_score)

    # Build and the send distribution metric
    country_counts = {}
    for country in synapse.countries:
        miner_country = country or "N/A"
        country_counts[miner_country] = country_counts.get(miner_country, 0) + 1

    for country, count in country_counts.items():
        gauge_miners_distribution.labels(country=country).set(count)


def send_neuron_to_prometheus(neuron):
    if neuron["type"] == "V":
        # Remove the old one
        labels = next(
            (
                x
                for x in gauge_neuron_validator._metrics.keys()
                if x[0] == str(neuron.get("uid"))
            ),
            None,
        )
        if labels:
            gauge_neuron_validator.remove(*labels)

        # Add the new one
        labels = {
            key: value
            for key, value in neuron.items()
            if key not in ["type", "incentive"]
        }
        gauge_neuron_validator.labels(**labels).set(0)
    elif neuron["type"] == "M":
        # Remove the old one
        labels = next(
            (
                x
                for x in gauge_neuron_miner._metrics.keys()
                if x[0] == str(neuron.get("uid"))
            ),
            None,
        )
        if labels:
            gauge_neuron_miner.remove(*labels)

        # Add the new one
        labels = {
            key: value
            for key, value in neuron.items()
            if key not in ["type", "vtrust", "dividend", "consensus"]
        }
        gauge_neuron_miner.labels(**labels).set(0)


def send_neurons_to_prometheus(neurons, neuron_uid):
    # Clear the old serie
    gauge_neuron_validator.clear()
    gauge_neuron_miner.clear()

    for neuron in neurons:
        if neuron["type"] == "V":
            labels = {
                key: value
                for key, value in neuron.items()
                if key not in ["type", "incentive"]
            }
            gauge_neuron_validator.labels(**labels).set(0)
        elif neuron["type"] == "M":
            labels = {
                key: value
                for key, value in neuron.items()
                if key not in ["type", "name", "vtrust", "dividend", "consensus"]
            }
            gauge_neuron_miner.labels(**labels).set(0)

    # Build and send the rank summarise metric
    miners = [x for x in neurons if x.get("type") == "M"]
    rank = next((x for x in miners if x.get("uid") == neuron_uid), {}).get("rank")

    sorted_neurons = sorted(miners, key=lambda item: item.get("rank"))
    top_uid = sorted_neurons[0].get("uid")
    worst_uid = sorted_neurons[-1].get("uid")

    gauge_miners_rank_details.labels(top=top_uid, worst=worst_uid, rank=rank).set(0)

    # Build the rank metric
    ranks = {x.get("uid"): float(x.get("rank")) for x in miners}
    ranks = dict(sorted(ranks.items(), key=lambda item: item[1], reverse=True))
    for uid, value in ranks.items():
        gauge_miners_rank.labels(uid=uid).set(value)
