# Energy utils

A collection of python utils related to energy.

## Help classes
- eliq.py: a class for accessing the
  [ELIQ Online API](https://my.eliq.se/knowledge/sv-SE/49-eliq-online/299-eliq-online-api)
- eon.py: a class for retrieving the monthly energy import/export from
  [E.ON](https://www.eon.se/)
- goteborgenergi.py: a class for retrieving the monthly energy import/export
  from "Din sida" (your page) on [Göteborg
  Energi](http://www.goteborgenergi.se/)
- pvoutput.py: a class wrapping the
  [pvoutput.org service API](http://pvoutput.org/help.html#api)
- sma.py: a class for communicating with [SMA inverters](http://www.sma.de/en/)

## Utils
- eliq2pvoutput: send data from ELIQ to pvoutput.org. No longer used by myself
  (due to the fact that the diode on the energy meter blinks for both import
  and export of energy and thus is not as useful as it could have been).
- eon2pvoutput: send data from E.ON to pvoutput.org.
- goteborgenergi2pvoutput: send data from goteborgenergi.py to pvoutput.org.
- sma2pvoutput: send the current DC voltage and current, AC power and daily
  energy yield to pvoutput. Intended to be run from cron every 5 minutes.
- templogger: logs the current outdoor temperature to a sqlite database.
