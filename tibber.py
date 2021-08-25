#!/usr/bin/env python3

# Copyright (c) 2020 Erik Johansson <erik@ejohansson.se>
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

import logging
import requests
from collections import namedtuple
from datetime import datetime
from enum import Enum


Consumption = namedtuple("Consumption", "date consumption unit_price cost")
Production = namedtuple("Production", "date production unit_price profit")

PageInfo = namedtuple("PageInfo", "start_cursor end_cursor has_previous has_next")

EnergyResolution = Enum("EnergyResolution", "HOURLY DAILY WEEKLY MONTHLY ANNUAL")


class Client:
    ENDPOINT = "https://api.tibber.com/v1-beta/gql"

    class Authentication(requests.auth.AuthBase):
        def __init__(self, token):
            self.token = token

        def __call__(self, req):
            req.headers["Authorization"] = f"Bearer {self.token}"
            return req

    def __init__(self, token):
        self._session = requests.Session()
        self._session.auth = self.Authentication(token)

    def do_query(self, query):
        query = "{ viewer { %s } }" % query
        logging.debug(query)
        req = self._session.post(self.ENDPOINT, json={"query": query})
        req.raise_for_status()
        return req.json()["data"]["viewer"]

    def get_homes(self):
        res = self.do_query("homes { id }")
        return [Home(self, h["id"]) for h in res["homes"]]


class Home:
    PAGE_SIZE = {
        EnergyResolution.HOURLY: 48,
        EnergyResolution.DAILY: 14,
        EnergyResolution.WEEKLY: 4,
        EnergyResolution.MONTHLY: 6,
        EnergyResolution.ANNUAL: 5,
    }

    def __init__(self, client, home_id):
        self._client = client
        self.home_id = home_id

    def do_query(self, query):
        res = self._client.do_query(f'home(id: "{self.home_id}") {{ {query} }}')
        return res["home"]

    def get_data(
        self, consumption, resolution, first=None, last=None, before=None, after=None,
    ):
        # Not setting filterEmptyNodes: true as that removes nodes with
        # e.g. consumption == 0 (and not just == null, i.e. unknown).
        params = [f"resolution: {resolution.name}"]
        if first is not None:
            params.append(f"first: {first}")
        if last is not None:
            params.append(f"last: {last}")
        if before is not None:
            params.append(f'before: "{before}"')
        if after is not None:
            params.append(f'after: "{after}"')

        name = "consumption" if consumption else "production"
        money = "cost" if consumption else "profit"

        query = (
            f"{name}({', '.join(params)}) {{"
            " pageInfo { startCursor endCursor hasPreviousPage hasNextPage }"
            f" nodes {{ from {name} unitPrice {money} }}"
            " }"
        )
        res = self.do_query(query)
        res = res[name]

        def to_tuple(cls, node):
            return cls(
                datetime.strptime(node["from"], "%Y-%m-%dT%H:%M:%S.%f%z"),
                int(node[name] * 1000),
                node["unitPrice"],
                round(node[money], 2),
            )

        pi = res["pageInfo"]
        page_info = PageInfo(
            pi["startCursor"], pi["endCursor"], pi["hasPreviousPage"], pi["hasNextPage"]
        )
        cls = Consumption if consumption else Production
        return (
            [to_tuple(cls, n) for n in res["nodes"] if n[name] is not None],
            page_info,
        )

    def data_generator(self, consumption, resolution, reverse):
        kwargs = {
            "consumption": consumption,
            "resolution": resolution,
        }
        kwargs["last" if reverse else "first"] = self.PAGE_SIZE[resolution]

        page_info = PageInfo(None, None, True, True)
        has_more = "has_previous" if reverse else "has_next"
        cursor = "start_cursor" if reverse else "end_cursor"

        while getattr(page_info, has_more):
            kwargs["before" if reverse else "after"] = getattr(page_info, cursor)
            entries, page_info = self.get_data(**kwargs)
            if reverse:
                entries = reversed(entries)
            for entry in entries:
                yield entry

    def consumption(self, resolution, reverse=False):
        yield from self.data_generator(True, resolution, reverse)

    def production(self, resolution, reverse=False):
        yield from self.data_generator(False, resolution, reverse)


if __name__ == "__main__":
    from datetime import timedelta
    from http.client import HTTPConnection
    import logging
    import sys

    logging.basicConfig()
    logging.getLogger().setLevel(logging.DEBUG)

    HTTPConnection.debuglevel = 1

    client = Client(sys.argv[1])
    for home in client.get_homes():
        # for c in home.consumption(EnergyResolution.ANNUAL, reverse=False):
        #     print(c)
        # for p in home.production(EnergyResolution.ANNUAL):
        #     print(p)
        end = datetime.now() - timedelta(days=5)
        for c in home.consumption(EnergyResolution.HOURLY, reverse=True):
            if c.date < end.astimezone(c.date.tzinfo):
                break
            print(c)
