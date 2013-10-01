"""
Whip utility module.
"""

import heapq
import itertools
import operator
import socket
import struct
import time


#
# IP address conversion utilities
#

IPV4_STRUCT = struct.Struct('>L')


def ipv4_int_to_str(n, _inet_ntoa=socket.inet_ntoa, _pack=IPV4_STRUCT.pack):
    """Convert an integer into an IPv4 address string.

    This function converts a (max 32 bit) integer into the common
    dot-decimal notation for IP addresses. Example: ``0x01020304``
    becomes *1.2.3.4*.
    """
    return _inet_ntoa(_pack(n))


def ipv4_str_to_int(s, _inet_aton=socket.inet_aton,
                    _unpack=IPV4_STRUCT.unpack):
    """Convert an IPv4 address string into an integer.

    This is the reverse of :py:func:`ipv4_int_to_str`.
    """
    return _unpack(_inet_aton(s))[0]


def ipv4_bytes_to_int(b, _unpack=IPV4_STRUCT.unpack):
    """Convert a 4 byte string into an integer"""
    return _unpack(b)[0]


def ipv4_int_to_bytes(n, _pack=IPV4_STRUCT.pack):
    """Convert an integer into 4 byte string"""
    return _pack(n)


#
# Range merging
#

EVENT_TYPE_BEGIN = 0
EVENT_TYPE_END = 1


def merge_ranges(*inputs):
    """
    Merge multiple ranges into a combined stream of ranges.

    This function combines all inputs by generating "change events" for
    each individual input, and merging these together in a single stream
    of changes. It then groups the change stream by position, and
    iterates over it, yielding a snapshot at each change point.
    """

    # Shortcut in case there is only a single input iterable
    if len(inputs) == 1:
        for begin, end, data in inputs[0]:
            yield begin, end, [data]
        return

    def generate_change_events(it, input_id):
        """Generate start/stop "edges" for an iterable"""
        for begin, end, data in it:
            assert begin <= end
            yield begin, EVENT_TYPE_BEGIN, input_id, data
            yield end + 1, EVENT_TYPE_END, input_id, None

    changes_generators = [
        generate_change_events(input, input_id)
        for input_id, input in enumerate(inputs)
    ]
    all_changes = heapq.merge(*changes_generators)
    grouper = itertools.groupby(all_changes, operator.itemgetter(0))
    active = {}
    previous_position = None

    for position, changes in grouper:

        # Yield output range from the previous position up to the
        # current position, containing all currently valid values.
        if active:
            yield previous_position, position - 1, list(active.values())

        # Apply begin/end changes
        for _, event_type, input_id, data in changes:
            if event_type == EVENT_TYPE_BEGIN:
                assert input_id not in active
                active[input_id] = data
            elif event_type == EVENT_TYPE_END:
                del active[input_id]

        # Remember current position
        previous_position = position

    # After consuming all changes, all ranges must be closed.
    assert len(active) == 0


#
# Dict diffing and squashing
#

def dict_diff(d, base):
    """
    Calculate differences between a dict and a base dict.

    The return value is a 2-tuple containing a dict of items in `d` that
    were either added or modified and a list with removed keys.

    Note: this function does not recursively compare dicts; only the
    keys and values in the supplied dicts will be compared.

    See also dict_patch().
    """
    to_set = {k: v for k, v in d.items() if k not in base or base[k] != v}
    to_delete = [k for k in base if k not in d]
    return to_set, to_delete


def dict_patch(d, patch):
    """
    Patches a dictionary using a changes dict and a list of deletions.

    This is the reverse of dict_diff().
    """
    to_set, to_delete = patch
    d.update(to_set)
    for k in to_delete:
        del d[k]


def dict_diff_incremental(iterable):
    """
    Create incremental diffs for an iterable of dicts.

    The first dict in `iterable` (which must yield at least one dict)
    will be used as the base dict, and subsequent dicts will be returned
    as incremental patches. These incremental patches can be used to
    reconstruct the original dicts by incrementally applying those
    patches to the base dict (and its patched versions).

    This function returns a 2-tuple containing the base dict and
    a generator that yields incremental patches.

    Example: for the input `[d1, d2, d3, d4]`, this function returns the
    2-tuple `(d1, <patches-generator>)`; the generator yields 3 patches:
    `<diff from d1 to d2>, <diff from d2 to d3>, <diff from d3 to d4>`.
    """
    # This implementation is inspired by the pairwise() recipe from the
    # itertools documentation.
    a, b = itertools.tee(iterable)
    base = next(a)
    return base, itertools.starmap(dict_diff, zip(a, b))


def dict_patch_incremental(base, patches, inplace=False):
    """Generate patches dicts given a base dict and a series of patches.

    This is the reverse of dict_diff_incremental().
    """
    d = base
    for patch in patches:
        if not inplace:
            d = d.copy()
        dict_patch(d, patch)
        yield d


def squash_duplicate_dicts(dicts, ignored_key=None):
    """Deduplicate a list of dicts by squashing adjacent identical dicts.

    This functions takes a list of dicts and returns only the first dict
    of each "run" of identical dicts, in the original order, i.e.
    `[d1, d1, d1, d2, d2, d3, d1]` results in `[d1, d2, d3, d1]`.

    The `ignored_key` arg specifies a key to ignore when comparing the
    dict (which uses a normal `d1 == d2` equality test).
    """
    # Step 1: preparation. Pop (and remember) the key to ignore, but
    # first copy the dicts (instances are "borrowed"), so that we can
    # safely mutate them.
    _missing_value = object()  # unique object
    dicts = [d.copy() for d in dicts]
    transformed = [(d.pop(ignored_key, _missing_value), d) for d in dicts]

    # Step 2: deduplication. Group identical information, keeping only
    # the first occurrence of each unique dict. The implementation is
    # based on the unique_justseen() recipe from the itertools docs:
    # group by actual value and take only the first item of each
    # group.
    #
    # Note: the grouper is a generator (lazy), so explicitly turn the
    # result into a list, as the dicts will be modified inside the loop
    # below. Not doing so breaks the comparison inside the grouper.
    grouper = itertools.groupby(transformed, operator.itemgetter(1))
    squashed = [next(group) for key, group in grouper]

    # Step 3: transform to original format. Add back the ignored key to obtain
    # dicts in the original format.
    uniques = []
    _append = uniques.append
    for ignored_value, d in squashed:
        if ignored_value is not _missing_value:
            # Write back previously extracted ignored key/value (if any)
            d[ignored_key] = ignored_value
        _append(d)

    return uniques


#
# Progress logging
#

DEFAULT_CALLBACK_INTERVAL = 10


class PeriodicCallback(object):
    """
    Periodic callback ticker, useful for logging progress information.

    This class doesn't do anything but keeping a simple timer and
    a threshold. It's up to the calling code to call `tick()` everytime
    it may want the callback to be run.
    """

    def __init__(self, cb, interval=DEFAULT_CALLBACK_INTERVAL):
        assert callable(cb)
        self._cb = cb
        self._interval = interval
        self._last_report = float('-inf')

    def tick(self, force_report=False):
        """
        Ping this periodic callback instance.

        The callback function will be called if if enough time has
        elapsed.
        """
        if force_report or time.time() - self._last_report > self._interval:
            self._cb()
            self._last_report = time.time()
