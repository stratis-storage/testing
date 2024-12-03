#!/usr/bin/env python3

# Copyright 2024 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Inspect Stratis pool-level metadata and produce various kinds of output.
"""

# isort: STDLIB
import argparse
import json
import os
from collections import defaultdict
from enum import Enum
from uuid import UUID

SIZE_OF_STRATIS_METADATA_SECTORS = 8192
SIZE_OF_CRYPT_METADATA_SECTORS = 32768


class Json:  # pylint: disable=too-few-public-methods
    """
    Keys in the pool-level metadata.
    """

    ALLOCS = "allocs"
    BACKSTORE = "backstore"
    BLOCKDEV = "blockdev"
    CACHE_TIER = "cache_tier"
    CAP = "cap"
    CRYPT_META_ALLOCS = "crypt_meta_allocs"
    DATA_TIER = "data_tier"
    DEVS = "devs"
    FEATURES = "features"
    FLEX_DEVS = "flex_devs"
    INTEGRITY_BLOCK_SIZE = "integrity_block_size"
    INTEGRITY_JOURNAL_SIZE = "integrity_journal_size"
    INTEGRITY_META_ALLOCS = "integrity_meta_allocs"
    INTEGRITY_TAG_SIZE = "integrity_tag_size"
    LENGTH = "length"
    META_DEV = "meta_dev"
    PARENT = "parent"
    START = "start"
    THIN_DATA_DEV = "thin_data_dev"
    THIN_META_DEV = "thin_meta_dev"
    THIN_META_DEV_SPARE = "thin_meta_dev_spare"
    UUID = "uuid"


class Feature:  # pylint: disable=too-few-public-methods
    """
    Possible feature value.
    """

    ENCRYPTION = "Encryption"


class DataDeviceUse(Enum):
    """
    Uses for block device allocations
    """

    STRATIS_METADATA = "stratis_metadata"
    INTEGRITY_METADATA = "integrity_metadata"
    ALLOCATED = "allocated"
    UNUSED = "unused"

    def __str__(self):
        return self.value


class CacheDeviceUse(Enum):
    """
    Uses for block device allocations
    """

    STRATIS_METADATA = "stratis_metadata"
    METADATA = "cache_metadata"
    DATA = "cache_data"
    UNUSED = "unused"

    def __str__(self):
        return self.value


class CapDeviceUse(Enum):
    """
    Use for cap device allocations.
    """

    ALLOCATED = "allocated"
    UNUSED = "unused"

    def __str__(self):
        return self.value


class FlexDeviceUse(Enum):
    """
    Encodes uses for layout on flex device.
    """

    META_DEV = "meta_dev"
    THIN_DATA_DEV = "thin_data_dev"
    THIN_META_DEV = "thin_meta_dev"
    THIN_META_DEV_SPARE = "thin_meta_dev_spare"
    UNUSED = "unused"

    def __str__(self):
        return self.value


def _check_overlap(iterable, init):
    """
    Check overlap of extents

    :param iterable: the iterable
    :param int init: start the check from this offset

    :returns: a list of errors as strings
    """
    errors = []

    current_block = init
    for start, (use, length) in sorted(iterable, key=lambda x: x[0]):
        if start < current_block:
            errors.append(
                f"allocation ({start, length}) for {use} overlaps with "
                f"previous allocation which extends to {current_block}"
            )

        current_block = start + length

    return errors


def _filled(iterable, filler, start_offset):
    """
    Return a hash of extents with all types listed.

    :param int start_offset: the offset from which to start the build
    """
    result = {}
    current_offset = start_offset
    for start, (use, length) in sorted(iterable, key=lambda x: x[0]):
        if start > current_offset:
            result[current_offset] = (
                filler,
                start - current_offset,
            )
        result[start] = (use, length)
        current_offset = start + length

    return result


def _max(iterable, start_offset):
    """
    Return the maximum allocated length

    :param int start_offset: the offset from which to start
    """
    current_max = start_offset
    _ = [
        current_max := max(current_max, start + length)
        for (start, (_, length)) in iterable
    ]
    return current_max


def _table(iterable):
    """
    Return a string representing the table of uses for a device.
    """
    return os.linesep.join(
        (
            f"({start}, {length}, {start + length})    {use}"
            for (start, (use, length)) in sorted(iterable, key=lambda x: x[0])
        )
    )


class CapDevice:
    """
    Layout on a cap device.
    """

    def __init__(self, encrypted):
        self.extents = {}
        self.encrypted = encrypted

    def add(self, *, allocs=None):
        """
        Add specified values to the CapDevice's extents.
        """
        allocs = [] if allocs is None else allocs

        for start, length in allocs:
            assert start not in self.extents
            self.extents[start] = (CapDeviceUse.ALLOCATED, length)

        return self

    def _offset(self):
        return 0 if self.encrypted else SIZE_OF_CRYPT_METADATA_SECTORS

    def filled(self):
        """
        Returns a copy of self with spaces filled with the unused value.
        """
        return _filled(self.extents.items(), CapDeviceUse.UNUSED, self._offset())

    def __str__(self):
        return f"On crypt device: {self.encrypted}{os.linesep}" + _table(
            self.filled().items()
        )

    def max(self):
        """
        Returns the maximum derivable from extents.
        """
        return _max(self.extents.items(), self._offset())

    def check(self):
        """
        Run all checks
        """

        def check_overlap(self):
            """
            Returns an error if allocations overlap
            """
            return [
                f"Cap Device: {x}"
                for x in _check_overlap(self.extents.items(), self._offset())
            ]

        return check_overlap(self)


class DataDevice:
    """
    Layout on a block device.
    """

    def __init__(self):
        self.extents = {
            0: (DataDeviceUse.STRATIS_METADATA, SIZE_OF_STRATIS_METADATA_SECTORS)
        }

    def add(self, *, integrity_meta_allocs=None, allocs=None):
        """
        Add more layout on the device.
        """
        integrity_meta_allocs = (
            [] if integrity_meta_allocs is None else integrity_meta_allocs
        )

        allocs = [] if allocs is None else allocs

        for start, length in integrity_meta_allocs:
            assert start not in self.extents
            self.extents[start] = (DataDeviceUse.INTEGRITY_METADATA, length)

        for start, length in allocs:
            assert start not in self.extents
            self.extents[start] = (DataDeviceUse.ALLOCATED, length)

        return self

    def filled(self):
        """
        Returns a copy of self with spaces filled with the unused value.
        """
        return _filled(self.extents.items(), DataDeviceUse.UNUSED, 0)

    def __str__(self):
        return _table(self.filled().items())

    def max(self):
        """
        Returns the maximum derivable from extents.
        """
        return _max(self.extents.items(), 0)

    def check(self):
        """
        Run well-formedness checks on this metadata.
        """

        def check_integrity_meta_round(self):
            """
            Check integrity metadata for rounding properties.
            """
            errors = []

            for length in (
                length
                for (_, (use, length)) in self.extents.items()
                if use is DataDeviceUse.INTEGRITY_METADATA
            ):
                if length % 8 != 0:
                    errors.append(
                        f"integrity meta_allocs length {length} sectors is "
                        "not a multiple of 4KiB"
                    )

            return errors

        def check_overlap(self):
            """
            Returns an error if allocations overlap
            """
            return [
                f"Data Block Device: {x}"
                for x in _check_overlap(self.extents.items(), 0)
            ]

        return check_overlap(self) + check_integrity_meta_round(self)


class CacheDevice:
    """
    Layout on a block device used for cache.
    """

    def __init__(self):
        self.extents = {
            0: (CacheDeviceUse.STRATIS_METADATA, SIZE_OF_STRATIS_METADATA_SECTORS)
        }

    def add(self, *, metadata_allocs=None, data_allocs=None):
        """
        Add more layout on the device.
        """
        metadata_allocs = [] if metadata_allocs is None else metadata_allocs
        data_allocs = [] if data_allocs is None else data_allocs

        for start, length in metadata_allocs:
            assert start not in self.extents
            self.extents[start] = (CacheDeviceUse.METADATA, length)

        for start, length in data_allocs:
            assert start not in self.extents
            self.extents[start] = (CacheDeviceUse.DATA, length)

        return self

    def filled(self):
        """
        Returns a copy of self with spaces filled with the unused value.
        """
        return _filled(self.extents.items(), CacheDeviceUse.UNUSED, 0)

    def __str__(self):
        return _table(self.filled().items())

    def max(self):
        """
        Returns the maximum derivable from extents.
        """
        return _max(self.extents.items(), 0)

    def check(self):
        """
        Run well-formedness checks on this metadata.
        """

        def check_overlap(self):
            """
            Returns an error if allocations overlap
            """
            return [
                f"Cache Block Device: {x}"
                for x in _check_overlap(self.extents.items(), 0)
            ]

        return check_overlap(self)


class CryptAllocs:
    """
    Represents the allocations for crypt metadata.
    """

    def __init__(self):
        """
        Initializer.
        """
        self.extents = {}

    def add(self, *, allocs=None):
        """
        Add allocations for crypt metadata.

        :param allocs: allocations for crypt metadata
        :type
        """
        allocs = [] if allocs is None else allocs

        for start, length in allocs:
            assert start not in self.extents
            self.extents[start] = length

        return self

    def check(self):
        """
        Run well-formedness checks.
        """

        def check_canonical(self):
            """
            Check that crypt allocs are what we expect them to be for the
            foreseeable future.
            """
            errors = []

            if len(self.extents) > 1:
                errors.append("No allocations for crypt metadata")

            if len(self.extents) == 0:
                errors.append("Multiple allocations for crypt metadata")

            (start, length) = list(self.extents.items())[0]

            if start != 0:
                errors.append(f"Crypt meta allocs offset, {start} sectors, is not 0")

            if length != 32768:
                errors.append(
                    f"Crypt meta allocs entry has unexpected length {length} sectors"
                )

            return errors

        return check_canonical(self)

    def __str__(self):
        return os.linesep.join(
            (f"({start}, {length})" for (start, length) in sorted(self.extents.items()))
        )


class FlexDevice:
    """
    Layout on flex device.
    """

    def __init__(self, encrypted):
        self.extents = {}
        self.encrypted = encrypted

    def add(
        self,
        *,
        thin_meta_dev=None,
        thin_meta_dev_spare=None,
        meta_dev=None,
        thin_data_dev=None,
    ):
        """
        Add allocations from flex device.
        """
        thin_meta_dev = [] if thin_meta_dev is None else thin_meta_dev
        thin_meta_dev_spare = [] if thin_meta_dev_spare is None else thin_meta_dev_spare
        meta_dev = [] if meta_dev is None else meta_dev
        thin_data_dev = [] if thin_data_dev is None else thin_data_dev

        for start, length in thin_meta_dev:
            assert start not in self.extents
            self.extents[start] = (FlexDeviceUse.THIN_META_DEV, length)

        for start, length in thin_meta_dev_spare:
            assert start not in self.extents
            self.extents[start] = (FlexDeviceUse.THIN_META_DEV_SPARE, length)

        for start, length in meta_dev:
            assert start not in self.extents
            self.extents[start] = (FlexDeviceUse.META_DEV, length)

        for start, length in thin_data_dev:
            assert start not in self.extents
            self.extents[start] = (FlexDeviceUse.THIN_DATA_DEV, length)

        return self

    def _offset(self):
        return 0 if self.encrypted else SIZE_OF_CRYPT_METADATA_SECTORS

    def filled(self):
        """
        Returns a copy of self with spaces filled with the unused value.
        """
        return _filled(self.extents.items(), FlexDeviceUse.UNUSED, self._offset())

    def __str__(self):
        return _table(self.filled().items())

    def max(self):
        """
        Returns the maximum derivable from extents.
        """
        return _max(self.extents.items(), self._offset())

    def check(self):
        """
        Run checks on this device.
        """

        def check_overlap(self):
            """
            Check if any of the allocations overlap.
            """
            return [
                f"Flex Device: {x}"
                for x in _check_overlap(self.extents.items(), self._offset())
            ]

        def check_spare_and_in_use(self):
            """
            Verify that spare and in use thin meta device allocations are the same.
            """

            def calc_total(iterable, use):
                return sum(length for (u, length) in iterable if u is use)

            thin_meta_total = calc_total(
                self.extents.values(), FlexDeviceUse.THIN_META_DEV
            )
            thin_meta_spare_total = calc_total(
                self.extents.values(), FlexDeviceUse.THIN_META_DEV_SPARE
            )

            return (
                []
                if thin_meta_total == thin_meta_spare_total
                else [
                    (
                        "Sum of the allocations for the thin meta device, "
                        f"{thin_meta_total} sectors, does not equal the sum of "
                        "the allocations for the thin meta spare device, "
                        f"{thin_meta_spare_total} sectors."
                    )
                ]
            )

        return check_spare_and_in_use(self) + check_overlap(self)


def _data_devices(metadata):
    """
    Returns a tuple of a map of DataDevice objects with key = UUID and the
    integrity parameters.
    """
    data_tier = metadata[Json.BACKSTORE][Json.DATA_TIER]

    data_tier_devs = data_tier[Json.BLOCKDEV][Json.DEVS]

    bds = defaultdict(
        DataDevice,
        (
            (
                UUID(dev[Json.UUID]),
                DataDevice().add(
                    integrity_meta_allocs=(dev.get(Json.INTEGRITY_META_ALLOCS) or [])
                ),
            )
            for dev in data_tier_devs
        ),
    )

    assert len(bds) == len(data_tier_devs), "UUID collision found"

    data_tier_allocs = data_tier[Json.BLOCKDEV][Json.ALLOCS][0]

    for item in data_tier_allocs:
        bds[UUID(item[Json.PARENT])].add(allocs=[[item[Json.START], item[Json.LENGTH]]])

    return (
        bds,
        {
            x: data_tier[x]
            for x in [
                Json.INTEGRITY_BLOCK_SIZE,
                Json.INTEGRITY_JOURNAL_SIZE,
                Json.INTEGRITY_TAG_SIZE,
            ]
            if data_tier.get(x) is not None
        },
    )


def _cache_devices(metadata):
    """
    Returns a map of CacheDevice objects with key = UUID
    """
    cache_tier_entries = metadata[Json.BACKSTORE].get(Json.CACHE_TIER)

    if cache_tier_entries is None:
        return {}

    cache_tier_devs = cache_tier_entries[Json.BLOCKDEV][Json.DEVS]

    bds = defaultdict(
        CacheDevice,
        ((UUID(dev[Json.UUID]), CacheDevice()) for dev in cache_tier_devs),
    )

    assert len(bds) == len(cache_tier_devs), "UUID collision found"

    cache_tier_allocs = cache_tier_entries[Json.BLOCKDEV][Json.ALLOCS]

    for item in cache_tier_allocs[0]:
        bds[UUID(item[Json.PARENT])].add(
            metadata_allocs=[[item[Json.START], item[Json.LENGTH]]]
        )

    for item in cache_tier_allocs[1]:
        bds[UUID(item[Json.PARENT])].add(
            data_allocs=[[item[Json.START], item[Json.LENGTH]]]
        )

    return bds


def _cap_device(metadata, encrypted=False):
    """
    Returns a cap device.
    """
    cap_device = CapDevice(encrypted)

    cap_device.add(allocs=metadata[Json.BACKSTORE][Json.CAP][Json.ALLOCS])

    return cap_device


def _crypt_allocs(metadata):
    """
    Get info about allocations for crypt metadata.
    """
    return CryptAllocs().add(allocs=metadata["backstore"]["cap"]["crypt_meta_allocs"])


def _flex_device(metadata, encrypted):
    """
    Get flex device allocation.
    """
    flex_dev_allocs = metadata[Json.FLEX_DEVS]
    return FlexDevice(encrypted).add(
        thin_meta_dev=flex_dev_allocs[Json.THIN_META_DEV],
        thin_meta_dev_spare=flex_dev_allocs[Json.THIN_META_DEV_SPARE],
        meta_dev=flex_dev_allocs[Json.META_DEV],
        thin_data_dev=flex_dev_allocs[Json.THIN_DATA_DEV],
    )


def check(metadata):
    """
    Check pool-level metadata for consistency.

    :param metadata: all the pool-level metadata.
    :type metadata: Python JSON representation
    :return: list of str
    """

    errors = []

    data_devices, _ = _data_devices(metadata)

    for bd in data_devices.values():
        errors.extend(bd.check())

    cache_devices = _cache_devices(metadata)

    for bd in cache_devices.values():
        errors.extend(bd.check())

    crypt_allocs = _crypt_allocs(metadata)

    errors.extend(crypt_allocs.check())

    cap_device = _cap_device(
        metadata, Feature.ENCRYPTION in (metadata.get(Json.FEATURES) or [])
    )
    errors.extend(cap_device.check())

    flex_device = _flex_device(
        metadata, Feature.ENCRYPTION in (metadata.get(Json.FEATURES) or [])
    )
    errors.extend(flex_device.check())

    return [str(x) for x in errors]


def _print(metadata):
    """
    Print a human readable representation of the layout of some parts of
    the stack.
    """

    data_devices, integrity_config = _data_devices(metadata)
    cache_devices = _cache_devices(metadata)

    print("Integrity config for data devices:")
    for key, value in integrity_config.items():
        print(f"{key}: {value}")

    print()

    print(
        f"{2 * os.linesep}".join(
            [
                f"Data Device UUID: {uuid}{os.linesep}{dev}"
                for uuid, dev in data_devices.items()
            ]
            + [
                f"Cache Device UUID: {uuid}{os.linesep}{dev}"
                for uuid, dev in cache_devices.items()
            ]
        )
    )

    crypt_allocs = _crypt_allocs(metadata)
    print("")
    print("Allocations for crypt metadata")
    print(f"{crypt_allocs}")

    cap_device = _cap_device(
        metadata, Feature.ENCRYPTION in (metadata.get(Json.FEATURES) or [])
    )

    print("")
    print("Cap Device:")
    print(f"{cap_device}")

    flex_device = _flex_device(
        metadata, Feature.ENCRYPTION in (metadata.get(Json.FEATURES) or [])
    )

    print("")
    print("Flex Device:")
    print(f"{flex_device}")


def _gen_parser():
    """
    Generate the parser.
    """
    parser = argparse.ArgumentParser(
        description=("Inspect Stratis pool-level metadata.")
    )

    parser.add_argument("file", help="The file with the pool-level metadata")

    parser.add_argument(
        "--print",
        action="store_true",
        help="print a human readable view of the storage stack",
    )
    return parser


def main():
    """
    The main method.
    """

    parser = _gen_parser()

    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as infile:
        metadata = json.load(infile)

    if args.print:
        _print(metadata)
    else:
        errors = check(metadata)
        if errors:
            raise RuntimeError(errors)


if __name__ == "__main__":
    main()
