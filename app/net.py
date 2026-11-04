"""Utilitas jaringan: daftar alamat IP LAN komputer ini."""
from __future__ import annotations

import socket


def _is_private(ip: str) -> bool:
    return (ip.startswith("192.168.") or ip.startswith("10.")
            or ip.startswith("172.16.") or ip.startswith("172.17.")
            or ip.startswith("172.18.") or ip.startswith("172.19.")
            or ip.startswith("172.2") or ip.startswith("172.30.")
            or ip.startswith("172.31."))


def lan_ips() -> list[str]:
    """Alamat IPv4 non-loopback yang paling mungkin dipakai HP/TV di LAN.

    Urutan penting: alamat privat (192.168.x / 10.x / 172.16–31.x) didahulukan,
    alamat link-local (169.254.x, khas adapter yang tidak dapat DHCP) dibuang
    karena tidak pernah bisa dibuka dari HP. Hasil dibatasi 3 alamat supaya
    yang ditampilkan di UI tidak membingungkan.
    """
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # tidak mengirim paket, hanya pilih rute
        found.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found:
                found.append(ip)
    except Exception:
        pass

    clean = [ip for ip in found
             if not ip.startswith("127.") and not ip.startswith("169.254.")]
    clean.sort(key=lambda ip: (not _is_private(ip), ip))
    return clean[:3]


def lan_urls(port: int, token: str = "") -> list[str]:
    suffix = f"/?t={token}" if token else "/"
    return [f"http://{ip}:{port}{suffix}" for ip in lan_ips()]
