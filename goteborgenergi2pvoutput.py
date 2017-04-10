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

SHOULDER_HOURS_1 = slice(6, 8)
PEAK_HOURS = slice(8, 17)
HIGH_SHOULDER_HOURS = slice(17, 20)
SHOULDER_HOURS_2 = slice(20, 23)

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
        if (output.import_peak is None
            or (output.import_peak + output.import_off_peak
                + output.import_shoulder + output.import_high_shoulder) == 0):
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
        energy_imp = imp.get_day_energy(date)
        energy_exp = exp.get_day_energy(date)
        if energy_imp[0] is None or energy_exp[0] is None:
            continue
        logging.debug("Found export %d Wh and import %d Wh for %s",
                      energy_exp[0], energy_imp[0], date.strftime("%Y-%m-%d"))
        imp_peak = sum(energy_imp[1][PEAK_HOURS])
        imp_shoulder = sum(energy_imp[1][SHOULDER_HOURS_1]) \
                       + sum(energy_imp[1][SHOULDER_HOURS_2])
        imp_high_shoulder = sum(energy_imp[1][HIGH_SHOULDER_HOURS])
        imp_off_peak = energy_imp[0] \
                       - sum([imp_peak, imp_shoulder, imp_high_shoulder])
        output = DefaultOutput._replace(
            date=date,
            exported=energy_exp[0],
            import_peak=imp_peak,
            import_off_peak=imp_off_peak,
            import_shoulder=imp_shoulder,
            import_high_shoulder=imp_high_shoulder)
        # Must explicity set consumption if nothing was generated as pvoutput
        # doesn't calculate it in that case.
        if output.exported == 0:
            outputs = pvoutput.get_output(date_from=date, date_to=date)
            if outputs and outputs[0].generated == 0:
                output = output._replace(consumption=energy_imp[0])
        pvoutput.add_output(output)

    ge.log_out()


if __name__ == '__main__':
    main()
