# The MIT License (MIT)
# Copyright © 2024 Eclipse Vortex

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
import os
import sys
import json
import time
import copy
import torch
import typing
import asyncio
import bittensor as bt
import threading
import traceback
from prometheus_client import start_http_server

from subnet import __version__ as THIS_VERSION
from subnet.constants import NEURO_NETWROK_PORT
from subnet.protocol import Miners

from subnet.shared.checks import check_registration
from subnet.shared.subtensor import get_hyperparameter_value
from subnet.shared.substrate import get_weights_min_stake
from subnet.shared.mock import MockMetagraph, MockSubtensor, MockAxon
from subnet.shared.network import NeuroNetwork
from subnet.shared.delegate import get_delegates_details

from subnet.country.country import CountryService

from subnet.bittensor.metagraph import SubVortexMetagraph
from subnet.bittensor.axon import SubVortexAxon
from subnet.bittensor.synapse import Synapse

from subnet.sse.sse_thread import SSEThread

from subnet.file.file_monitor import FileMonitor
from subnet.firewall.firewall_factory import (
    create_firewall_tool,
    create_firewall_observer,
)
from subnet.miner.run import run
from subnet.miner.firewall import Firewall
from subnet.miner.config import (
    config,
    check_config,
    add_args,
)
from subnet.miner.utils import load_request_log
from subnet.miner.metrics import (
    send_details_to_prometheus,
    send_miners_to_prometheus,
    send_neurons_to_prometheus,
    send_neuron_to_prometheus,
)


class Miner:
    @classmethod
    def check_config(cls, config: "bt.Config"):
        """
        Adds neuron-specific arguments to the argument parser.

        Args:
            parser (argparse.ArgumentParser): Parser to add arguments to.

        This class method enriches the argument parser with options specific to the neuron's configuration.
        """
        check_config(cls, config)

    @classmethod
    def add_args(cls, parser):
        """
        Adds neuron-specific arguments to the argument parser.

        Args:
            parser (argparse.ArgumentParser): Parser to add arguments to.

        This class method enriches the argument parser with options specific to the neuron's configuration.
        """
        add_args(cls, parser)

    @classmethod
    def config(cls):
        """
        Retrieves the configuration for the neuron.

        Returns:
            bt.Config: The configuration object for the neuron.

        This class method returns the neuron's configuration, which is used throughout the neuron's lifecycle
        for various functionalities and operations.
        """
        return config(cls)

    subtensor: "bt.subtensor"
    wallet: "bt.wallet"
    metagraph: SubVortexMetagraph

    def __init__(self, config=None):
        base_config = copy.deepcopy(config or Miner.config())
        self.config = Miner.config()
        self.config.merge(base_config)
        self.check_config(self.config)
        bt.logging(
            config=self.config,
            logging_dir=self.config.miner.full_path,
            debug=True,
        )
        bt.logging.set_trace(self.config.logging.trace)
        bt.logging._stream_formatter.set_trace(self.config.logging.trace)
        bt.logging.info(f"{self.config}")

        self.previouds_last_challenge = None

        # Set ip/port of the miner
        self.ip = bt.utils.networking.get_external_ip()
        self.port = (
            self.config.axon.external_port
            if self.config.axon.external_port is not None
            else self.config.axon.port
        )
        self.version = THIS_VERSION

        # Show the pid
        pid = os.getpid()
        bt.logging.debug(f"miner PID: {pid}")

        # Show miner details
        bt.logging.debug(f"miner ip {self.ip}")
        bt.logging.debug(f"miner port {self.port}")
        bt.logging.debug(f"miner version {self.version}")

        # Init device.
        bt.logging.debug("loading device")
        self.device = torch.device(self.config.miner.device)
        bt.logging.debug(str(self.device))

        # File monitor
        self.file_monitor = FileMonitor()
        self.file_monitor.start()

        # Server-Sent Events
        self.sse = SSEThread(ip=self.config.sse.firewall.ip, port=self.config.sse.port)
        self.sse.server.add_stream("firewall")
        self.sse.start()

        # Firewall
        self.firewall = None
        if self.config.firewall.on:
            bt.logging.debug(
                f"Starting firewall on interface {self.config.firewall.interface}"
            )
            self.firewall = Firewall(
                observer=create_firewall_observer(),
                tool=create_firewall_tool(),
                sse=self.sse.server,
                port=self.port,
                interface=self.config.firewall.interface,
                config_file=self.config.firewall.config,
            )
            self.file_monitor.add_file_provider(self.firewall.provider)
            self.firewall.start()

        # Init wallet.
        bt.logging.debug("loading wallet")
        self.wallet = (
            bt.MockWallet(config=self.config)
            if self.config.mock
            else bt.wallet(config=self.config)
        )
        self.wallet.create_if_non_existent()
        bt.logging.debug(f"wallet: {str(self.wallet)}")

        # Init subtensor
        bt.logging.debug("loading subtensor")
        self.subtensor = (
            MockSubtensor(self.config.netuid, wallet=self.wallet)
            if self.config.miner.mock_subtensor
            else bt.subtensor(config=self.config, network="local")
        )
        bt.logging.debug(str(self.subtensor))
        self.current_block = self.subtensor.get_current_block()

        bt.logging.debug("checking wallet registration")
        check_registration(self.subtensor, self.wallet, self.config.netuid)

        # Init metagraph.
        bt.logging.debug("loading metagraph")
        self.metagraph = (
            MockMetagraph(self.config.netuid, subtensor=self.subtensor)
            if self.config.mock
            else SubVortexMetagraph(
                netuid=self.config.netuid,
                network=self.subtensor.network,
                sync=False,
                onSync=self._handle_sync,
            )
        )

        self.metagraph.sync(subtensor=self.subtensor)  # Sync metagraph with subtensor.
        bt.logging.debug(str(self.metagraph))

        self.uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        bt.logging.info(f"Running miner on uid: {self.uid}")

        # The axon handles request processing, allowing validators to send this process requests.
        self.axon = (
            MockAxon(
                wallet=self.wallet,
                config=self.config,
                external_ip=bt.utils.networking.get_external_ip(),
                blacklist_fn=self._blacklist,
            )
            if self.config.mock
            else SubVortexAxon(
                wallet=self.wallet,
                config=self.config,
                external_ip=self.ip,
                blacklist_fn=self._blacklist,
            )
        )
        bt.logging.info(f"Axon {self.axon}")
        bt.logging.info(f"Axon version {self.axon.info().version}")

        # Serve passes the axon information to the network + netuid we are hosting on.
        # This will auto-update if the axon port of external ip have changed.
        bt.logging.info(
            f"Serving axon {self.axon} on network: {self.subtensor.chain_endpoint} with netuid: {self.config.netuid}"
        )
        self.axon.serve(netuid=self.config.netuid, subtensor=self.subtensor)

        # Check there is not another miner running on the machine
        bt.logging.debug(f"Checking number of miners on same ip")
        number_of_miners = len(
            [axon for axon in self.metagraph.axons if self.axon.external_ip == axon.ip]
        )
        if number_of_miners > 1:
            bt.logging.error(
                "At least one miner is already running on this machine. If you run more than one miner you will penalise all of your miners until you get de-registered or start each miner on a unique machine"
            )
            sys.exit(1)

        # Start Prometheus
        if self.config.prometheus.port:
            self.prometheus, _ = start_http_server(port=self.config.prometheus.port)

        # Start Neuro Network
        neurons = [(x.uid, x.axon_info.ip) for x in self.metagraph.neurons]
        self.p2p = NeuroNetwork(
            uid=self.uid,
            ip=self.ip,
            port=NEURO_NETWROK_PORT,
            version=self.version,
            peers=neurons,
        )
        self.p2p.start()
        self.p2p.subscribe("MINER", self._handle_miners)
        self.p2p.subscribe("DISCOVERY", self._handle_discovery)

        # Start  starts the miner's axon, making it active on the network.
        bt.logging.info(f"Starting axon server on port: {self.config.axon.port}")
        self.axon.start()

        # File monitor
        self.file_monitor = FileMonitor()
        self.file_monitor.start()

        # Country service
        self.country_service = CountryService(self.config.netuid)
        self.file_monitor.add_file_provider(self.country_service.provider)
        self.country_service.wait()

        # Send neuron details to Prometheus
        self.country = self.country_service.get_country(self.ip)
        send_details_to_prometheus(
            netuid=self.config.netuid,
            uid=self.uid,
            country=self.country,
            ip=self.ip,
            version=self.version,
        )

        # Init the event loop.
        self.loop = asyncio.get_event_loop()

        # Instantiate runners
        self.should_exit: bool = False
        self.is_running: bool = False
        self.thread: threading.Thread = None
        self.lock = asyncio.Lock()
        self.request_timestamps: typing.Dict = {}
        self.previous_last_updates = []

        self.step = 0

        self.request_log = load_request_log(self.config.miner.request_log_path)

    def _blacklist(self, synapse: Synapse) -> typing.Tuple[bool, str]:
        caller = synapse.dendrite.hotkey
        caller_version = synapse.dendrite.neuron_version or 0
        synapse_type = type(synapse).__name__

        # Get the list of all validators
        validators = self.metagraph.get_validators(0)

        # Get the validator associated to the hotkey
        validator = next((x for x in validators if x[1] == caller), None)

        # Block hotkeys that are not an active validator hotkey
        if not validator:
            bt.logging.debug(
                f"Blacklisted a {synapse_type} request from a unrecognized hotkey {caller}"
            )
            return True, "Unrecognized hotkey"

        # Block hotkeys that do not have the latest version
        active_version = get_hyperparameter_value(
            self.subtensor, "weights_version", self.config.netuid
        )
        if caller_version < active_version:
            bt.logging.debug(
                f"Blacklisted a {synapse_type} request from a non-updated hotkey {caller}"
            )
            return True, "Non-updated hotkey"

        # Block hotkeys that do not have the minimum require stake to set weight
        weights_min_stake = get_weights_min_stake(self.subtensor.substrate)
        if validator[2] < weights_min_stake:
            bt.logging.debug(
                f"Blacklisted a {synapse_type} request from a not enought stake hotkey {caller}"
            )
            return True, "Not enough stake hotkey"

        bt.logging.trace(f"Not Blacklisting recognized hotkey {caller}")
        return False, "Hotkey recognized!"

    def _handle_miners(self, message):
        content = json.loads(message)
        synapse = Miners(**content)

        validator_uid = 20

        index = synapse.uids.index(self.uid)
        last_challenge = synapse.last_challenges[index]

        if (
            self.previouds_last_challenge is None
            or self.previouds_last_challenge != last_challenge
        ):
            score = synapse.availability_scores[index]
            bt.logging.info(f"[{validator_uid}] Availability score {score}")

            score = synapse.latency_scores[index]
            bt.logging.info(f"[{validator_uid}] Latency score {score}")

            score = synapse.reliability_scores[index]
            bt.logging.info(f"[{validator_uid}] Reliability score {score}")

            score = synapse.distribution_scores[index]
            bt.logging.info(f"[{validator_uid}] Distribution score {score}")

            score = synapse.final_scores[index]
            bt.logging.success(f"[{validator_uid}] Score {score}")

            self.previouds_last_challenge = last_challenge

        # Send metics
        send_miners_to_prometheus(synapse, self.uid)

    def _handle_discovery(self, message):
        content = json.loads(message)

        if content.get("type") == "M":
            # Do nothing as we have already all the information via the metagraph
            return

        # Validator - Some information (e.g ip) are not available in the metagraph, so we use the discovery message

        # Rank the validators
        validators_uids = {
            x: self.metagraph.validator_trust[i]
            for i, x in enumerate(self.metagraph.uids)
            if self.metagraph.neuron_types[x] == "V"
        }
        sorted_validators_ranks = dict(
            sorted(validators_uids.items(), key=lambda item: item[1], reverse=True)
        )
        sorted_validators_uids = list(sorted_validators_ranks.keys())

        # Get the delegate info
        delegate_info = get_delegates_details(url=bt.__delegates_details_url__)

        # Build metrics
        uid = content.get("uid")
        neuron_type = self.metagraph.neuron_types[uid]

        rank = sorted_validators_uids.index(uid)
        axon = self.metagraph.axons[uid]
        name = (
            delegate_info[axon.hotkey].name
            if axon.hotkey in delegate_info
            else axon.hotkey
        )

        incentive = format(self.metagraph.incentive[uid], ".5f")
        dividend = format(self.metagraph.dividends[uid], ".5f")
        vtrust = format(self.metagraph.validator_trust[uid], ".5f")
        consensus = format(self.metagraph.consensus[uid], ".5f")
        neuron = {
            "rank": rank,
            "uid": uid,
            "name": name,
            "type": neuron_type,
            "ip": content.get("ip"),
            "country": "",
            "version": content.get("version"),
            "network_status": 1,
            "incentive": incentive,
            "dividend": dividend,
            "vtrust": vtrust,
            "consensus": consensus,
            "coldkey": axon.coldkey,
            "hotkey": axon.hotkey,
        }

        # Send the neurons to Prometheus
        send_neuron_to_prometheus(neuron)

    def _handle_sync(self):
        # Rank the miners
        miner_uids = {
            x: self.metagraph.ranks[i]
            for i, x in enumerate(self.metagraph.uids)
            if self.metagraph.neuron_types[x] == "M"
        }
        sorted_miners_ranks = dict(
            sorted(miner_uids.items(), key=lambda item: item[1], reverse=True)
        )
        sorted_miners_uids = list(sorted_miners_ranks.keys())

        # Rank the validators
        validators_uids = {
            x: self.metagraph.validator_trust[i]
            for i, x in enumerate(self.metagraph.uids)
            if self.metagraph.neuron_types[x] == "V"
        }
        sorted_validators_ranks = dict(
            sorted(validators_uids.items(), key=lambda item: item[1], reverse=True)
        )
        sorted_validators_uids = list(sorted_validators_ranks.keys())

        # Get the delegate info
        delegate_info = get_delegates_details(url=bt.__delegates_details_url__)

        # Build metrics
        neurons = []
        for uid in self.metagraph.uids:
            neuron_type = self.metagraph.neuron_types[uid]

            rank = (
                sorted_miners_uids.index(uid)
                if neuron_type == "M"
                else sorted_validators_uids.index(uid) if neuron_type == "V" else -1
            )

            axon = self.metagraph.axons[uid]
            name = (
                delegate_info[axon.hotkey].name
                if axon.hotkey in delegate_info
                else axon.hotkey
            )
            incentive = format(self.metagraph.incentive[uid], ".5f")
            dividend = format(self.metagraph.dividends[uid], ".5f")
            vtrust = format(self.metagraph.validator_trust[uid], ".5f")
            consensus = format(self.metagraph.consensus[uid], ".5f")

            neuron = {
                "rank": rank,
                "uid": uid,
                "name": name,
                "type": neuron_type,
                "ip": axon.ip,
                "country": "",
                "version": "",
                "network_status": 0,
                "incentive": incentive,
                "dividend": dividend,
                "vtrust": vtrust,
                "consensus": consensus,
                "coldkey": axon.coldkey,
                "hotkey": axon.hotkey,
            }
            neurons.append(neuron)

        # Send the neurons to Prometheus
        uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        send_neurons_to_prometheus(neurons, uid)

    def run(self):
        run(self)

    def run_in_background_thread(self):
        """
        Starts the miner's operations in a separate background thread.
        This is useful for non-blocking operations.
        """
        if not self.is_running:
            bt.logging.debug("Starting miner in background thread.")
            self.should_exit = False
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()
            self.is_running = True
            bt.logging.debug("Started")

    def stop_run_thread(self):
        """
        Stops the miner's operations that are running in the background thread.
        """
        if self.is_running:
            bt.logging.debug("Stopping miner in background thread.")
            self.should_exit = True
            self.thread.join(5)
            self.is_running = False
            bt.logging.debug("Stopped")

    def __enter__(self):
        """
        Starts the miner's operations in a background thread upon entering the context.
        This method facilitates the use of the miner in a 'with' statement.
        """
        self.run_in_background_thread()

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Stops the miner's background operations upon exiting the context.
        This method facilitates the use of the miner in a 'with' statement.

        Args:
            exc_type: The type of the exception that caused the context to be exited.
                      None if the context was exited without an exception.
            exc_value: The instance of the exception that caused the context to be exited.
                       None if the context was exited without an exception.
            traceback: A traceback object encoding the stack trace.
                       None if the context was exited without an exception.
        """
        self.stop_run_thread()

    def should_sync_metagraph(self):
        """
        True if the metagraph has been resynced, False otherwise.
        """
        last_updates = self.subtensor.substrate.query(
            module="SubtensorModule",
            storage_function="LastUpdate",
            params=[self.config.netuid],
        )
        if self.previous_last_updates == last_updates:
            return False

        # Save the new updates
        self.previous_last_updates = last_updates

        return True

    def update_firewall(self):
        # Get version and min stake
        version = get_hyperparameter_value(
            self.subtensor, "weights_version", self.config.netuid
        )
        weights_min_stake = get_weights_min_stake(self.subtensor.substrate)

        # Update the specifications
        specifications = {
            "neuron_version": version,
            "synapses": self.axon.forward_class_types,
            "hotkey": self.wallet.hotkey.ss58_address,
        }
        bt.logging.debug(f"Firewall specifications {specifications}")

        # Define the valid validators
        validators = self.metagraph.get_validators(weights_min_stake)
        valid_validators = [x[1] for x in validators]

        # Update the firewall
        self.firewall.update(
            specifications=specifications,
            whitelist_hotkeys=valid_validators,
        )
        bt.logging.debug("Firewall updated")


def run_miner():
    """
    Main function to run the neuron.

    This function initializes and runs the neuron. It handles the main loop, state management, and interaction
    with the Bittensor network.
    """
    miner = None
    try:
        miner = Miner()
        miner.run_in_background_thread()

        while 1:
            time.sleep(1)
    except KeyboardInterrupt:
        bt.logging.info("Keyboard interrupt detected, exiting.")
        sys.exit(0)
    except Exception as e:
        bt.logging.error(traceback.format_exc())
        bt.logging.error(f"Unhandled exception: {e}")
        sys.exit(1)
    finally:
        if miner and miner.prometheus:
            bt.logging.info("Stopping Prometheus")
            miner.prometheus.shutdown()

        if miner and miner.p2p:
            bt.logging.info("Stopping neuro network")
            miner.p2p.stop()

        if miner and miner.sse:
            miner.sse.stop()

        if miner and miner.firewall:
            miner.firewall.stop()

        if miner and miner.file_monitor:
            miner.file_monitor.stop()

        if miner and miner.axon:
            bt.logging.info("Stopping axon")
            miner.axon.stop()


if __name__ == "__main__":
    run_miner()
