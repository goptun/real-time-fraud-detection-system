"""Tabela fixa de cidades sintéticas + distância geodésica.

A localização de uma transação é (país, cidade); a lat/lon vem desta tabela.
Assim o payload do evento fica pequeno e `km_from_last` / `implied_speed_kmh`
(viagem impossível) são calculados de forma determinística no treino e no
serving.
"""

from __future__ import annotations

import math
from typing import NamedTuple


class City(NamedTuple):
    country: str
    lat: float
    lon: float


# Peso = probabilidade relativa de ser a cidade "casa" de um usuário (só BR).
BR_HOME_WEIGHTS: dict[str, float] = {
    "São Paulo": 20,
    "Rio de Janeiro": 10,
    "Belo Horizonte": 5,
    "Brasília": 4,
    "Curitiba": 4,
    "Porto Alegre": 4,
    "Salvador": 4,
    "Fortaleza": 4,
    "Recife": 4,
    "Manaus": 2,
    "Goiânia": 3,
    "Florianópolis": 2,
}

CITIES: dict[str, City] = {
    # Brasil
    "São Paulo": City("BR", -23.55, -46.63),
    "Rio de Janeiro": City("BR", -22.91, -43.17),
    "Belo Horizonte": City("BR", -19.92, -43.94),
    "Brasília": City("BR", -15.79, -47.88),
    "Curitiba": City("BR", -25.43, -49.27),
    "Porto Alegre": City("BR", -30.03, -51.23),
    "Salvador": City("BR", -12.97, -38.51),
    "Fortaleza": City("BR", -3.73, -38.53),
    "Recife": City("BR", -8.05, -34.88),
    "Manaus": City("BR", -3.12, -60.02),
    "Goiânia": City("BR", -16.68, -49.25),
    "Florianópolis": City("BR", -27.60, -48.55),
    # Exterior
    "Lisboa": City("PT", 38.72, -9.14),
    "Madrid": City("ES", 40.42, -3.70),
    "London": City("GB", 51.51, -0.13),
    "New York": City("US", 40.71, -74.01),
    "Miami": City("US", 25.76, -80.19),
    "Buenos Aires": City("AR", -34.60, -58.38),
    "Santiago": City("CL", -33.45, -70.67),
    "Mexico City": City("MX", 19.43, -99.13),
    "Dubai": City("AE", 25.20, 55.27),
    "Singapore": City("SG", 1.35, 103.82),
    "Tokyo": City("JP", 35.68, 139.65),
    "Sydney": City("AU", -33.87, 151.21),
}

BR_CITIES = [c for c, v in CITIES.items() if v.country == "BR"]
FOREIGN_CITIES = [c for c, v in CITIES.items() if v.country != "BR"]

_EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def city_distance_km(city_a: str, city_b: str) -> float:
    a, b = CITIES[city_a], CITIES[city_b]
    return haversine_km(a.lat, a.lon, b.lat, b.lon)
