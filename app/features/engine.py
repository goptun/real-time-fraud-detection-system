"""Máquina de estados de features — FONTE ÚNICA usada no treino e no streaming.

    features = compute_features(state, txn)   # leitura pura do state
    applied  = update_state(state, txn)       # só depois de persistir a transação

O treino reproduz o stream: percorre as transações em ordem temporal passando-as
por estas mesmas duas funções (`FeatureEngine.process`). Não existe uma segunda
implementação em pandas — a paridade treino/serving vale por construção.

Estado por usuário (`UserState`):
  * `log`: últimas transações (≤ 24 h e ≤ LOG_MAX), ordenadas por timestamp —
    alimenta janelas de frequência, "última transação" e o tratamento de eventos
    atrasados;
  * MEMÓRIA ROLANTE de MEMORY_DAYS (30) dias: dispositivos/cidades/países/merchants
    com o instante em que foram vistos pela última vez, e buckets diários
    (contagem, valor máximo). "Novo" = não visto nos últimos 30 dias;
    `txn_count_30d` e o máximo de valor também são da janela de 30 dias;
  * média/variância do log-valor de todo o histórico (Welford; estável).

Por que memória rolante e não acumulada: features baseadas em "tudo que o usuário já
fez" (contagem total, máximo histórico, merchants já vistos) deslocam-se sozinhas com o
tempo — medido em simulação: PSI de `user_txn_count` > 0.3 doze dias depois da
referência e a taxa de `is_new_merchant` caindo de 14.7% para 4.7% em um ano. Um replay
rodando por semanas geraria alerta falso de drift e degradaria o modelo. Com janela de
30 dias as features são estacionárias e o estado por usuário é limitado.

Sem histórico recente (primeira transação, ou usuário inativo há > 30 dias) os valores
padrão são: contagens 0, z-score 0, razão-para-o-máximo 1, seconds_since_last =
MAX_GAP_SECONDS, flags "novo" = 0 (ausência de histórico não é evidência de fraude;
`txn_count_30d`=0 informa o modelo do cold start), km/velocidade 0.

Eventos atrasados (timestamp < último já processado): as features são calculadas
só com o log anterior ao timestamp do evento (nunca usam informação do futuro).
Limitação documentada: para eles, "novo dispositivo/cidade/merchant" e as
estatísticas de valor enxergam apenas o log retido (≤ 24 h), não os 30 dias.
"""

from __future__ import annotations

import bisect
import math
from collections import OrderedDict
from typing import Callable, Iterable, NamedTuple

from app.data.catalog import CATEGORY_NAMES, RISKY_CATEGORIES
from app.data.geo import CITIES, city_distance_km
from app.data.schema import Transaction

WINDOWS: tuple[tuple[float, str], ...] = (
    (60.0, "txn_count_1m"),
    (600.0, "txn_count_10m"),
    (3600.0, "txn_count_1h"),
    (86400.0, "txn_count_24h"),
)
DAY_SECONDS = 86400.0
MEMORY_DAYS = 30
MEMORY_SECONDS = MEMORY_DAYS * DAY_SECONDS
LOG_HORIZON_SECONDS = 86400.0
LOG_MAX = 64
MAX_GAP_SECONDS = 7 * 86400.0
MAX_SPEED_KMH = 5000.0
MIN_DT_HOURS = 1.0 / 60.0  # evita velocidade infinita para eventos quase simultâneos
MIN_STD_LN = 0.3
Z_CLIP = 10.0
RATIO_CLIP = 50.0

FEATURE_NAMES: tuple[str, ...] = (
    "amount",
    "amount_zscore_user",
    "amount_ratio_to_user_max",
    "txn_count_1m",
    "txn_count_10m",
    "txn_count_1h",
    "txn_count_24h",
    "seconds_since_last",
    "is_new_device",
    "is_new_city",
    "is_new_country",
    "is_new_merchant",
    "km_from_last",
    "implied_speed_kmh",
    "merchant_category_code",
    "is_risky_category",
    "hour_of_day",
    "txn_count_30d",
)
N_FEATURES = len(FEATURE_NAMES)
_IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}
_CATEGORY_CODE = {c: i for i, c in enumerate(CATEGORY_NAMES)}
_RISKY = frozenset(RISKY_CATEGORIES)


class _Entry(NamedTuple):
    ts: float
    city: str
    device: str
    merchant: str
    amount: float
    txn_id: str


class UserState:
    __slots__ = ("log", "devices", "cities", "countries", "merchants", "buckets", "n", "mean_ln", "m2_ln", "last_ts")

    def __init__(self) -> None:
        self.log: list[_Entry] = []
        # entidade -> instante da última vez vista (memória rolante de MEMORY_DAYS)
        self.devices: dict[str, float] = {}
        self.cities: dict[str, float] = {}
        self.countries: dict[str, float] = {}
        self.merchants: dict[str, float] = {}
        # dia (epoch // 86400) -> [contagem, valor máximo]
        self.buckets: dict[int, list[float]] = {}
        self.n = 0  # transações de todo o histórico (só para o z-score)
        self.mean_ln = 0.0
        self.m2_ln = 0.0
        self.last_ts: float | None = None

    @classmethod
    def from_transactions(cls, txns: Iterable[Transaction]) -> "UserState":
        """Reconstrói o state reaplicando o histórico do usuário em ordem de tempo."""
        state = cls()
        for t in sorted(txns, key=lambda t: t.epoch):
            update_state(state, t)
        return state


def _entry(txn: Transaction) -> _Entry:
    return _Entry(txn.epoch, txn.city, txn.device_id, txn.merchant_id, txn.amount, txn.transaction_id)


def _seen(d: dict[str, float], key: str, ts: float) -> None:
    if ts > d.get(key, -math.inf):
        d[key] = ts


def _prune(state: UserState) -> None:
    """Descarta o que saiu da janela de memória (chamado quando um novo dia começa)."""
    newest = max(state.buckets)
    for day in [d for d in state.buckets if d <= newest - MEMORY_DAYS]:
        del state.buckets[day]
    cutoff = state.last_ts - MEMORY_SECONDS if state.last_ts is not None else -math.inf
    for seen in (state.devices, state.cities, state.countries, state.merchants):
        for key in [k for k, ts in seen.items() if ts < cutoff]:
            del seen[key]


def _apply(state: UserState, e: _Entry) -> None:
    """Aplica uma entrada (sem checagem de duplicata) — usado por update e replay."""
    log = state.log
    if not log or e.ts >= log[-1].ts:
        log.append(e)
    else:  # evento atrasado: insere na posição cronológica
        log.insert(bisect.bisect_right([x.ts for x in log], e.ts), e)
    newest = log[-1].ts
    while len(log) > 1 and (log[0].ts < newest - LOG_HORIZON_SECONDS or len(log) > LOG_MAX):
        log.pop(0)

    if state.last_ts is None or e.ts > state.last_ts:
        state.last_ts = e.ts
    _seen(state.devices, e.device, e.ts)
    _seen(state.cities, e.city, e.ts)
    _seen(state.countries, CITIES[e.city].country, e.ts)
    _seen(state.merchants, e.merchant, e.ts)
    day = int(e.ts // DAY_SECONDS)
    bucket = state.buckets.get(day)
    if bucket is None:
        state.buckets[day] = [1.0, e.amount]
        _prune(state)
    else:
        bucket[0] += 1.0
        bucket[1] = max(bucket[1], e.amount)

    state.n += 1
    x = math.log(e.amount)
    delta = x - state.mean_ln
    state.mean_ln += delta / state.n
    state.m2_ln += delta * (x - state.mean_ln)


def update_state(state: UserState, txn: Transaction) -> bool:
    """Incorpora a transação ao state. Retorna False (state intacto) se ela já
    foi aplicada — reentrega da mesma transaction_id não conta duas vezes."""
    if any(e.txn_id == txn.transaction_id for e in state.log):
        return False
    _apply(state, _entry(txn))
    return True


def _as_of(state: UserState, t: float) -> UserState:
    """State reconstruído só com o log anterior (≤ t): para eventos atrasados."""
    prior = UserState()
    for e in state.log:
        if e.ts <= t:
            _apply(prior, e)
    return prior


def compute_features(state: UserState, txn: Transaction) -> list[float]:
    """Vetor de features (ordem = FEATURE_NAMES) da transação, dado o histórico
    do usuário. Função pura: não altera `state`."""
    t = txn.epoch
    if state.last_ts is not None and t < state.last_ts:
        state = _as_of(state, t)  # evento atrasado: sem vazamento do futuro

    log = state.log
    counts = [0, 0, 0, 0]
    for e in reversed(log):
        age = t - e.ts
        if age > WINDOWS[-1][0]:
            break
        for k, (w, _) in enumerate(WINDOWS):
            if age <= w:
                counts[k] += 1

    seconds_since_last = MAX_GAP_SECONDS
    km = speed = 0.0
    if log:
        last = log[-1]
        dt = max(t - last.ts, 0.0)
        seconds_since_last = min(dt, MAX_GAP_SECONDS)
        km = city_distance_km(last.city, txn.city)
        speed = min(km / max(dt / 3600.0, MIN_DT_HOURS), MAX_SPEED_KMH)

    # Janela de memória: buckets diários com dia > dia(t) − MEMORY_DAYS (≤ 30 dias)
    lo_day = int(t // DAY_SECONDS) - MEMORY_DAYS
    recent = [b for day, b in state.buckets.items() if day > lo_day]
    count_30d = sum(b[0] for b in recent)
    max_recent = max((b[1] for b in recent), default=0.0)
    has_history = count_30d > 0
    horizon = t - MEMORY_SECONDS

    def is_new(seen: dict[str, float], key: str) -> float:
        return float(has_history and seen.get(key, -math.inf) < horizon)

    z = 0.0
    if state.n >= 3:
        std = math.sqrt(state.m2_ln / (state.n - 1))
        z = max(-Z_CLIP, min(Z_CLIP, (math.log(txn.amount) - state.mean_ln) / max(std, MIN_STD_LN)))
    ratio = min(txn.amount / max_recent, RATIO_CLIP) if has_history else 1.0

    return [
        txn.amount,
        z,
        ratio,
        float(counts[0]),
        float(counts[1]),
        float(counts[2]),
        float(counts[3]),
        seconds_since_last,
        is_new(state.devices, txn.device_id),
        is_new(state.cities, txn.city),
        is_new(state.countries, txn.country),
        is_new(state.merchants, txn.merchant_id),
        km,
        speed,
        float(_CATEGORY_CODE[txn.merchant_category]),
        float(txn.merchant_category in _RISKY),
        float(txn.timestamp.hour),
        float(count_30d),
    ]


def features_as_dict(vector: list[float]) -> dict[str, float]:
    return dict(zip(FEATURE_NAMES, vector))


def feature_index(name: str) -> int:
    return _IDX[name]


class UserStateStore:
    """Cache LRU de `UserState` por usuário. Em cache miss (boot, evicção) o
    state é reconstruído por `loader(user_id)` — no serving, o PostgreSQL é a
    fonte de verdade; sem loader, o usuário começa sem histórico."""

    def __init__(self, capacity: int = 20_000, loader: Callable[[str], Iterable[Transaction]] | None = None):
        self.capacity = capacity
        self.loader = loader
        self._states: OrderedDict[str, UserState] = OrderedDict()
        self.loads = 0  # quantas reconstruções via loader (observabilidade/testes)

    def __len__(self) -> int:
        return len(self._states)

    def get(self, user_id: str) -> UserState:
        state = self._states.get(user_id)
        if state is not None:
            self._states.move_to_end(user_id)
            return state
        if self.loader is not None:
            state = UserState.from_transactions(self.loader(user_id))
            self.loads += 1
        else:
            state = UserState()
        self._states[user_id] = state
        while len(self._states) > self.capacity:
            self._states.popitem(last=False)
        return state

    def peek(self, user_id: str) -> UserState | None:
        return self._states.get(user_id)


class FeatureEngine:
    """Uso offline/contínuo: `process` = compute + update sobre um store."""

    def __init__(self, store: UserStateStore | None = None):
        self.store = store or UserStateStore(capacity=10**9)

    def process(self, txn: Transaction) -> list[float]:
        state = self.store.get(txn.user_id)
        vector = compute_features(state, txn)
        update_state(state, txn)
        return vector
