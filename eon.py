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

from datetime import timedelta

import html.parser
import re
import requests
import urllib.parse


class Error(Exception):
    pass


class LogInError(Error):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class ParseError(Error):
    def __init__(self, message):
        self.message = message


class EonMonthEnergyHtmlParser(html.parser.HTMLParser):
    def __init__(self, date):
        super().__init__()
        self.days_in_month = (
            (date + timedelta(days=32 - date.day)).replace(day=1)
            - timedelta(days=1)
        ).day

    def parse(self, data):
        self.values = []
        self.day = None
        self.in_td = False
        self.done = False

        self.feed(data)
        self.close()

        if len(self.values) != self.days_in_month:
            raise ParseError("Expected %d values, got %d" % (
                self.days_in_month, len(self.values)))
        for day in self.values:
            if len(day) != 24:
                raise ParseError("Expected 24 hour values, got %d" % len(day))

        return self.values

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == 'td':
            assert not self.in_td
            self.in_td = True

    def handle_endtag(self, tag):
        if tag == 'td':
            self.in_td = False
        elif tag == 'tr':
            self.day = None
        elif tag == 'table' and self.values:
            self.done = True

    def handle_data(self, data):
        if self.done or not self.in_td:
            return

        if self.day is None:
            match = re.match(r'([0-9]{2})X?', data)
            if match is not None:
                self.day = int(match.group(0).lstrip("0"))
                self.values.append(())
                assert len(self.values) == self.day
        elif len(self.values[self.day - 1]) < 24:
            data = data.strip()
            if re.match(r'-|[0-9.]+', data):
                # Convert kWh -> Wh if there is a value
                value = None if data == '-' else int(float(data) * 1000)
                self.values[self.day - 1] += (value,)


class Eon:
    MY_PAGES_SERVER = 'minasidor.eon.se'

    LOGIN_PATH = '/eon-online/loginservlet'
    LOGOUT_PATH = '/eon-online/logoutservlet'
    MONTH_ENERGY_PATH = '/eon-online/eon.consumption.month.sap'

    def __init__(self, user_id, password, user_id_type=1):
        """Class to access my pages on eon.se

        user_id_type identifies the type of user_id: 1 = e-mail, 2 = social
        security number (personnummer), 4 = customer number

        """
        self.user_id = user_id
        self.password = password
        self.user_id_type = user_id_type

        self._session = None

    def _build_url(self, path):
        return 'https://%s%s' % (Eon.MY_PAGES_SERVER, path)

    def log_in(self):
        self._session = requests.Session()

        headers = {'Content-Type': 'application/json; charset=utf-8'}
        data = {'userIdType': self.user_id_type,
                'userId': self.user_id,
                'password': self.password,
                'cookies': 1}

        req = self._session.post(self._build_url(Eon.LOGIN_PATH),
                                 data=urllib.parse.urlencode(data))
        req.raise_for_status()
        response = req.json()

        status = int(response['Status'])
        if status == 1:
            return response['Customer']

        raise LogInError(status,
                         {2: 'Account locked',
                          3: 'Account not activated',
                          4: 'Account no longer active',
                          5: 'Wrong username and/or password',
                          6: 'Initial password must be changed',
                          7: 'No account for customer'
                         }.get(status, 'Unknown status code'))

    def log_out(self):
        assert self._session

        req = self._session.post(self._build_url(Eon.LOGOUT_PATH),
                                 allow_redirects=False)
        req.raise_for_status()

        self._session = None

    def _get_month_energy(self, installation_id, date, role, type):
        assert self._session

        data = {'radioChosen': 'KWH',
                'role': role,
                'type': type,
                'installationSelector': installation_id,
                'year': date.strftime("%Y"),
                'month': date.strftime("%m")}

        req = self._session.post(
            self._build_url(Eon.MONTH_ENERGY_PATH),
            data=data)
        req.raise_for_status()

        parser = EonMonthEnergyHtmlParser(date)
        return parser.parse(req.text)

    def get_month_import(self, installation_id, date):
        """Get energy imported during the month given by date.

        To get installation_id, log in to Mina Sidor and select "My
        consumption". installation_id is the value for installationSelector
        given in the link "Energi- och Effektstatistik".

        """
        return self._get_month_energy(installation_id, date, '01', 'P')

    def get_month_export(self, installation_id, date):
        """Get energy exported during the month given by date.

        See get_month_import for description on how to get installation id.
        """
        return self._get_month_energy(installation_id, date, '03', 'G')


if __name__ == '__main__':
    from datetime import date
    import sys

    eon = Eon(sys.argv[1], sys.argv[2])

    c = eon.log_in()
    for a in c['Accounts']:
        print("%s account: %s|%s|%s|%s|A" % (
            {'01': 'import', '03': 'export'}.get(a['Kofizsd'], "unknown"),
            a['vkont'], '?', a['Premise'], a['Anlage']))
    if len(sys.argv) > 3:
        print(eon.get_month_import(sys.argv[3], date.today()))
    if len(sys.argv) > 4:
        print(eon.get_month_export(sys.argv[4], date.today()))

    eon.log_out()
