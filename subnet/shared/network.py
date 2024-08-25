import time
import json
import socket
import struct
import select
import traceback
import threading
import bittensor as bt
from collections import defaultdict

LOGGING_PREFIX = "Network"
CHUNK_SIZE = 1024
STRUCT_SIZE = 8
MESSAGE_SIZE = CHUNK_SIZE - 8


class Peer:
    def __init__(self, uid, ip, port, version="0.0.0.0", status=None):
        self.uid = int(uid)
        self.ip = ip
        self.port = int(port)
        self.version = version
        self.status = status

    @staticmethod
    def copy(peer):
        return Peer(uid=peer.uid, ip=peer.ip, port=peer.port, status=peer.status)

    def __eq__(self, other):
        if isinstance(other, Peer):
            return (
                self.uid == other.uid
                and self.ip == other.ip
                and self.port == other.port,
            )
        return False

    def __hash__(self):
        return hash((self.uid, self.ip, self.ip, self.port))

    def __str__(self):
        return f"Peer(uid={self.uid}, ip={self.ip}, port={self.port}, version={self.version}, status={self.status})"

    def __repr__(self):
        return f"Peer(uid={self.uid}, ip={self.ip}, port={self.port}, version={self.version}, status={self.status})"


# TODO: health check all the peer + add a status on all of them to send message when broadcasting only on the healthy ones
class NeuroNetwork(threading.Thread):
    def __init__(
        self,
        uid: str,
        ip: str,
        port: int,
        version: str = "0.0.0.0",
        peers=[],
    ):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self._events = defaultdict()
        self.uid = uid
        self.ip = ip
        self.port = port
        self.version = version

        # Initialise the peers
        self._peers = set(
            [
                Peer(
                    uid=uid,
                    ip=self.ip if uid == self.uid else ip,
                    port=self.port,
                    version=self.version if uid == self.uid else "0.0.0.0",
                    status="unhealthy" if uid != self.uid else "healthy",
                )
                for uid, ip in peers
            ]
        )

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", self.port))
        bt.logging.info(f"[{LOGGING_PREFIX}] Starting neuro socket on {self.port}")

    @property
    def peer(self):
        return list(self._peers)

    # TODO: Manage the errors in a better way
    def run(self):
        try:

            # Discover the Neuron
            message = self._create_discovery_message()
            self.broadcast("DISCOVERY", message)

            request_id = None
            seq_num = None
            received_messages = defaultdict(list)
            received_end_of_messages = defaultdict(bool)
            while not self._stop_event.is_set():
                try:

                    # Use select to check for incoming data with a timeout
                    ready_socks, _, _ = select.select([self.sock], [], [], 1)

                    if ready_socks:
                        # Receive data
                        data, addr = self.sock.recvfrom(CHUNK_SIZE)
                        if len(data) < 8:
                            # TODO: Issue, notif?
                            continue

                        # Unpack the request ID and sequence number
                        request_id, seq_num, total_chunks = struct.unpack(
                            "!I H H", data[:8]
                        )
                        message_part = data[8:]

                        is_end_of_message = seq_num == 0xFFFF

                        # If the request ID is new, initialize a list to store the message parts
                        if request_id not in received_messages:
                            received_messages[request_id] = [None] * total_chunks

                        # Store the End of Message has been received if it has not ben yet stored
                        if not received_end_of_messages.get(request_id, False):
                            received_end_of_messages[request_id] = is_end_of_message

                        # Store the received part (exclude the end of message)
                        if not is_end_of_message:
                            received_messages[request_id][seq_num] = message_part

                        number_of_part = len(
                            [x for x in received_messages[request_id] if x is not None]
                        )
                        if (
                            number_of_part != total_chunks
                            or not received_end_of_messages[request_id]
                        ):
                            # We have not received all the chunks yet
                            continue

                        # If this is the end of the message, process the full message
                        full_message = b"".join(received_messages[request_id])

                        # Delete the message
                        del received_messages[request_id]
                        del received_end_of_messages[request_id]

                        # Process the full message
                        self._handle_message(full_message.decode(), addr)

                    # Send Metrics to Prometheus
                    # send_peers_to_prometheus(list(self._peers))

                    # Send DISCOVERY if the status is still unhealthy
                    # my_peer = next((x for x in self._peers if x.uid == self.uid), None)
                    if not self._at_least_one_neuron_up():
                        self.broadcast("DISCOVERY", message)
                except Exception as e:
                    bt.logging.error(
                        f"[{LOGGING_PREFIX}][{request_id}][{total_chunks}][{seq_num}] Processing Synapse failed: {e}"
                    )
                    bt.logging.error(traceback.format_exc())
                    pass
        except Exception as e:
            pass
        finally:
            # Undiscover the neuron
            message = json.dumps({"uid": self.uid, "ip": self.ip, "port": self.port})
            self.broadcast("UNDISCOVERY", message)

    def stop(self):
        self._stop_event.set()
        self.join()

    def broadcast(self, message_type, message):
        """
        Send a message to all known peers.
        """
        # Format the message
        formatted_message = f"{message_type}:{message}"

        # Encode the message
        message = formatted_message.encode()

        # Define request ID and sequence number
        request_id = int(time.time() * 1000) & 0xFFFFFFFF

        # Fragment the message if necessary
        chunks = [
            message[i : i + MESSAGE_SIZE] for i in range(0, len(message), MESSAGE_SIZE)
        ]
        total_chunks = len(chunks)

        peers = list(self._peers)
        for peer in peers:
            is_peer = peer.ip == self.ip and peer.port == self.port
            if is_peer or peer.ip == "0.0.0.0":
                # Do not send the message to itself
                # Do not send the message to unhealthy peers
                continue

            try:
                for seq_num, chunk in enumerate(chunks):
                    # Pack the request ID, sequence number, and chunk into a message
                    data = (
                        struct.pack("!I H H", request_id, seq_num, total_chunks) + chunk
                    )

                    # Send the data
                    self.sock.sendto(data, (peer.ip, peer.port))

                # Send an empty packet with a special sequence number to indicate the end of the message
                end_of_message = struct.pack("!I H H", request_id, 0xFFFF, total_chunks)
                self.sock.sendto(end_of_message, (peer.ip, peer.port))
            except socket.gaierror:
                peer.status = "unhealthy"
                # new_peer = Peer.copy(peer)
                # self._update_peer(peer, new_peer)

    def sendto(self, message_type, message, peer: Peer):
        """
        Send a message to all known peers.
        """
        # Format the message
        formatted_message = f"{message_type}:{message}"

        # Encode the message
        message = formatted_message.encode()

        # Define request ID and sequence number
        request_id = int(time.time() * 1000) & 0xFFFFFFFF

        # Fragment the message if necessary
        chunks = [
            message[i : i + MESSAGE_SIZE] for i in range(0, len(message), MESSAGE_SIZE)
        ]
        total_chunks = len(chunks)

        is_peer = peer.ip == self.ip and peer.port == self.port
        if is_peer or peer.ip == "0.0.0.0":
            # Do not send the message to itself
            # Do not send the message to unhealthy peers
            return

        try:
            if peer.ip in [
                "158.220.82.181",
                "167.86.79.86",
            ]:
                bt.logging.info(
                    f"[{LOGGING_PREFIX}] Send event {message_type} to {peer.ip}:{peer.port}"
                )

            for seq_num, chunk in enumerate(chunks):
                # Pack the request ID, sequence number, and chunk into a message
                data = struct.pack("!I H H", request_id, seq_num, total_chunks) + chunk

                # Send the data
                self.sock.sendto(data, (peer.ip, peer.port))

            # Send an empty packet with a special sequence number to indicate the end of the message
            end_of_message = struct.pack("!I H H", request_id, 0xFFFF, total_chunks)
            self.sock.sendto(end_of_message, (peer.ip, peer.port))
        except socket.gaierror:
            peer.status = "unhealthy"
            # new_peer = Peer(*peer, status="unhealthy")
            # self._update_peer(peer, new_peer)

    def subscribe(self, event_name: str, callback):
        if event_name not in self._events:
            self._events[event_name] = []

        if callback not in self._events[event_name]:
            self._events[event_name].append(callback)

    def unsubscribe(self, event_name: str, callback):
        if callback in self._events[event_name]:
            self._events[event_name].remove(callback)

        if not self._events[event_name]:
            del self._events[event_name]

    def _at_least_one_neuron_up(self):
        return (
            len([x for x in self._peers if x.status == "healthy" and x.uid != self.uid])
            > 0
        )

    def _create_discovery_message(self):
        peer = next((x for x in self._peers if x.uid == self.uid), None)
        bt.logging.info(f"[Network] Create Discovery Message {peer}")
        return json.dumps(
            {
                "uid": peer.uid,
                "ip": peer.ip,
                "port": peer.port,
                "version": peer.version,
            }
        )

    def _add_peer(self, peer: Peer):
        if peer in self._peers:
            return

        self._peers.add(peer)

    def _remove_peer(self, peer):
        if peer not in self._peers:
            return

        self._peers.remove(peer)

    def _update_peer(self, old_peer, new_peer):
        # Remove the peer
        self._remove_peer(old_peer)

        # Add the new peer
        self._add_peer(new_peer)

    def _handle_message(self, message: str, addr):
        if len(message) == 0:
            return

        details = message.split(":", 1)
        event_name = details[0]
        message_content = details[1]

        # Get the peer that send the message
        peer = next((x for x in self._peers if x.ip == addr[0]), None)
        neuron_uid = peer.uid if peer else None

        if event_name == "DISCOVERY":
            self._process_discovery(message_content)
        elif event_name == "DISCOVERY#ACK":
            self._process_discovery_ack(message_content, addr)
        elif event_name == "UNDISCOVERY":
            self._process_undiscovery(message_content)

        if event_name not in self._events:
            # Not handler for that event, so no need to do anything
            return

        handlers = self._events.get(event_name, [])
        for handler in handlers:
            handler(message=message_content, neuron_uid=neuron_uid)

    def _process_discovery(self, message):
        # Instanciate peer
        content = json.loads(message)

        # Get the new Peer
        new_peer = Peer(**content, status="healthy")

        # Get the previous peer
        old_peer = next((x for x in self._peers if x.uid == new_peer.uid), None)

        # Update the peer
        self._update_peer(old_peer, new_peer)

        # Send your peer to the sender
        message = self._create_discovery_message()
        self.sendto("DISCOVERY#ACK", message, new_peer)

    def _process_discovery_ack(self, message, addr):
        # Instanciate peer
        content = json.loads(message)

        # Get the new Peer
        new_peer = Peer(**content, status="healthy")

        # Override the ip from the socket itself as we do not trust validators
        new_peer.ip = addr[0]

        # Get the previous peer
        old_peer = next((x for x in self._peers if x.uid == new_peer.uid), None)

        # Update the peer
        self._update_peer(old_peer, new_peer)

    def _process_undiscovery(self, message):
        # Instanciate peer
        content = json.loads(message)

        # Get the new Peer
        new_peer = Peer(**content, status="unhealthy")

        # Get the previous peer
        old_peer = next((x for x in self._peers if x.uid == new_peer.uid), None)

        # Update the peer
        self._update_peer(old_peer, new_peer)
