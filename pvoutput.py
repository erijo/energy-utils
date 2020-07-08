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

import logging
import requests
import time
import urllib.parse


Output = namedtuple(
    "Output",
    [
        "date",
        "generated",
        "efficiency",
        "exported",
        "import_peak",
        "import_off_peak",
        "import_shoulder",
        "import_high_shoulder",
        "consumption",
    ],
)
DefaultOutput = Output(None, None, None, None, None, None, None, None, None)

Extended = namedtuple(
    "Extended",
    [
        "date",
        "extended_1",
        "extended_2",
        "extended_3",
        "extended_4",
        "extended_5",
        "extended_6",
    ],
)

Status = namedtuple(
    "Status",
    [
        "datetime",
        "energy_generation",
        "power_generation",
        "energy_consumption",
        "power_consumption",
        "efficiency",
        "temperature",
        "voltage",
        "extended_1",
        "extended_2",
        "extended_3",
        "extended_4",
        "extended_5",
        "extended_6",
    ],
)
DefaultStatus = Status(
    None, None, None, None, None, None, None, None, None, None, None, None, None, None
)


def value(obj, field, converter):
    v = getattr(obj, field)
    if v is None:
        return ""
    return str(converter(v))


def result(entry, index, converter):
    v = entry[index]
    if v == "NaN":
        return None
    return converter(v)


class PvOutput:
    def __init__(self, apikey, systemid, dry_run=False, donation_mode=None):
        self.donation_mode = donation_mode
        self.dry_run = dry_run
        self.last_request_time = 0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Pvoutput-Apikey": apikey,
                "X-Pvoutput-SystemId": systemid,
                "Content-type": "application/x-www-form-urlencoded",
                "Accept": "text/plain",
            }
        )

    def send_request(self, url, params, ignore_dry_run=False):
        params = {k: v for k, v in params.items() if v is not ""}

        if self.dry_run and not ignore_dry_run:
            logging.debug("POST to %s: %s (dry-run)", url, params)
            return (requests.codes.ok, "OK", "")

        time_since_last_request = time.time() - self.last_request_time
        if time_since_last_request < 10:
            time.sleep(10 - time_since_last_request)

        logging.debug("POST to %s: %s", url, params)
        headers = None
        if self.donation_mode is None:
            headers = {"X-Rate-Limit": "1"}

        req = self.session.post(
            "https://pvoutput.org%s" % url, headers=headers, data=params
        )

        status = req.status_code
        reason = req.reason
        body = req.text
        logging.debug(
            "HTTP response: %d (%s): %s",
            status,
            reason,
            body if len(body) < 120 else body[0:100] + " ... " + body[-20:],
        )

        if self.donation_mode is None:
            mode = req.headers["X-Rate-Limit-Limit"]
            self.donation_mode = mode == "300"
            logging.debug(
                "Donation mode: %s (limit %s/%s)",
                self.donation_mode,
                req.headers["X-Rate-Limit-Remaining"],
                mode,
            )

        self.last_request_time = time.time()
        return (status, reason, body)

    def add_output(self, output):
        params = {
            "d": output.date.strftime("%Y%m%d"),
            "g": value(output, "generated", int),
            "e": value(output, "exported", int),
            "ip": value(output, "import_peak", int),
            "io": value(output, "import_off_peak", int),
            "is": value(output, "import_shoulder", int),
            "ih": value(output, "import_high_shoulder", int),
            "c": value(output, "consumption", int),
        }

        (status, _, body) = self.send_request("/service/r2/addoutput.jsp", params)
        if status != requests.codes.ok:
            logging.error("Add output failed: %s", body)
            raise Exception("Failed to add output")

    def add_status(self, status, net=None):
        params = {
            "d": status.datetime.strftime("%Y%m%d"),
            "t": status.datetime.strftime("%H:%M"),
            "v1": value(status, "energy_generation", int),
            "v2": value(status, "power_generation", int),
            "v3": value(status, "energy_consumption", int),
            "v4": value(status, "power_consumption", int),
            "v5": value(status, "temperature", float),
            "v6": value(status, "voltage", float),
            "v7": value(status, "extended_1", float),
            "v8": value(status, "extended_2", float),
            "v9": value(status, "extended_3", float),
            "v10": value(status, "extended_4", float),
            "v11": value(status, "extended_5", float),
            "v12": value(status, "extended_6", float),
        }
        if net is not None:
            params["n"] = int(net)

        (status, _, body) = self.send_request("/service/r2/addstatus.jsp", params)
        if status != requests.codes.ok:
            logging.error("Add status failed: %s", body)
            raise Exception("Failed to add status")

    def add_batch_status(self, statuses):
        data = []
        for status in statuses:
            entry = [
                status.datetime.strftime("%Y%m%d"),
                status.datetime.strftime("%H:%M"),
                value(status, "energy_generation", int),
                value(status, "power_generation", int),
                value(status, "energy_consumption", int),
                value(status, "power_consumption", int),
                value(status, "temperature", float),
                value(status, "voltage", float),
                value(status, "extended_1", float),
                value(status, "extended_2", float),
                value(status, "extended_3", float),
                value(status, "extended_4", float),
                value(status, "extended_5", float),
                value(status, "extended_6", float),
            ]
            data.append(",".join(entry).rstrip(","))
        offset = 0
        limit = 100 if self.donation_mode else 30
        while offset < len(data):
            entries = data[offset : offset + limit]
            (status, reason, body) = self.send_request(
                "/service/r2/addbatchstatus.jsp", {"data": ";".join(entries)}
            )
            if status == requests.codes.ok:
                offset += len(entries)
            elif status == requests.codes.bad_request and "Load in progress" in body:
                time.sleep(20)
            else:
                logging.error("Add batch status failed: %s", body)
                raise Exception("Failed to add batch status")

    def get_status(
        self,
        date=None,
        time=None,
        history=False,
        asc=False,
        limit=None,
        time_from=None,
        time_to=None,
        extended=False,
    ):
        params = {
            "d": date.strftime("%Y%m%d") if date else "",
            "t": time.strftime("%H:%M") if time else "",
            "h": 1 if history else 0,
            "asc": 1 if asc else 0,
            "limit": limit if limit is not None else 24 * 12,
            "from": time_from.strftime("%H:%M") if time_from else "",
            "to": time_to.strftime("%H:%M") if time_to else "",
            "ext": 1 if extended else 0,
        }

        (status, _, body) = self.send_request(
            "/service/r2/getstatus.jsp", params, ignore_dry_run=True
        )
        if status != requests.codes.ok:
            logging.error("Get status failed: %s", body)
            raise Exception("Failed to get status")

        statuses = []
        if not body:
            return statuses

        for entry in body.split(";"):
            fields = entry.split(",")
            statuses.append(
                DefaultStatus._replace(
                    datetime=datetime.combine(
                        datetime.strptime(fields[0], "%Y%m%d").date(),
                        datetime.strptime(fields[1], "%H:%M").time(),
                    ),
                    energy_generation=result(fields, 2, int),
                    power_generation=result(fields, 4 if history else 3, int),
                    energy_consumption=result(fields, 7 if history else 4, int),
                    power_consumption=result(fields, 8 if history else 5, int),
                    efficiency=result(fields, 3 if history else 6, float),
                    temperature=result(fields, 9 if history else 7, float),
                    voltage=result(fields, 10 if history else 8, float),
                )
            )
            if extended:
                statuses[-1] = statuses[-1]._replace(
                    extended_1=result(fields, 11 if history else 9, float),
                    extended_2=result(fields, 12 if history else 10, float),
                    extended_3=result(fields, 13 if history else 11, float),
                    extended_4=result(fields, 14 if history else 12, float),
                    extended_5=result(fields, 15 if history else 13, float),
                    extended_6=result(fields, 16 if history else 14, float),
                )
        return statuses

    def get_output(self, date_from=None, date_to=None, limit=None):
        params = {
            "df": date_from.strftime("%Y%m%d") if date_from else "",
            "dt": date_to.strftime("%Y%m%d") if date_to else "",
            "limit": limit if limit is not None else "",
        }

        (status, _, body) = self.send_request(
            "/service/r2/getoutput.jsp", params, ignore_dry_run=True
        )
        if status != requests.codes.ok:
            logging.error("Get output failed: %s", body)
            raise Exception("Failed to get output")

        outputs = []
        if not body:
            return outputs

        for entry in body.split(";"):
            fields = entry.split(",")
            outputs.append(
                DefaultOutput._replace(
                    date=datetime.strptime(fields[0], "%Y%m%d").date(),
                    generated=result(fields, 1, int),
                    efficiency=result(fields, 2, float),
                    exported=result(fields, 3, int),
                    import_peak=result(fields, 10, int),
                    import_off_peak=result(fields, 11, int),
                    import_shoulder=result(fields, 12, int),
                    import_high_shoulder=result(fields, 13, int),
                )
            )
        return outputs

    def get_extended(self, date_from=None, date_to=None, limit=None):
        params = {
            "df": date_from.strftime("%Y%m%d") if date_from else "",
            "dt": date_to.strftime("%Y%m%d") if date_to else "",
            "limit": limit if limit is not None else "",
        }

        (status, _, body) = self.send_request(
            "/service/r2/getextended.jsp", params, ignore_dry_run=True
        )
        if status != requests.codes.ok:
            logging.error("Get extended failed: %s", body)
            raise Exception("Failed to get extended")

        extended = []
        if not body:
            return extended

        for entry in body.split(";"):
            fields = entry.split(",")
            extended.append(
                Extended(
                    date=datetime.strptime(fields[0], "%Y%m%d").date(),
                    extended_1=result(fields, 1, float),
                    extended_2=result(fields, 2, float),
                    extended_3=result(fields, 3, float),
                    extended_4=result(fields, 4, float),
                    extended_5=result(fields, 5, float),
                    extended_6=result(fields, 6, float),
                )
            )

        return extended

    def get_missing(self, date_from, date_to):
        params = {"df": date_from.strftime("%Y%m%d"), "dt": date_to.strftime("%Y%m%d")}

        (status, _, body) = self.send_request(
            "/service/r2/getmissing.jsp", params, ignore_dry_run=True
        )
        if status != requests.codes.ok:
            logging.error("Get missing failed: %s", body)
            raise Exception("Failed to get missing")

        missing = []
        if body:
            for entry in body.split(","):
                missing.append(datetime.strptime(entry, "%Y%m%d").date())
        return missing


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(message)s", level=logging.DEBUG
    )
    import sys

    pvoutput = PvOutput(sys.argv[1], sys.argv[2])
    pvoutput.get_status()
