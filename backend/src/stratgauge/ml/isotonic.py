"""Small dependency-free isotonic regression primitives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class IsotonicBin:
    """One monotonic probability interval fitted by PAVA."""

    lower_probability: Decimal
    upper_probability: Decimal
    calibrated_probability: Decimal
    sample_count: int
    positive_count: int


def fit_isotonic_bins(
    probabilities: Sequence[Decimal],
    positives: Sequence[bool],
) -> tuple[IsotonicBin, ...]:
    """Fit a monotonic binary probability map, grouping equal inputs first."""

    if not probabilities or len(probabilities) != len(positives):
        raise ValueError("isotonic regression requires equally sized non-empty inputs")
    grouped: dict[Decimal, list[int]] = {}
    for probability, positive in zip(probabilities, positives, strict=True):
        _validate_probability(probability)
        counts = grouped.setdefault(probability, [0, 0])
        counts[0] += 1
        counts[1] += int(positive)

    blocks: list[_IsotonicBlock] = []
    for probability, counts in sorted(grouped.items()):
        blocks.append(
            _IsotonicBlock(
                lower_probability=probability,
                upper_probability=probability,
                positive_count=counts[1],
                sample_count=counts[0],
            )
        )
        while len(blocks) >= 2 and (
            blocks[-2].calibrated_probability > blocks[-1].calibrated_probability
        ):
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                _IsotonicBlock(
                    lower_probability=left.lower_probability,
                    upper_probability=right.upper_probability,
                    positive_count=left.positive_count + right.positive_count,
                    sample_count=left.sample_count + right.sample_count,
                )
            )
    return tuple(
        IsotonicBin(
            lower_probability=block.lower_probability,
            upper_probability=block.upper_probability,
            calibrated_probability=block.calibrated_probability,
            sample_count=block.sample_count,
            positive_count=block.positive_count,
        )
        for block in blocks
    )


def apply_isotonic_bin(probability: Decimal, bins: Sequence[IsotonicBin]) -> Decimal:
    """Apply a fitted monotonic map to one bounded probability."""

    _validate_probability(probability)
    if not bins:
        raise ValueError("isotonic regression has no fitted bins")
    return next(
        (item.calibrated_probability for item in bins if probability <= item.upper_probability),
        bins[-1].calibrated_probability,
    )


@dataclass
class _IsotonicBlock:
    lower_probability: Decimal
    upper_probability: Decimal
    positive_count: int
    sample_count: int

    @property
    def calibrated_probability(self) -> Decimal:
        return Decimal(self.positive_count) / Decimal(self.sample_count)


def _validate_probability(probability: Decimal) -> None:
    if not probability.is_finite() or not 0 <= probability <= 1:
        raise ValueError("isotonic probabilities must be finite and bounded")
