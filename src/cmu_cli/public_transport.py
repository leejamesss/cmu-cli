"""Anonymous HTTPS sockets pinned to a validated public DNS result.

Keep the hostname for TLS SNI/certificate verification; connect only to a vetted
numeric address. DNS is resolved once per new connection, never again by connect.
"""

import ipaddress
import socket

from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError
from urllib3.util.connection import create_connection


def public_address(address):
    ip = ipaddress.ip_address(address)
    return (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_unspecified
        and not (getattr(ip, "ipv4_mapped", None) and not ip.ipv4_mapped.is_global)
    )


class PublicHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        try:
            answers = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
            if not addresses or not all(public_address(ip) for ip in addresses):
                raise OSError("Non-public destination")
            # create_connection receives a numeric address, eliminating DNS rebinding
            # between policy validation and the socket connection.
            return create_connection(
                (addresses[0], self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except (OSError, ValueError) as exc:
            raise NewConnectionError(self, "Public HTTPS connection failed") from exc


class PublicHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = PublicHTTPSConnection


class PublicHTTPSAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        super().init_poolmanager(connections, maxsize, block=block, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            "https": PublicHTTPSConnectionPool,
        }
