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

from collections import namedtuple
from datetime import datetime

import http.client as http
import json
import logging
import urllib.parse


Data = namedtuple("Data", ["channelid", "startdate", "enddate", "intervaltype", "data"])
DataEntry = namedtuple(
    "DataEntry", ["avgpower", "energy", "temp_out", "time_start", "time_end"]
)
DataNow = namedtuple("DataNow", ["channelid", "createddate", "power"])


def parse_datetime(date):
    return datetime.strptime(date, "%Y-%m-%dT%H:%M:%S")


class EliqOnline:
    def __init__(self, access_token, server="my.eliq.io", port=http.HTTPS_PORT):
        self.access_token = access_token
        self.server = server
        self.port = port

    def _send_request(self, url, params):
        # Don't include access token in log
        params["accesstoken"] = "%s.../%d" % (
            self.access_token[0:4],
            len(self.access_token),
        )
        logging.debug("GET %s: %s", url, params)
        params["accesstoken"] = self.access_token

        conn = http.HTTPSConnection(self.server, self.port)
        conn.request("GET", "%s?%s" % (url, urllib.parse.urlencode(params)))

        response = conn.getresponse()
        status = response.status
        reason = response.reason
        body = response.read().decode("utf-8")
        logging.debug("HTTP response: %d (%s): %s", status, reason, body)

        if status != http.OK and status != http.CREATED:
            raise Exception("HTTP request to %s failed" % self.server)

        conn.close()
        return (status, reason, body)

    def get_data_now(self, channel_id=None):
        params = {}
        if channel_id is not None:
            params["channelid"] = channel_id

        (_, _, body) = self._send_request("/api/datanow", params)
        result = json.loads(body)

        return DataNow(
            result["channelid"], parse_datetime(result["createddate"]), result["power"]
        )

    def _get_data(self, interval_type, start, end=None, channel_id=None):
        date_format = {"day": "%Y-%m-%d", "6min": "%Y-%m-%dT%H:%M"}[interval_type]

        params = {
            "intervaltype": interval_type,
            "startdate": start.strftime(date_format),
        }
        if end is not None:
            params["enddate"] = end.strftime(date_format)
        if channel_id is not None:
            params["channelid"] = channel_id

        (_, _, body) = self._send_request("/api/data", params)
        result = json.loads(body)

        data = Data(
            result["channelid"],
            parse_datetime(result["startdate"]),
            parse_datetime(result["enddate"]),
            result["intervaltype"],
            [],
        )
        for entry in result["data"]:
            data.data.append(
                DataEntry(
                    entry["avgpower"],
                    entry["energy"],
                    entry["temp_out"],
                    parse_datetime(entry["time_start"]),
                    parse_datetime(entry["time_end"]),
                )
            )
        return data

    def get_day_data(self, start, end=None, channel_id=None):
        return self._get_data("day", start, end, channel_id)

    def get_6min_data(self, start, end=None, channel_id=None):
        return self._get_data("6min", start, end, channel_id)
