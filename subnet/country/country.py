import os
import json
import copy
import time
import threading
import ipaddress
import bittensor as bt
from os import path
from typing import List

from subnet.country.country_constants import (
    COUNTRY_URL,
    COUNTRY_LOGGING_NAME,
    COUNTRY_SLEEP,
    COUNTRY_ATTEMPTS,
)
from subnet.file.file_google_drive_monitor import FileGoogleDriveMonitor
from subnet.validator.localisation import (
    get_country_by_country_is,
    get_country_by_ip_api,
    get_country_by_ipinfo_io,
    get_country_by_my_api,
)

here = path.abspath(path.dirname(__file__))


class CountryService:
    def __init__(self, netuid: int):
        self._lock = threading.Lock()
        self._data = {}
        self._subregions = []
        self.first_try = True

        self.provider = FileGoogleDriveMonitor(
            logger_name=COUNTRY_LOGGING_NAME,
            file_url=COUNTRY_URL.get(netuid),
            check_interval=COUNTRY_SLEEP,
            callback=self.run,
        )

    def _is_custom_api_enabled(self):
        with self._lock:
            return self._data.get("enable_custom_api", True)

    def get_subregions(self):
        subregions = []

        try:
            filename = path.join(here, "../subregions.json")
            if not os.path.exists(filename):
                return subregions

            with open(filename, "r") as rfile:
                subregions = json.load(rfile)
        except:
            pass

        return subregions

    def get_last_modified(self):
        with self._lock:
            return self._data.get("last-modified")

    def get_locations(self) -> List[str]:
        with self._lock:
            localisations = self._data.get("localisations", {})
            return copy.deepcopy(localisations)

    def get_ipv4(self, ip):
        try:
            # First, try to interpret the input as an IPv4 address
            ipv4 = ipaddress.IPv4Address(ip)
            return str(ipv4)
        except ipaddress.AddressValueError:
            pass

        try:
            # Next, try to interpret the input as an IPv6 address
            ipv6 = ipaddress.IPv6Address(ip)
            if ipv6.ipv4_mapped:
                return str(ipv6.ipv4_mapped)
        except ipaddress.AddressValueError:
            pass

        return ip

    def get_country(self, ip: str):
        """
        Get the country code of the ip
        """
        ip_ipv4 = self.get_ipv4(ip)

        country = None
        with self._lock:
            overrides = self._data.get("overrides") or {}
            country = overrides.get(ip_ipv4)

        if country:
            return self._subregions.get(country), country

        country, reason1 = (
            get_country_by_my_api(ip_ipv4)
            if self._is_custom_api_enabled()
            else (None, None)
        )
        if country:
            return self._subregions.get(country), country

        country, reason2 = get_country_by_country_is(ip_ipv4)
        if country:
            return self._subregions.get(country), country

        country, reason3 = get_country_by_ip_api(ip_ipv4)
        if country:
            return self._subregions.get(country), country

        country, reason4 = get_country_by_ipinfo_io(ip_ipv4)
        if country:
            return self._subregions.get(country), country

        bt.logging.warning(
            f"Could not get the country of the ip {ip_ipv4}: Api 1: {reason1} / Api 2: {reason2} / Api 3: {reason3} / Api 4: {reason4}"
        )
        return None

    def wait(self):
        """
        Wait until we have execute the run method at least one
        """
        attempt = 1
        while self.first_try and attempt <= COUNTRY_ATTEMPTS:
            bt.logging.debug(
                f"[{COUNTRY_LOGGING_NAME}][{attempt}] Waiting file to be process..."
            )
            time.sleep(1)
            attempt += 1

    def run(self, data):
        with self._lock:
            self._data = data
            self._subregions = self.get_subregions()

        self.first_try = False

        bt.logging.success(
            f"[{COUNTRY_LOGGING_NAME}] File proceed successfully",
        )
