#!/usr/bin/env python3

# Copyright (c) 2016 Erik Johansson <erik@ejohansson.se>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

from datetime import date

import http.client as http
import http.cookies as cookies
import json
import logging
import re
import urllib.parse


class MonthEnergy:
    def __init__(self, data):
        self.data = data

    def get_day_energy(self, date):
        total = None
        hours = 24 * [None]

        for entry in self.data["series"][0]["data"]:
            if entry and date.day == int(entry["name"]):
                total = int(entry["y"] * 1000)
                break

        if total is None:
            return (total, hours)

        for entry in self.data["drilldown"]["series"]:
            if entry and date.day == int(entry["name"]):
                for h in entry["data"]:
                    hour = int(h["name"])
                    assert hour >= 0 and hour <= 23
                    assert h["y"] >= 0
                    hours[hour] = int(h["y"] * 1000)
        return (total, hours)


class GoteborgEnergi:
    SERVER = "elavtal.goteborgenergi.se"
    LOGIN_PATH = "/din-sida-info/logga-in/"
    LOGOUT_PATH = "/din-sida/elforbrukning-och-elavtal/Logout/"
    ENERGY_PATH = "/din-sida/elforbrukning-och-elavtal/"
    BY_HOUR_PATH = "/din-sida/elforbrukning-och-elavtal/GetConsumptionByHour/"

    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.cookies = None
        self.headers = {"Accept": "*/*"}

    def _set_cookies(self, response):
        if not self.cookies:
            self.cookies = cookies.SimpleCookie()

        for c in response.getheader("Set-Cookie", "").split(", "):
            self.cookies.load(c)

        cookie = self.cookies.output(attrs={}, header="", sep=";")
        self.headers["Cookie"] = cookie.lstrip()

    def log_in(self):
        conn = http.HTTPSConnection(GoteborgEnergi.SERVER)

        conn.request("GET", GoteborgEnergi.LOGIN_PATH)
        response = conn.getresponse()

        logging.debug(
            "Get %s: %d (%s)",
            GoteborgEnergi.LOGIN_PATH,
            response.status,
            response.reason,
        )
        if response.status != http.OK:
            raise Exception("Failed to get login page")

        self._set_cookies(response)

        match = re.search(
            r'name="__RequestVerificationToken" type="hidden" value="([^"]+)',
            response.read().decode("utf-8"),
        )
        if not match:
            raise Exception("Failed to detect login token")

        params = urllib.parse.urlencode(
            {
                "ReturnUrl": "",
                "KeepMeLoggedIn": "false",
                "Username": self.username,
                "Password": self.password,
                "__RequestVerificationToken": match.group(1),
            }
        )

        headers = self.headers.copy()
        headers["Content-type"] = "application/x-www-form-urlencoded"

        conn.request("POST", GoteborgEnergi.LOGIN_PATH, params, headers)
        response = conn.getresponse()

        logging.debug(
            "Post %s: %d (%s)",
            GoteborgEnergi.LOGIN_PATH,
            response.status,
            response.reason,
        )
        if response.status != http.FOUND:
            raise Exception("Failed to log in")

        self._set_cookies(response)
        conn.close()

    def log_out(self):
        conn = http.HTTPSConnection(GoteborgEnergi.SERVER)

        conn.request("GET", GoteborgEnergi.LOGOUT_PATH, headers=self.headers)
        response = conn.getresponse()

        logging.debug(
            "Get %s: %d (%s)",
            GoteborgEnergi.LOGOUT_PATH,
            response.status,
            response.reason,
        )
        if response.status != http.FOUND:
            raise Exception("Faild to log out")

        self.cookies = None
        conn.close()

    def get_month_energy(self, podid, date):
        conn = http.HTTPSConnection(GoteborgEnergi.SERVER)

        path = "%s?%s" % (
            GoteborgEnergi.ENERGY_PATH,
            urllib.parse.urlencode({"podid": podid}),
        )
        conn.request("GET", path, headers=self.headers)
        response = conn.getresponse()

        logging.debug("Get %s: %d (%s)", path, response.status, response.reason)
        if response.status != http.OK:
            raise Exception("Faild to load energy page")

        match = re.search(
            r'<form.*? id="get-consumption-form" .*?'
            '<input name="__RequestVerificationToken" .*? value="([^"]+)"',
            response.read().decode("utf-8"),
            re.S,
        )
        if not match:
            raise Exception("Failed to detect energy token")

        params = urllib.parse.urlencode(
            {
                "PodId": podid,
                "year": date.year,
                "month": date.month,
                "__RequestVerificationToken": match.group(1),
            }
        )

        headers = self.headers.copy()
        headers["Content-type"] = "application/x-www-form-urlencoded"

        conn.request("POST", GoteborgEnergi.BY_HOUR_PATH, params, headers)
        response = conn.getresponse()

        logging.debug(
            "Post %s: %d (%s)",
            GoteborgEnergi.BY_HOUR_PATH,
            response.status,
            response.reason,
        )
        if response.status != http.OK:
            raise Exception("Failed to get by hour energy")

        data = json.loads(response.read().decode("utf-8"))
        conn.close()

        return MonthEnergy(data)


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s", level=logging.DEBUG
    )
    import sys

    ge = GoteborgEnergi(sys.argv[1], sys.argv[2])
    ge.log_in()
    energy = ge.get_month_energy(sys.argv[3], date.today())
    print(energy.get_day_energy(date.today()))
    ge.log_out()
