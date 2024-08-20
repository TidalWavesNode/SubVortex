import requests
import bittensor as bt
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple


@dataclass
class DelegatesDetails:
    name: str
    url: str
    description: str
    signature: str

    @classmethod
    def from_json(cls, json: Dict[str, any]) -> "DelegatesDetails":
        return cls(
            name=json["name"],
            url=json["url"],
            description=json["description"],
            signature=json["signature"],
        )


def get_delegates_details(url: str):
    try:
        response = requests.get(url)

        if response.status_code == 200:
            all_delegates: Dict[str, Any] = response.json()
            all_delegates_details = {}
            for delegate_hotkey, delegates_details in all_delegates.items():
                all_delegates_details[delegate_hotkey] = DelegatesDetails.from_json(
                    delegates_details
                )
            return all_delegates_details
        else:
            return {}
    except:
        pass
