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

from goteborgenergi import GoteborgEnergi
from pvoutput import PvOutput, DefaultOutput

from getpass import getpass

import argparse
import configparser
import datetime
import logging


def main():
    logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s')

    parser = argparse.ArgumentParser(
        description="Connect goteborgenergi.se to PVoutput.org")
    parser.add_argument('config', help="Configuration file to use")
    parser.add_argument('-d', '--debug', help="Enable debug output",
                        action="store_true")
    parser.add_argument('-n', '--dry-run', help="Don't send any data",
                        action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    config = configparser.ConfigParser()
    config['goteborgenergi'] = {}
    config['pvoutput'] = {}
    config.read(args.config)

    modified = False
    if not config['goteborgenergi'].get('username'):
        config['goteborgenergi']['username'] = \
            input("goteborgenergi.se username: ")
        modified = True
    if not config['goteborgenergi'].get('password'):
        config['goteborgenergi']['password'] = \
            getpass("goteborgenergi.se password: ")
        modified = True
    if not config['goteborgenergi'].get('import'):
        config['goteborgenergi']['import'] = input("import podid: ")
        modified = True
    if not config['goteborgenergi'].get('export'):
        config['goteborgenergi']['export'] = input("export podid: ")
        modified = True

    if not config['pvoutput'].get('apikey'):
        config['pvoutput']['apikey'] = input("PVOutput API key: ")
        modified = True
    if not config['pvoutput'].get('systemid'):
        config['pvoutput']['systemid'] = input("PVOutput System Id: ")
        modified = True

    if modified:
        with open(args.config, 'w') as configfile:
            config.write(configfile)

    missing = []

    pvoutput = PvOutput(config['pvoutput']['apikey'],
                        config['pvoutput']['systemid'],
                        args.dry_run)

    for output in pvoutput.get_output(limit=35):
        if output.date == datetime.date.today():
            continue
        if output.import_peak is None:
            missing.append(output.date)

    logging.debug("Missing export/import for: %s",
                  [d.strftime("%Y-%m-%d") for d in missing])
    if not missing:
        return

    ge = GoteborgEnergi(config['goteborgenergi']['username'],
                        config['goteborgenergi']['password'])
    ge.log_in()

    imp = None
    exp = None
    month = None
    for date in missing:
        if date.month != month:
            imp = ge.get_month_energy(config['goteborgenergi']['import'], date)
            exp = ge.get_month_energy(config['goteborgenergi']['export'], date)
            month = date.month
        if imp[date.day - 1] is None or exp[date.day - 1] is None:
            continue
        logging.debug("Found export/import for %s", date.strftime("%Y-%m-%d"))
        output = DefaultOutput._replace(
            date=date,
            exported=exp[date.day - 1] * 1000,
            import_peak=imp[date.day - 1] * 1000)
        pvoutput.add_output(output)

    ge.log_out()


if __name__ == '__main__':
    main()
