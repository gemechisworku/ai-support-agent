"""T-OBS-01 · OBS-R2: per-turn redaction latency (target p95 ≤ 50 ms on the M4; CI bound 200 ms)."""

import random
import statistics
import time

from supportagent.observability.redaction import redact, warmup

TEMPLATES = [
    "Hi, I'm {name}, my loan is {loan} and you can call me on {phone}.",
    "Your payment of ${amount} is due on November {day}. Would you like to pay today?",
    "I was born {dob} and my last four are {spoken}.",
    "Okay, I can pay ${amount} next Friday, the {day}th, from my checking account.",
    "Can you explain the late fee on my personal loan? I thought there was a grace period of ten days.",
    "Please send the confirmation to {email} and not to my old address.",
]
NAMES = ["Frank Miller", "Priya Raman", "Tomás Herrera", "Grace Okafor", "Lena Fischer"]
DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def _turns(n: int) -> list[str]:
    rng = random.Random(7)
    out = []
    for _ in range(n):
        out.append(
            rng.choice(TEMPLATES).format(
                name=rng.choice(NAMES),
                loan=f"55{rng.randrange(10**8):08d}",
                phone=f"555-010-{rng.randrange(10**4):04d}",
                amount=f"{rng.randrange(20, 2000)}.{rng.randrange(100):02d}",
                day=rng.randrange(1, 28),
                dob=f"March {rng.randrange(1, 28)} 19{rng.randrange(50, 99)}",
                spoken=" ".join(rng.choice(DIGITS) for _ in range(4)),
                email=f"user{rng.randrange(1000)}@example.com",
            )
        )
    return out


def test_redaction_p95_latency() -> None:
    warmup()
    timings = []
    for turn in _turns(200):
        start = time.perf_counter()
        redact(turn)
        timings.append((time.perf_counter() - start) * 1000)
    p95 = statistics.quantiles(timings, n=100)[94]
    print(f"\nredaction p95 = {p95:.1f} ms over 200 turns (target ≤ 50 ms on M4)")
    assert p95 <= 200
