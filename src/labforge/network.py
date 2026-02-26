import ipaddress


BASE_NETWORK = ipaddress.IPv4Network("172.30.0.0/16")
SUBNET_PREFIX = 24


class NetworkError(Exception):
    pass


class NetworkAllocator:
    """Allocates unique /24 subnets from 172.30.0.0/16 for each lab."""

    def __init__(self, used_subnets: list[str] | None = None):
        self.used = set()
        for s in (used_subnets or []):
            try:
                self.used.add(ipaddress.IPv4Network(s))
            except ValueError:
                pass

    def allocate(self) -> str:
        """Allocate the next available /24 subnet."""
        for subnet in BASE_NETWORK.subnets(new_prefix=SUBNET_PREFIX):
            # Skip the first subnet (172.30.0.0/24) to avoid conflicts
            if subnet.network_address == BASE_NETWORK.network_address:
                continue
            if subnet not in self.used:
                self.used.add(subnet)
                return str(subnet)
        raise NetworkError("No available subnets in 172.30.0.0/16")

    @staticmethod
    def compute_ip(subnet: str, offset: int) -> str:
        """Compute an IP address from a subnet and offset."""
        net = ipaddress.IPv4Network(subnet)
        ip = net.network_address + offset
        if ip not in net:
            raise NetworkError(
                f"IP offset {offset} is out of range for subnet {subnet}"
            )
        return str(ip)

    @staticmethod
    def gateway_ip(subnet: str) -> str:
        """Return the gateway IP (first usable address) for a subnet."""
        net = ipaddress.IPv4Network(subnet)
        return str(net.network_address + 1)
