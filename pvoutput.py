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
import logging
import time
import urllib.parse


Output = namedtuple('Output', ['datetime',
                               'generated', 'exported', 'consumption'])
DefaultOutput = Output(None, None, None, None)

Status = namedtuple('Status', ['datetime',
                               'energy_generation', 'power_generation',
                               'energy_consumption', 'power_consumption',
                               'temperature', 'voltage'])
DefaultStatus = Status(None, None, None, None, None, None, None)


def value(status, field, converter):
    v = getattr(status, field)
    if v is None:
        return ''
    return str(converter(v))

def result(entry, index, converter):
    v = entry[index]
    if v == 'NaN':
        return None
    return converter(v)


class PvOutput:
    def __init__(self, apikey, systemid, dry_run=False):
        self.dry_run = dry_run
        self.headers = {
            "X-Pvoutput-Apikey": apikey,
            "X-Pvoutput-SystemId": systemid,
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain"
        }

    def send_request(self, url, params, ignore_dry_run=False):
        params = {k: v for k, v in params.items() if v is not ''}
        logging.debug("POST to %s: %s", url, params)
        if self.dry_run and not ignore_dry_run:
            return (http.OK, "OK", "")

        conn = http.HTTPConnection('pvoutput.org')
        encoded = urllib.parse.urlencode(params)
        conn.request("POST", url, encoded, self.headers)

        response = conn.getresponse()
        status = response.status
        reason = response.reason
        body = response.read().decode('utf-8')
        logging.debug(
            "HTTP response: %d (%s): %s", status, reason,
            body if len(body) < 120 else body[0:100] + " ... " + body[-20:])

        conn.close()
        return (status, reason, body)

    def add_output(self, output):
        params = {'d': output.datetime.strftime("%Y%m%d"),
                  'g': value(output, 'generated', int),
                  'e': value(output, 'exported', int),
                  'c': value(output, 'consumption', int)}

        (status, _, _) = self.send_request("/service/r2/addoutput.jsp", params)
        if status != http.OK:
            raise Exception("Failed to add output")

    def add_status(self, status, net=None):
        params = {'d': status.datetime.strftime("%Y%m%d"),
                  't': status.datetime.strftime("%H:%M"),
                  'v1': value(status, 'energy_generation', int),
                  'v2': value(status, 'power_generation', int),
                  'v3': value(status, 'energy_consumption', int),
                  'v4': value(status, 'power_consumption', int),
                  'v5': value(status, 'temperature', float),
                  'v6': value(status, 'voltage', float)}
        if net is not None:
            params['n'] = int(net)

        (status, _, _) = self.send_request("/service/r2/addstatus.jsp", params)
        if status != http.OK:
            raise Exception("Failed to add status")

    def add_batch_status(self, statuses):
        data = []
        for status in statuses:
            entry = [status.datetime.strftime("%Y%m%d"),
                     status.datetime.strftime("%H:%M"),
                     value(status, 'energy_generation', int),
                     value(status, 'power_generation', int),
                     value(status, 'energy_consumption', int),
                     value(status, 'power_consumption', int),
                     value(status, 'temperature', float),
                     value(status, 'voltage', float)]
            data.append(','.join(entry))
        offset = 0
        while offset < len(data):
            entries = data[offset:offset + 30]
            (status, reason, body) = self.send_request(
                "/service/r2/addbatchstatus.jsp",
                {"data": ";".join(entries)})
            if status == http.OK:
                offset += len(entries)
                if offset < len(data):
                    time.sleep(10 if not self.dry_run else 0)
            elif (status == http.BAD_REQUEST and 'Load in progress' in body):
                time.sleep(20 if not self.dry_run else 0)
            else:
                logging.error("Failed to add status batch: %s", body)
                raise Exception("could not add status batch")

    def get_status(self, date=None, history=False, asc=False, limit=None):
        params = {'d': date.strftime("%Y%m%d") if date is not None else '',
                  'h': 1 if history else 0,
                  'asc': 1 if asc else 0,
                  'limit': limit if limit is not None else 24 * 12}

        (status, _, body) = self.send_request("/service/r2/getstatus.jsp",
                                              params, ignore_dry_run=True)
        if status != http.OK:
            raise Exception("Failed to get status")

        statuses = []
        if not body:
            return statuses

        for entry in body.split(";"):
            fields = entry.split(",")
            statuses.append(DefaultStatus._replace(
                datetime=datetime.combine(
                    datetime.strptime(fields[0], "%Y%m%d").date(),
                    datetime.strptime(fields[1], "%H:%M").time()),
                energy_generation=result(fields, 2, int),
                power_generation=result(fields, 4 if history else 3, int),
                energy_consumption=result(fields, 7 if history else 4, int),
                power_consumption=result(fields, 8 if history else 5, int),
                temperature=result(fields, 9 if history else 7, float),
                voltage=result(fields, 10 if history else 8, float)))
        return statuses
