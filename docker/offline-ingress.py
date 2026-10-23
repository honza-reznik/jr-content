"""Brány: před spuštěním odebrat výchozí trasy a všechna zvýšená oprávnění."""

import errno
import os
import socket
import struct
import sys

# Docker 29 nepublikuje porty čistě internal sítí. Vstupní síť proto slouží jen
# místním portům; odstranění výchozích tras proběhne před spuštěním aplikace.
with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE) as netlink:
    netlink.settimeout(5)
    netlink.bind((0, 0))
    sequence = 0
    for family in (socket.AF_INET, socket.AF_INET6):
        for _ in range(16):
            sequence += 1
            # RTM_DELROUTE, REQUEST|ACK; výchozí unicast trasa v hlavní tabulce.
            route = struct.pack("BBBBBBBBI", family, 0, 0, 0, 254, 0, 0, 1, 0)
            header = struct.pack("IHHII", 16 + len(route), 25, 5, sequence, 0)
            netlink.sendto(header + route, (0, 0))
            reply = netlink.recv(65535)
            _, kind, _, received, _ = struct.unpack_from("IHHII", reply)
            if kind != 2 or received != sequence:  # NLMSG_ERROR (i úspěšné ACK)
                raise RuntimeError("Neočekávaná odpověď při odstranění trasy")
            error = -struct.unpack_from("i", reply, 16)[0]
            if error in (errno.ESRCH, errno.ENOENT):
                break
            if error:
                raise OSError(error, os.strerror(error))
        else:
            raise RuntimeError("Příliš mnoho výchozích tras")

os.execvp("setpriv", [
    "setpriv", "--reuid", os.environ["JRC_UID"], "--regid", os.environ["JRC_GID"],
    "--clear-groups", "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
    "--no-new-privs", "--", *sys.argv[1:],
])
