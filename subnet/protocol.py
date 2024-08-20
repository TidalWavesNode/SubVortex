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

import typing
from subnet.bittensor.synapse import Synapse


class Score(Synapse):
    validator_uid: typing.Optional[int] = None
    miner_uid: int

    # Challenge
    process_time: float

    # Score
    availability: float
    latency: float
    reliability: float
    distribution: float
    score: float
    count: typing.Optional[int] = 0
    reason: typing.Optional[str] = None

    # Returns
    version: typing.Optional[str] = None

    def deserialize(self) -> typing.Optional[str]:
        return self.version


class Miners(Synapse):
    uids: typing.List[int]
    countries: typing.List[str]
    versions: typing.List[str]
    network_status: typing.List[int]
    last_challenges: typing.List[float]
    process_times: typing.List[float]
    final_scores: typing.List[float]
    availability_scores: typing.List[float]
    latency_scores: typing.List[float]
    reliability_scores: typing.List[float]
    distribution_scores: typing.List[float]
    moving_average_scores: typing.List[float]

    def deserialize(self) -> "Miners":
        return self
