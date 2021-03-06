# Energy utils

A collection of python utils related to energy.

## Help classes
- eliq.py: a class for accessing the
  [ELIQ Online API](https://my.eliq.se/knowledge/sv-SE/49-eliq-online/299-eliq-online-api)
- goteborgenergi.py: a class for retrieving the monthly energy import/export
  from "Din sida" (your page) on [Göteborg
  Energi](http://www.goteborgenergi.se/)
- pvoutput.py: a class wrapping the
  [pvoutput.org service API](http://pvoutput.org/help.html#api)
- sma.py: a class for communicating with [SMA inverters](http://www.sma.de/en/)
- tibber.py: a class for accessing the
  [Tibber GraphQL API](https://developer.tibber.com/docs/reference)

## Utils
- eliq2pvoutput: send data from ELIQ to pvoutput.org. No longer used by myself
  (due to the fact that the diode on the energy meter blinks for both import
  and export of energy and thus is not as useful as it could have been).
- goteborgenergi2pvoutput: send data from goteborgenergi.se to pvoutput.org.
- sma2pvoutput: send the current DC voltage and current, AC power and daily
  energy yield to pvoutput. Intended to be run from cron every 5 minutes.
- templogger: logs the current outdoor temperature to a sqlite database.
- tibber2pvoutput: update pvoutput.org with export/import data from Tibber.
