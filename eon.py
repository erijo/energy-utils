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

from datetime import datetime, timedelta

import html.parser
import http.client as http
import http.cookies as cookies
import logging
import re
import urllib.parse


class EonMonthEnergyHtmlParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(self)
        self.values = []
        self.day = None
        self.in_td = False
        self.done = False

    def parse(self, data):
        self.feed(data)
        self.close()
        assert(len(self.values) >= 28)
        for day in self.values:
            assert(len(day) == 24)
        return self.values

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == 'td':
            assert(not self.in_td)
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
                assert(len(self.values) == self.day)
        elif len(self.values[self.day - 1]) < 24:
            if re.match(r'-|[0-9]+', data):
                # Convert kWh -> Wh if there is a value
                value = None if data == '-' else int(data) * 1000
                self.values[self.day - 1] += (value,)


class Eon:
    MY_PAGES_SERVER = 'minasidor.eon.se'
    MY_PAGES_LOGIN_PATH = '/privatkund/Mina-sidor/Inloggning/'
    SAP_SERVER = 'sapuces.eon.se'
    SAP_MONTH_ENERGY_PATH = '/eon-online/eon.consumption.month.sap'

    def __init__(self, user_id, password):
        self.user_id = user_id
        self.password = password
        self.viewstate = None

    def _get_viewstate(self):
        if self.viewstate:
            return self.viewstate
        conn = http.HTTPConnection(Eon.MY_PAGES_SERVER)
        conn.request("GET", Eon.MY_PAGES_LOGIN_PATH)

        response = conn.getresponse()
        logging.debug("GET viewstate returned %u (%s)",
                      response.status, response.reason)
        if response.status != http.OK:
            raise Exception("Failed to get viewstate")

        body = response.read().decode('utf-8')
        conn.close()

        match = re.search(r'id="__VIEWSTATE" value="([^"]+)"', body)
        assert(match is not None)
        self.viewstate = match.group(1)
        return self.viewstate

    def log_in(self):
        params = {'__VIEWSTATE': self._get_viewstate()}
        params['m$blocks1C2R1$Login$UserIdTypeField'] = 1
        params['m$blocks1C2R1$Login$UserIdField'] = self.user_id
        params['m$blocks1C2R1$Login$PasswordField'] = self.password
        params['m$blocks1C2R1$Login$LoginButton'] = 'Logga in'

        headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain"
        }
        conn = http.HTTPSConnection(Eon.MY_PAGES_SERVER)
        encoded = urllib.parse.urlencode(params)
        conn.request("POST", Eon.MY_PAGES_LOGIN_PATH, encoded, headers)

        response = conn.getresponse()
        logging.debug("Log in returned %u (%s)",
                      response.status, response.reason)
        if response.status != http.FOUND:
            raise Exception("Failed to log in")

        self.cookies = cookies.SimpleCookie(response.getheader('Set-Cookie'))
        conn.close()

    def log_out(self):
        params = {'__VIEWSTATE': self._get_viewstate()}
        params['__EVENTTARGET'] = 'm$ctl01$logoutConfirmButton'
        params['__EVENTARGUMENT'] = ''

        headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain"
        }
        conn = http.HTTPSConnection(Eon.MY_PAGES_SERVER)
        encoded = urllib.parse.urlencode(params)
        conn.request("POST", Eon.MY_PAGES_LOGIN_PATH, encoded, headers)

        response = conn.getresponse()
        logging.debug("Log out returned %u (%s)",
                      response.status, response.reason)
        if response.status != http.FOUND:
            raise Exception("Failed to log out")

        self.cookies = None
        conn.close()

    def _get_month_energy(self, installation, date, role, type):
        params = {'radioChosen': 'KWH', 'role': role, 'type': type}
        params['installationSelector'] = installation
        params['year'] = date.strftime("%Y")
        params['month'] = date.strftime("%m")

        cookie = self.cookies.output(attrs={}, header='', sep=';')
        headers = {
            'Cookie': cookie.lstrip(),
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain"
        }
        conn = http.HTTPSConnection(Eon.SAP_SERVER)
        encoded = urllib.parse.urlencode(params)
        conn.request("POST", Eon.SAP_MONTH_ENERGY_PATH, encoded, headers)

        response = conn.getresponse()
        logging.debug("GET month energy for %s returned %u (%s)",
                      installation, response.status, response.reason)
        if response.status != http.OK:
            raise Exception("Failed to get month energy")

        body = response.read().decode('iso8859-15')
        conn.close()
        parser = EonMonthEnergyHtmlParser()
        return parser.parse(body)

    def get_month_import(self, installation, date):
        return self._get_month_energy(installation, date, '01', 'P')

    def get_month_export(self, installation, date):
        return self._get_month_energy(installation, date, '03', 'G')
