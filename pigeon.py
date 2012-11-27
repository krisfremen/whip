
"""
Pigeon, fast IP Geo lookup
"""

import csv
from itertools import chain, count, groupby, tee
import logging
from math import ceil
import struct
import socket

try:
    from itertools import imap, izip
except ImportError:
    # Python 3
    imap = map
    izip = zip
    xrange = range

import plyvel
import simplejson as json


from operator import itemgetter
__all__ = ['PigeonStore']


BATCH_SIZE = 20 * 1000
DEFAULT_DATABASE_DIR = 'db/'
logger = logging.getLogger(__name__)


#
# Utilities
#

IP_STRUCT = struct.Struct('>L')


def incr_ip(ip):
    n = IP_STRUCT.unpack(ip)[0] + 1
    try:
        return IP_STRUCT.pack(n)
    except struct.error:
        return None


def batch(iterable, n):
    counter = chain.from_iterable(izip(*tee(count(), n)))
    _next = next

    def key(item):
        return _next(counter)

    it = iter(iterable)
    return imap(itemgetter(1), groupby(it, key=key))


def transform_record(rec):
    start_ip = IP_STRUCT.pack(int(rec['start_ip_int']))
    end_ip = IP_STRUCT.pack(int(rec['end_ip_int']))
    key = start_ip + end_ip

    # TODO: carrier_id, tld_id, sld_id, reg_org_id,
    # phone_number_prefix, asn, cidr

    tz = rec['timezone']
    if tz != '999':
        timezone = None
    else:
        timezone = '{:+04d}'.format(int(ceil(100 * float(tz))))

    value = json.dumps(dict(
        begin=socket.inet_ntoa(start_ip),
        end=socket.inet_ntoa(end_ip),
        continent=(rec['continent'], 1.),
        country=(rec['country_iso2'],
                 float(rec['country_cf']) / 100),
        state=(rec['state'],
               float(rec['state_cf']) / 100),
        city=(rec['city'],
              float(rec['city_cf']) / 100),
        postal_code=rec['postal_code'],
        type=rec['connectiontype'],
        routing=rec['ip_routingtype'],
        coordinates=(float(rec['latitude']),
                     float(rec['longitude'])),
        timezone=timezone,
        line_speed=rec['linespeed'],
        asn=rec['asn'],
    ))

    return key, value


#
# Public API
#

class PigeonStore(object):
    def __init__(self, database_dir=None, create_if_missing=False):
        if database_dir is None:
            database_dir = DEFAULT_DATABASE_DIR
        logger.debug("Opening database %s", database_dir)
        self.db = plyvel.DB(
            database_dir,
            create_if_missing=create_if_missing)

    def load(self, fp):
        """Load CSV data from an open file-like object"""
        dr = csv.DictReader(fp, delimiter='\t')
        n = 0
        for chunk in batch(dr, BATCH_SIZE):
            with self.db.write_batch() as wb:
                for n, rec in enumerate(chunk, n + 1):
                    key, value = transform_record(rec)
                    wb.put(key, value)

                    if n % 100000 == 0:
                        logger.info('Indexed %d records', n)

    def lookup(self, ip):
        """Lookup a single ip address in the database"""
        range_key = incr_ip(ip)
        it = self.db.iterator(reverse=True, stop=range_key)
        try:
            key, value = next(it)
        except StopIteration:
            # Start of range, no hit
            return None

        # Looked up ip must be within the range
        end = key[4:]
        if ip > end:
            return None

        return value
