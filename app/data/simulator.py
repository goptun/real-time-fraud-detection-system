"""Simulador de transações sintéticas rotuladas.

Um único código gera (a) o dataset de treino/avaliação e (b) o fluxo contínuo
do replay ao vivo: o `Simulator` mantém o estado dos usuários entre chamadas de
`advance(days)`, que devolvem as transações do próximo bloco de dias em ordem
temporal. Tudo é determinístico dado (seed, config, sequência de `advance`).

Comportamento legítimo (por usuário): faixa de valor log-normal, 1-3 dispositivos,
cidade "casa", merchants favoritos, horários preferidos — mais outliers
legítimos (compra cara, dispositivo novo, viagem, "sprees") para que as classes
se sobreponham e o problema não seja separável por um limiar.

Fraude: episódios com padrões comportamentais distintos (velocity, amount_spike,
new_device, impossible_travel, low_and_slow — este último quase indistinguível
de uma compra normal, de propósito).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from app.data.catalog import (
    BIG_TICKET_CATEGORIES,
    CATEGORIES,
    CATEGORY_BY_NAME,
    MERCHANTS,
    RISKY_CATEGORIES,
)
from app.data.geo import BR_CITIES, BR_HOME_WEIGHTS, CITIES, city_distance_km
from app.data.schema import Transaction

DAY = 86400.0
HOUR = 3600.0
TRAVEL_SPEED_KMH = 700.0  # velocidade "de avião" usada p/ tornar viagens legítimas plausíveis

# Curva diurna base (UTC-naive, "hora local" sintética): pico no almoço e à noite.
_HOUR_BASE = np.array(
    [1, 0.5, 0.3, 0.3, 0.3, 0.6, 1.5, 3, 4, 5, 6, 7, 8, 8, 7, 6, 6, 7, 8, 8, 7, 5, 3, 2],
    dtype=float,
)

# Participação desejada de cada padrão no total de TRANSAÇÕES fraudulentas.
PATTERN_WEIGHTS: dict[str, float] = {
    "velocity": 0.30,
    "amount_spike": 0.25,
    "new_device": 0.20,
    "impossible_travel": 0.15,
    "low_and_slow": 0.10,
}
# Tamanho médio (nº de transações) de um episódio de cada padrão. A escolha do
# padrão é por episódio, então o peso é dividido por isto — senão as rajadas
# (~7 transações por episódio) dominariam e o problema ficaria fácil demais.
_EPISODE_SIZE: dict[str, float] = {
    "velocity": 7.0,
    "amount_spike": 1.3,
    "new_device": 2.0,
    "impossible_travel": 1.4,
    "low_and_slow": 1.0,
}

_MERCHANT_IDS = [m for m, _ in MERCHANTS]
_MERCHANT_CATS = [c for _, c in MERCHANTS]
_MERCHANT_W = np.array([CATEGORY_BY_NAME[c].legit_weight for c in _MERCHANT_CATS], dtype=float)
_MERCHANT_W /= _MERCHANT_W.sum()
_BY_CAT: dict[str, list[int]] = {}
for _i, _c in enumerate(_MERCHANT_CATS):
    _BY_CAT.setdefault(_c, []).append(_i)
_RISKY_IDX = [i for c in RISKY_CATEGORIES for i in _BY_CAT[c]]


@dataclass
class SimConfig:
    n_users: int = 2000
    seed: int = 42
    fraud_rate: float = 0.02  # fração alvo de transações fraudulentas
    start: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    id_prefix: str = "t"
    mean_txn_per_day: float = 4.0
    # Comportamento legítimo "atípico"
    legit_big_purchase_p: float = 0.012
    legit_new_device_p: float = 0.003
    legit_spree_p: float = 0.004
    trip_p_per_day: float = 0.006


@dataclass
class _Trip:
    city: str
    start: float
    end: float
    gap: float  # tempo de deslocamento (ida e volta) durante o qual não há transações


@dataclass
class _User:
    user_id: str
    home_city: str
    devices: list[str]
    favorites: list[int]  # índices em MERCHANTS
    mu: float
    sigma: float
    rate: float
    hour_probs: np.ndarray
    trip: _Trip | None = None
    last_t: float | None = None
    last_city: str = ""


@dataclass
class _Ev:
    t: float
    user: int
    city: str
    device: str
    merchant: int
    amount: float
    fraud: bool = False
    pattern: str | None = None

    @property
    def category(self) -> str:
        return _MERCHANT_CATS[self.merchant]


class Simulator:
    def __init__(self, config: SimConfig | None = None):
        self.cfg = config or SimConfig()
        self.rng = np.random.default_rng(self.cfg.seed)
        start = self.cfg.start.astimezone(timezone.utc).timestamp()
        self._day0 = math.floor(start / DAY) * DAY
        self._clock = start
        self._next_day = 0
        self._counter = 0
        self._fraud_carry = 0.0
        self.users: list[_User] = [self._new_user(i) for i in range(self.cfg.n_users)]
        names = list(PATTERN_WEIGHTS)
        self._pattern_names = names
        p = np.array([PATTERN_WEIGHTS[n] / _EPISODE_SIZE[n] for n in names])
        self._pattern_p = p / p.sum()

    # ------------------------------------------------------------------ users
    def _hex_id(self, prefix: str) -> str:
        return f"{prefix}_{int(self.rng.integers(0, 2**40)):010x}"

    def _new_user(self, i: int) -> _User:
        rng = self.rng
        cities = list(BR_HOME_WEIGHTS)
        w = np.array([BR_HOME_WEIGHTS[c] for c in cities], dtype=float)
        home = cities[int(rng.choice(len(cities), p=w / w.sum()))]
        n_dev = 1 + int(rng.random() < 0.35) + int(rng.random() < 0.10)
        rate = float(np.clip(rng.lognormal(math.log(self.cfg.mean_txn_per_day), 0.5), 0.8, 14.0))
        shift = int(rng.integers(-2, 3))
        probs = np.roll(_HOUR_BASE, shift)
        n_fav = int(rng.integers(8, 15))
        return _User(
            user_id=f"u_{i:05d}",
            home_city=home,
            devices=[self._hex_id("dev") for _ in range(n_dev)],
            favorites=[int(x) for x in rng.choice(len(MERCHANTS), size=n_fav, replace=False, p=_MERCHANT_W)],
            mu=float(rng.normal(math.log(70.0), 0.55)),
            sigma=float(rng.uniform(0.45, 0.85)),
            rate=rate,
            hour_probs=probs / probs.sum(),
            last_city=home,
        )

    # -------------------------------------------------------------- utilities
    def _amount(self, mu: float, sigma: float) -> float:
        return round(min(max(float(np.exp(self.rng.normal(mu, sigma))), 1.0), 500_000.0), 2)

    def _random_merchant_in(self, cats) -> int:
        idx = _BY_CAT[cats[int(self.rng.integers(len(cats)))]]
        return idx[int(self.rng.integers(len(idx)))]

    def _random_merchant(self) -> int:
        return int(self.rng.choice(len(MERCHANTS), p=_MERCHANT_W))

    def _risky_merchant(self) -> int:
        return _RISKY_IDX[int(self.rng.integers(len(_RISKY_IDX)))]

    # ---------------------------------------------------------------- legit
    def _new_trip(self, u: _User, start: float) -> _Trip:
        rng = self.rng
        if rng.random() < 0.7:
            options = [c for c in BR_CITIES if c != u.home_city]
        else:
            options = [c for c, v in CITIES.items() if v.country != "BR"]
        city = options[int(rng.integers(len(options)))]
        gap = max(2 * HOUR, 3 * HOUR + city_distance_km(u.home_city, city) / TRAVEL_SPEED_KMH * HOUR)
        return _Trip(city=city, start=start, end=start + rng.uniform(2, 6) * DAY, gap=gap)

    def _city_at(self, u: _User, t: float) -> str | None:
        tr = u.trip
        if tr is None or t < tr.start:
            return u.home_city
        if t < tr.start + tr.gap:
            return None  # em trânsito: sem transações (viagens legítimas nunca são "impossíveis")
        if t < tr.end:
            return tr.city
        if t < tr.end + tr.gap:
            return None
        u.trip = None
        return u.home_city

    def _pick_device(self, u: _User) -> str:
        rng = self.rng
        if rng.random() < self.cfg.legit_new_device_p:  # celular novo, legítimo
            u.devices.append(self._hex_id("dev"))
            return u.devices[-1]
        if len(u.devices) == 1 or rng.random() < 0.88:
            return u.devices[0]
        return u.devices[int(rng.integers(len(u.devices)))]

    def _legit_events(self, ui: int, u: _User, t: float, city: str) -> list[_Ev]:
        rng, cfg = self.rng, self.cfg
        device = self._pick_device(u)
        if rng.random() < cfg.legit_big_purchase_p:
            m = self._random_merchant_in(BIG_TICKET_CATEGORIES)
            shift = CATEGORY_BY_NAME[_MERCHANT_CATS[m]].amount_shift
            amount = round(min(self._amount(u.mu + shift, u.sigma) * float(rng.uniform(3, 8)), 500_000.0), 2)
        else:
            m = u.favorites[int(rng.integers(len(u.favorites)))] if rng.random() < 0.85 else self._random_merchant()
            amount = self._amount(u.mu + CATEGORY_BY_NAME[_MERCHANT_CATS[m]].amount_shift, u.sigma)
        events = [_Ev(t, ui, city, device, m, amount)]
        if rng.random() < cfg.legit_spree_p:  # várias compras seguidas (shopping, viagem)
            tt = t
            for _ in range(int(rng.integers(2, 5))):
                tt += float(rng.uniform(30, 600))
                m2 = self._random_merchant()
                events.append(_Ev(tt, ui, city, device, m2, self._amount(u.mu + CATEGORY_BY_NAME[_MERCHANT_CATS[m2]].amount_shift, u.sigma)))
        return events

    def _legit_for_user(self, ui: int, u: _User, w0: float, days: int) -> list[_Ev]:
        rng = self.rng
        events: list[_Ev] = []
        for d in range(days):
            day_start = w0 + d * DAY
            if u.trip is None and rng.random() < self.cfg.trip_p_per_day:
                u.trip = self._new_trip(u, day_start + float(rng.uniform(0, DAY)))
            n = int(rng.poisson(u.rate))
            if n == 0:
                continue
            hours = rng.choice(24, size=n, p=u.hour_probs)
            times = np.sort(day_start + hours * HOUR + rng.uniform(0, HOUR, size=n))
            for t in times:
                city = self._city_at(u, float(t))
                if city is not None:
                    events.extend(self._legit_events(ui, u, float(t), city))
        events.sort(key=lambda e: e.t)
        return events

    # ---------------------------------------------------------------- fraud
    def _anchor(self, ui: int, t: float, legit: list[_Ev]) -> tuple[float | None, str, str]:
        """Último evento legítimo do usuário até `t`: (tempo, cidade, dispositivo)."""
        i = bisect.bisect_right([e.t for e in legit], t)
        if i:
            e = legit[i - 1]
            return e.t, e.city, e.device
        u = self.users[ui]
        return u.last_t, (u.last_city or u.home_city), u.devices[0]

    def _night(self, t: float, lo: float, hi: float, p: float) -> float:
        """Com probabilidade p, move t para a madrugada (0-5h) do mesmo dia, se couber na janela."""
        if self.rng.random() >= p:
            return t
        t2 = math.floor(t / DAY) * DAY + float(self.rng.uniform(0, 5 * HOUR))
        return t2 if lo <= t2 < hi else t

    def _fraud_ev(self, t, ui, city, device, m, amount, pattern) -> _Ev:
        return _Ev(t, ui, city, device, m, round(min(max(amount, 1.0), 500_000.0), 2), True, pattern)

    def _fraud_velocity(self, ui, t, legit, lo, hi) -> list[_Ev]:
        rng, u = self.rng, self.users[ui]
        _, city, main_dev = self._anchor(ui, t, legit)
        device = self._hex_id("dev") if rng.random() < 0.6 else main_dev
        merchants = [self._risky_merchant() for _ in range(int(rng.integers(2, 4)))]
        testing = rng.random() < 0.3  # "card testing": valores minúsculos em rajada
        out = []
        for _ in range(int(rng.integers(4, 11))):
            t += float(rng.uniform(8, 60))
            amount = float(rng.uniform(1, 15)) if testing else self._amount(u.mu - 0.2, 0.5) * float(rng.uniform(1, 2))
            out.append(self._fraud_ev(min(t, hi - 1), ui, city, device, merchants[int(rng.integers(len(merchants)))], amount, "velocity"))
        return out

    def _fraud_amount_spike(self, ui, t, legit, lo, hi) -> list[_Ev]:
        rng, u = self.rng, self.users[ui]
        t = self._night(t, lo, hi, 0.4)
        _, city, main_dev = self._anchor(ui, t, legit)
        device = main_dev if rng.random() < 0.5 else self._hex_id("dev")
        out = []
        for _ in range(1 if rng.random() < 0.7 else 2):
            m = self._risky_merchant() if rng.random() < 0.7 else u.favorites[int(rng.integers(len(u.favorites)))]
            amount = math.exp(u.mu + 2 * u.sigma) * float(rng.uniform(1.5, 6.0))
            out.append(self._fraud_ev(min(t, hi - 1), ui, city, device, m, amount, "amount_spike"))
            t += float(rng.uniform(60, 900))
        return out

    def _fraud_new_device(self, ui, t, legit, lo, hi) -> list[_Ev]:
        rng, u = self.rng, self.users[ui]
        t = self._night(t, lo, hi, 0.4)
        _, city, _ = self._anchor(ui, t, legit)
        if rng.random() >= 0.6:
            other = [c for c in BR_CITIES if c != city]
            city = other[int(rng.integers(len(other)))]
        device = self._hex_id("dev")
        out = []
        for _ in range(int(rng.integers(1, 4))):
            out.append(self._fraud_ev(min(t, hi - 1), ui, city, device, self._risky_merchant(), self._amount(u.mu + 0.4, 0.6), "new_device"))
            t += float(rng.uniform(300, 2400))
        return out

    def _fraud_impossible_travel(self, ui, t, legit, lo, hi) -> list[_Ev]:
        rng, u = self.rng, self.users[ui]
        anchor_t, city, main_dev = self._anchor(ui, t, legit)
        far = [c for c in CITIES if city_distance_km(city, c) > 3000]
        dest = far[int(rng.integers(len(far)))]
        if anchor_t is not None:
            t = anchor_t + float(rng.uniform(600, 5400))  # 10-90 min depois: fisicamente impossível
        t = min(max(t, lo), hi - 1)
        device = main_dev if rng.random() < 0.5 else self._hex_id("dev")
        out = []
        for _ in range(1 if rng.random() < 0.6 else 2):
            out.append(self._fraud_ev(min(t, hi - 1), ui, dest, device, self._risky_merchant(), self._amount(u.mu + 0.3, 0.6), "impossible_travel"))
            t += float(rng.uniform(300, 1200))
        return out

    def _fraud_low_and_slow(self, ui, t, legit, lo, hi) -> list[_Ev]:
        rng, u = self.rng, self.users[ui]
        t = self._night(t, lo, hi, 0.5)
        _, city, main_dev = self._anchor(ui, t, legit)
        m = self._random_merchant()  # merchant fora dos favoritos, valor e local normais
        amount = self._amount(u.mu + CATEGORY_BY_NAME[_MERCHANT_CATS[m]].amount_shift, u.sigma) * float(rng.uniform(1.0, 1.6))
        return [self._fraud_ev(min(t, hi - 1), ui, city, main_dev, m, amount, "low_and_slow")]

    def _inject_fraud(self, legit_by_user: list[list[_Ev]], lo: float, hi: float, n_legit: int) -> list[_Ev]:
        rate = self.cfg.fraud_rate
        self._fraud_carry += rate / (1.0 - rate) * n_legit
        out: list[_Ev] = []
        span = max(hi - lo, 1.0)
        while self._fraud_carry >= 1.0:
            pattern = self._pattern_names[int(self.rng.choice(len(self._pattern_names), p=self._pattern_p))]
            ui = int(self.rng.integers(len(self.users)))
            # Deixa folga no fim da janela para o episódio (rajadas) caber nela.
            t = lo + float(self.rng.uniform(0, max(span - 900.0, 0.0)))
            evs = getattr(self, f"_fraud_{pattern}")(ui, t, legit_by_user[ui], lo, hi)
            out.extend(evs)
            self._fraud_carry -= len(evs)
        return out

    # ------------------------------------------------------------------ API
    def advance(self, days: int = 1) -> list[Transaction]:
        """Gera as transações dos próximos `days` dias, ordenadas por timestamp."""
        w0 = self._day0 + self._next_day * DAY
        w1 = w0 + days * DAY
        lo = max(w0, self._clock)  # a 1ª janela pode começar no meio do dia (start)
        legit_by_user: list[list[_Ev]] = []
        for ui, u in enumerate(self.users):
            evs = [e for e in self._legit_for_user(ui, u, w0, days) if e.t >= lo]
            legit_by_user.append(evs)
        n_legit = sum(len(e) for e in legit_by_user)
        events = [e for evs in legit_by_user for e in evs]
        events.extend(self._inject_fraud(legit_by_user, lo, w1, n_legit))
        events.sort(key=lambda e: e.t)  # estável: empates mantêm a ordem de geração
        for ui, evs in enumerate(legit_by_user):
            if evs:
                self.users[ui].last_t, self.users[ui].last_city = evs[-1].t, evs[-1].city
        self._clock = w1
        self._next_day += days
        return [self._to_txn(e) for e in events]

    def _to_txn(self, e: _Ev) -> Transaction:
        # model_construct: os eventos são gerados por este módulo (confiáveis) e a
        # validação do pydantic dominaria o custo de gerar centenas de milhares.
        city = CITIES[e.city]
        tid = f"{self.cfg.id_prefix}_{self._counter:08d}"
        self._counter += 1
        return Transaction.model_construct(
            transaction_id=tid,
            user_id=self.users[e.user].user_id,
            amount=e.amount,
            currency="BRL",
            country=city.country,
            city=e.city,
            device_id=e.device,
            merchant_id=_MERCHANT_IDS[e.merchant],
            merchant_category=_MERCHANT_CATS[e.merchant],
            timestamp=datetime.fromtimestamp(round(e.t, 3), tz=timezone.utc),
            is_fraud=e.fraud,
            fraud_pattern=e.pattern,
        )


def generate_dataset(days: int = 60, **config) -> list[Transaction]:
    """Atalho: dataset completo de `days` dias, ordenado por tempo."""
    return Simulator(SimConfig(**config)).advance(days)
